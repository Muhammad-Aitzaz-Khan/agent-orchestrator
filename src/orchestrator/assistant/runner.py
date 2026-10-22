"""Running one job: gather the material, let the team work, save the result, notify.

Daily allowances keep a shared free AI key from being used up by one person or one routine:
ASSISTANT_DAILY_TOKENS for the whole site and ASSISTANT_USER_DAILY_TOKENS per person.
"""
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from orchestrator.assistant import gather as gathering
from orchestrator.assistant import push
from orchestrator.assistant.skills import SKILLS
from orchestrator.assistant.store import Store, now
from orchestrator.orchestrator import run_orchestrator

JOB_TOKEN_BUDGET = 70_000


def _limit(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def allowance_left(store: Store, user_id: str) -> bool:
    return (store.usage_today() < _limit("ASSISTANT_DAILY_TOKENS", 180_000)
            and store.usage_today(user_id) < _limit("ASSISTANT_USER_DAILY_TOKENS", 90_000))


class LiveEvents:
    """Events of running jobs, for people watching the agents work."""

    def __init__(self):
        self._events: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def start(self, job_id: str) -> None:
        with self._lock:
            self._events[job_id] = []

    def add(self, job_id: str, event: dict) -> None:
        with self._lock:
            self._events.setdefault(job_id, []).append(event)

    def get(self, job_id: str) -> list[dict] | None:
        with self._lock:
            events = self._events.get(job_id)
            return list(events) if events is not None else None

    def finish(self, job_id: str) -> None:
        with self._lock:
            self._events.pop(job_id, None)


def task_text(job: dict, gathered: gathering.Gathered, user: dict) -> str:
    prefs = user.get("prefs") or {}
    lines = [f"Request from {user.get('name') or 'the person'}: {job['instructions'] or job['title']}"]
    if prefs.get("about"):
        lines.append(f"About them: {prefs['about']}")
    if gathered.files:
        lines.append("Material gathered for you in inputs/: " + ", ".join(sorted(gathered.files)) + ".")
    else:
        lines.append("There is no gathered material for this job.")
    if gathered.notes:
        lines.append("Notes: " + " ".join(gathered.notes) + " Mention these briefly at the end.")
    return "\n".join(lines)


def _summary(result, files: dict[str, str]) -> str:
    review = next((r.review for r in reversed(result.rounds) if r.review), None)
    if review and review.verdict == "approve" and review.message:
        return review.message
    text = next(iter(files.values()), "")
    for line in text.splitlines():
        line = line.strip("# ").strip()
        if line:
            return line[:240]
    return "Your result is ready."


def run_job(store: Store, job: dict, client=None, models: dict | None = None,
            live: LiveEvents | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Run a queued job to the end and return the updated job."""
    user = store.user(job["user_id"])
    skill = SKILLS.get(job["skill"])
    store.update_job(job["id"], status="running")

    def finish(status: str, summary: str = "", notify: bool = True, **values) -> dict:
        store.update_job(job["id"], status=status, summary=summary, finished_at=now(),
                         unread=notify, **values)
        if notify and status in ("done", "failed"):
            push.send(store, job["user_id"], job["title"] if status == "done" else f"{job['title']}: problem",
                      summary, url=f"/?job={job['id']}", tag=job["id"])
        return store.job_by_id(job["id"])

    if user is None or skill is None:
        return finish("failed", "This job could not run.", notify=False, error="unknown user or skill")

    if skill.key == "reminder":
        return finish("done", job["params"].get("text") or job["title"])

    routine = store.routine(user["id"], job["routine_id"]) if job.get("routine_id") else None
    gathered = gathering.gather(skill.key, job["params"] or {}, user, store,
                                (routine or {}).get("state") or {})
    if routine and gathered.state:
        store.update_routine(routine["id"], state=gathered.state)

    if skill.quiet_when_nothing_new and gathered.nothing_new:
        return finish("quiet", "Nothing new.", notify=False)
    if not skill.uses_ai:
        return finish("done", gathered.summary or "Done.")

    if not allowance_left(store, user["id"]):
        return finish("skipped", "Today's AI allowance is used up, so this was skipped. "
                                 "It will run again next time.")

    workspace = Path(tempfile.mkdtemp(prefix="relay_job_"))
    events: list[dict] = []
    if live:
        live.start(job["id"])

    def on_update(event: dict) -> None:
        events.append(event)
        if live:
            live.add(job["id"], event)

    try:
        inputs = workspace / "inputs"
        inputs.mkdir()
        for name, text in gathered.files.items():
            (inputs / name).write_text(text, encoding="utf-8")
        result = run_orchestrator(task_text(job, gathered, user), workspace, client=client,
                                  models=models, kind="assist", on_update=on_update, sleep=sleep,
                                  token_budget=JOB_TOKEN_BUDGET, runs_dir=workspace.parent / f"{workspace.name}_traces")
        store.add_usage(user["id"], result.total_tokens)
        files = {k: v for k, v in result.files.items() if not k.startswith("inputs/")}
        if result.status == "error" and "DailyLimitReached" in (result.error or ""):
            return finish("skipped", "The AI's free allowance for today ran out, so this was skipped. "
                                     "It resets at midnight (UTC).", error=result.error[:1000],
                          events=events, tokens=result.total_tokens)
        if result.status == "error" or not files:
            reason = result.error or "the team did not produce a result"
            return finish("failed", "Something went wrong, so this did not finish. It will try "
                                    "again next time." if routine else "Something went wrong. Try again in a minute.",
                          error=reason[:1000], events=events, tokens=result.total_tokens)
        return finish("done", _summary(result, files), files=files, events=events,
                      tokens=result.total_tokens)
    except Exception as e:  # noqa: BLE001 - one broken job must not stop the worker
        return finish("failed", "Something went wrong. Try again in a minute.",
                      error=f"{type(e).__name__}: {e}"[:1000], events=events)
    finally:
        if live:
            live.finish(job["id"])
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(workspace.parent / f"{workspace.name}_traces", ignore_errors=True)

"""The web app: start a team run, watch the agents work live, or replay a recorded run.

Routes
    GET  /                          the office page (static/relay.html) with live runs on
    GET  /api/status                whether an API key is configured, model, sandbox
    GET  /api/examples              sample requests and sample projects to fix
    GET  /api/replays               recorded runs that can be replayed without an API key
    POST /api/runs                  start a live run {prompt, kind, sample?} -> {id}
    POST /api/replays/{name}        start a replay -> {id}
    GET  /api/runs/{id}/events      the run's events as a Server-Sent Events stream

Safety: the server is meant to run on your own computer (it listens on 127.0.0.1 by
default), allows one live run at a time, limits the request length, and every run has the
orchestrator's token budget. Every live run's events are saved to runs/web/<id>/events.jsonl,
so a good run can be copied into web/replays/ and replayed later.

Public mode (RELAY_PUBLIC=1, for a demo anyone can use, e.g. on Hugging Face Spaces):
    - visitors never see or enter a key: the server's key is read once at start-up and
      removed from the environment, and the process is made non-dumpable, so code the
      agents run cannot read it from /proc
    - model-written code runs with SANDBOX=guarded (see orchestrator/guard)
    - a daily token allowance for the whole site (RELAY_DAILY_TOKENS) and a number of jobs
      per visitor per day (RELAY_JOBS_PER_VISITOR); a smaller token budget per job
    - nothing a visitor types is written to disk after their job ends
Use a key from a provider account with no payment method, so the site can never cost money.
"""
import asyncio
import ctypes
import datetime
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from orchestrator.agent import default_model, make_client
from orchestrator.orchestrator import run_orchestrator
from orchestrator.tools import sandbox_mode
from orchestrator.web.site import load_recordings, render_page

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
REPLAYS_DIR = WEB_DIR / "replays"
PROJECT_ROOT = WEB_DIR.parents[2]  # the repository root (src/orchestrator/web -> repo)
TASKS_DIR = PROJECT_ROOT / "evals" / "tasks"
MAX_PROMPT_CHARS = 8000
REPLAY_MAX_PAUSE_S = 1.6  # long pauses in a recording are shortened when replayed
SECRET_VARS = ("LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
PUBLIC_JOB_TOKENS = 60_000  # token budget for one visitor's job in public mode

LANDING_PAGE_PROMPT = """Generate a ONE-PAGE LANDING PAGE creation prompt using a RANDOMLY SELECTED design style from this list, or a more suitable professional style of your own: Neobrutalist, Swiss/International, Editorial, Glassmorphism, Retro-futuristic, Bauhaus, Art Deco, Minimal, Flat, Material, Neumorphic, Monochromatic, Scandinavian, Japandi, Dark Mode First, Modernist, Organic/Fluid, Corporate Professional, Tech Forward, Luxury Minimal, Neo-Geo, Kinetic, Gradient Modern, Typography First, Metropolitan. Actually randomize the selection; do not default to a favourite.

The prompt must be EXACTLY THREE PARAGRAPHS.
Paragraph 1: state the chosen style and ask the AI to conceive an innovative business or service concept for a single-page landing page. Describe the emotional qualities the style evokes, how the visual hierarchy and flow should make visitors feel as they scroll, and ask for colorful elements where they enhance the emotional impact.
Paragraph 2: explain the design philosophy through emotion and user experience: how the typography should feel, what sensation interactions and animations should create, and how the single-page journey should progress emotionally from first impression to the final call to action.
Paragraph 3: give abstract reference points that capture the aesthetic (kinds of spaces, cultural movements, artistic periods, architectural styles, design philosophies, qualities of premium experiences) and explain how they should shape the final design, without naming specific brands or platforms.

Emphasize that it is ONE cohesive landing page with a single scrolling experience. Focus on feeling, atmosphere, and abstract references rather than technical details. Save the prompt as output.md."""

CREATE_EXAMPLES = [
    {
        "title": "Landing page prompt",
        "prompt": LANDING_PAGE_PROMPT,
    },
    {
        "title": "Study plan",
        "prompt": "Make a two-week study plan for learning the basics of linear algebra "
        "(vectors, matrices, systems of equations, determinants) for a first-year computer "
        "science student with one hour a day. Use a table with one row per day, and end with "
        "a short list of free resources. Save it as output.md.",
    },
    {
        "title": "Small web page",
        "prompt": "Build a single self-contained web page (index.html) for a neighbourhood "
        "bakery called Crumb & Co.: a hero section, today's three specials with prices in "
        "Pakistani rupees, opening hours, and a contact section. It must look good on phones.",
    },
]


def fix_samples() -> list[dict]:
    samples = []
    for task_dir in sorted(TASKS_DIR.glob("*")) if TASKS_DIR.is_dir() else []:
        meta = task_dir / "task.toml"
        if not meta.is_file():
            continue
        text = meta.read_text(encoding="utf-8")
        prompt = re.search(r'^prompt = "(.*)"$', text, re.MULTILINE)
        difficulty = re.search(r'^difficulty = "(.*)"$', text, re.MULTILINE)
        samples.append({
            "name": task_dir.name,
            "difficulty": difficulty.group(1) if difficulty else "",
            "prompt": prompt.group(1).replace('\\"', '"') if prompt else "",
            "files": sorted(p.name for p in (task_dir / "repo").glob("*.py")),
        })
    return samples


# ---------- runs and their event logs ----------


@dataclass
class WebRun:
    id: str
    kind: str
    replay: bool = False
    events: list[dict] = field(default_factory=list)
    done: bool = False
    log_path: Path | None = None

    def publish(self, event: dict) -> None:
        self.events.append(event)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        if event.get("type") == "run_finished":
            self.done = True


class StartRun(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    kind: str = Field(default="create", pattern="^(create|fix)$")
    sample: str | None = None  # fix mode: which sample project to fix


Runner = Callable[..., object]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Allowance:
    """Public mode: how much of today's free allowance is left, for the site and per visitor."""

    daily_tokens: int
    jobs_per_visitor: int
    day: str = ""
    tokens_used: int = 0
    provider_exhausted: bool = False
    jobs: dict[str, int] = field(default_factory=dict)

    def _roll(self) -> None:
        today = datetime.datetime.now(datetime.UTC).date().isoformat()
        if today != self.day:
            self.day, self.tokens_used, self.provider_exhausted, self.jobs = today, 0, False, {}

    def site_open(self) -> bool:
        self._roll()
        return not self.provider_exhausted and self.tokens_used < self.daily_tokens

    def visitor_jobs_left(self, visitor: str) -> int:
        self._roll()
        return max(0, self.jobs_per_visitor - self.jobs.get(visitor, 0))

    def start(self, visitor: str) -> None:
        self._roll()
        self.jobs[visitor] = self.jobs.get(visitor, 0) + 1

    def finish(self, tokens: int, daily_limit_hit: bool) -> None:
        self._roll()
        self.tokens_used += tokens
        self.provider_exhausted = self.provider_exhausted or daily_limit_hit


def _protect_process_memory() -> None:
    """Make this process non-dumpable so other processes of the same user cannot read
    /proc/<pid>/environ or its memory (Linux only; a no-op elsewhere)."""
    try:
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except (OSError, AttributeError, TypeError):
        pass


def _visitor(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def create_app(
    runner: Runner = run_orchestrator,
    runs_dir: Path = Path("runs") / "web",
    replays_dir: Path = REPLAYS_DIR,
    replay_speed: float = 1.0,
    public: bool | None = None,
    client_factory: Callable[[], object] = make_client,
) -> FastAPI:
    """Build the app. Tests pass a runner backed by a fake model client."""
    load_dotenv()
    if public is None:
        public = os.getenv("RELAY_PUBLIC", "").strip().lower() in ("1", "true", "yes")
    app = FastAPI(title="Relay", docs_url=None, redoc_url=None)
    runs: dict[str, WebRun] = {}
    lock = threading.Lock()
    allowance = Allowance(_env_int("RELAY_DAILY_TOKENS", 180_000),
                          _env_int("RELAY_JOBS_PER_VISITOR", 3))
    public_client = None
    if public:
        runs_dir = Path(tempfile.mkdtemp(prefix="relay_jobs_"))  # outside the app's folder
        os.environ["SANDBOX"] = "guarded"
        os.environ["RELAY_GUARD_DENY"] = str(PROJECT_ROOT)
        if any(os.getenv(name) for name in SECRET_VARS):
            public_client = client_factory()
        for name in SECRET_VARS:
            os.environ.pop(name, None)
        _protect_process_memory()

    def active_live_run() -> WebRun | None:
        return next((r for r in runs.values() if not r.done and not r.replay), None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return render_page(live=True, recordings=load_recordings(replays_dir))

    @app.get("/api/status")
    def status(request: Request) -> dict:
        if public:
            has_key = public_client is not None
        else:
            has_key = bool(os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY"))
        active = active_live_run()
        return {
            "api_key_configured": has_key,
            "public": public,
            "open_today": allowance.site_open() if public else True,
            "jobs_left": allowance.visitor_jobs_left(_visitor(request)) if public else None,
            "model": default_model(),
            "sandbox": sandbox_mode(),
            "active_run": active.id if active else None,
        }

    @app.get("/api/examples")
    def examples() -> dict:
        return {"create": CREATE_EXAMPLES, "fix": fix_samples()}

    @app.get("/api/replays")
    def replays() -> list[dict]:
        found = []
        for path in sorted(replays_dir.glob("*.jsonl")):
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            found.append({
                "name": path.stem,
                "task": first.get("task", ""),
                "kind": first.get("kind", "fix"),
                "title": first.get("title", path.stem.replace("_", " ")),
            })
        return found

    @app.post("/api/runs")
    def start_run(body: StartRun, request: Request) -> dict:
        visitor = _visitor(request)
        with lock:
            if public:
                if public_client is None:
                    raise HTTPException(503, "This site has no AI key set up yet.")
                if not allowance.site_open():
                    raise HTTPException(429, "The team has used up today's free allowance. "
                                             "Come back tomorrow, or watch a recorded job.")
                if allowance.visitor_jobs_left(visitor) <= 0:
                    raise HTTPException(429, "You have used today's jobs. Come back tomorrow, "
                                             "or watch a recorded job.")
            if active_live_run():
                raise HTTPException(409, "The team is busy with someone else's job.")
            run_id = uuid.uuid4().hex[:10]
            folder = runs_dir / run_id
            workspace = folder / "workspace"
            if body.kind == "fix":
                sample = next((s for s in fix_samples() if s["name"] == body.sample), None)
                if sample is None:
                    raise HTTPException(400, "Choose one of the sample projects to fix.")
                shutil.copytree(TASKS_DIR / sample["name"] / "repo", workspace)
            else:
                workspace.mkdir(parents=True)
            run = WebRun(id=run_id, kind=body.kind,
                         log_path=None if public else folder / "events.jsonl")
            runs[run_id] = run
            if public:
                allowance.start(visitor)

        def work() -> None:
            extra = {"client": public_client, "token_budget": PUBLIC_JOB_TOKENS} if public else {}
            try:
                runner(body.prompt, workspace, kind=body.kind, on_update=run.publish,
                       runs_dir=folder / "traces", **extra)
            except Exception as e:  # noqa: BLE001 - e.g. no API key; show it in the page
                run.publish({"type": "run_finished", "status": "error",
                             "error": f"{type(e).__name__}: {e}", "time": time.time(),
                             "files": {}, "diff": "", "total_tokens": 0, "rounds": 0})
            finally:
                run.done = True
                if public:
                    end = next((e for e in reversed(run.events) if e.get("type") == "run_finished"), {})
                    allowance.finish(int(end.get("total_tokens") or 0),
                                     "DailyLimitReached" in str(end.get("error") or ""))
                    shutil.rmtree(folder, ignore_errors=True)

        threading.Thread(target=work, daemon=True).start()
        return {"id": run_id}

    @app.post("/api/replays/{name}")
    def start_replay(name: str) -> dict:
        path = replays_dir / f"{name}.jsonl"
        if not re.fullmatch(r"[\w-]+", name) or not path.is_file():
            raise HTTPException(404, "No recording with that name.")
        recorded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        run = WebRun(id=uuid.uuid4().hex[:10], kind=recorded[0].get("kind", "fix"), replay=True)
        runs[run.id] = run

        def play() -> None:
            previous = None
            for event in recorded:
                t = event.get("time")
                if previous is not None and t is not None:
                    time.sleep(min(max(t - previous, 0), REPLAY_MAX_PAUSE_S) / replay_speed)
                previous = t if t is not None else previous
                run.publish({**event, "replay": True})
            run.done = True

        threading.Thread(target=play, daemon=True).start()
        return {"id": run.id}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str) -> StreamingResponse:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "No run with that id.")

        async def stream():
            sent = 0
            while True:
                while sent < len(run.events):
                    yield f"data: {json.dumps(run.events[sent], default=str)}\n\n"
                    sent += 1
                if run.done and sent >= len(run.events):
                    yield "event: end\ndata: {}\n\n"
                    return
                await asyncio.sleep(0.15)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app

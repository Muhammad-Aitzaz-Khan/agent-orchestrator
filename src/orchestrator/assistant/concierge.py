"""The Concierge: the agent at the front desk.

It reads what the person asked and proposes exactly one thing: a job to run now, a routine
that repeats, a reminder, or one short question when something essential is missing. Nothing
is scheduled until the person confirms the proposal (the "review" pattern), and every routine
stores a self-contained instruction, because a scheduled run has no memory of this chat.
"""
import datetime as dt
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from orchestrator.agent import run_agent
from orchestrator.assistant import schedule as sched
from orchestrator.assistant.skills import SKILLS, catalogue_text
from orchestrator.roles import Role


class ScheduleSpec(BaseModel):
    time: str = Field(default="", description="24-hour local time HH:MM for daily or weekly routines")
    days: list[int] = Field(default_factory=list,
                            description="0=Monday ... 6=Sunday; empty means every day")
    every_hours: int | None = Field(default=None, description="for checks every N hours instead of a time")
    once: str = Field(default="", description="local 'YYYY-MM-DD HH:MM' for something that happens one time")


class Proposal(BaseModel):
    action: Literal["job", "routine", "reminder", "question"] = Field(
        description="job = do it now; routine = repeat on a schedule; reminder = notify at a time; "
                    "question = ask one short question because something essential is missing")
    skill: str = Field(description="one skill key from the catalogue")
    title: str = Field(default="", description="short title, at most 6 words, e.g. 'Morning tech news'")
    instructions: str = Field(
        default="",
        description="complete, self-contained instructions for the team that will do the work, "
                    "including the person's preferences, format, and length. Empty for reminders.")
    topics: list[str] = Field(default_factory=list)
    region: str = ""
    city: str = ""
    days: int | None = None
    url: str = ""
    focus: str = ""
    topic: str = ""
    urls: list[str] = Field(default_factory=list)
    goals: str = ""
    text: str = Field(default="", description="for reminders: the reminder in the person's words")
    schedule: ScheduleSpec | None = None
    question: str = Field(default="", description="when action is question: the one question")
    message: str = Field(description="one or two warm, plain sentences to the person saying what "
                                     "the team will do once they confirm (or the question)")


CONCIERGE_PROMPT = """You are the CONCIERGE of Relay, a personal assistant run by a small team of AI
agents. You sit at the front desk: you never do the work yourself. You read what the person
asks and propose exactly one thing by calling submit_proposal.

Choose the action:
- "job": they want something done now (a summary, a report, writing, help with a file).
- "routine": they want it to repeat ("every morning", "each Sunday", "every 6 hours").
- "reminder": they want to be reminded of something (skill "reminder", no AI work).
- "question": only when something essential is missing and cannot be guessed sensibly
  (for example the city for weather). Ask one short question. Prefer sensible defaults.

Rules:
- Pick the single best skill from the catalogue. Use "answer" for general writing,
  explaining, planning, or questions that need no outside material.
- Turn times into 24-hour local time: "morning" means 07:00, "evening" 20:00, "night" 22:00.
  Days are numbers, 0 = Monday. For a one-time time use schedule.once in local time.
- instructions must stand on their own: a teammate reading only them must know exactly what
  to produce, for whom, in what format and length, and with which preferences.
- Fill params with the skill's settings from what they said (the catalogue lists them). For
  news and morning_brief, topics must name the subjects they mentioned: "tech news" means
  ["technology"], "cricket and Pakistan" means ["cricket", "Pakistan"]. Leave topics empty only
  when they named no subject at all.
- Never promise to send emails or messages for the person: Relay only reads and drafts.
- If a skill needs a connection the person has not made, still propose it; the app will ask
  them to connect.
- message speaks to the person directly, warmly and briefly. No technical words. Nothing has
  happened yet when you write it (they still have to confirm), so say what the team WILL do,
  e.g. "I'll have the team pull together today's top five tech stories." Never "I've prepared"."""

CONCIERGE = Role(
    name="concierge",
    title="Concierge",
    system_prompt=CONCIERGE_PROMPT,
    tools=(),
    output_model=Proposal,
    submit_tool="submit_proposal",
    submit_description="Propose exactly one job, routine, reminder, or question.",
    max_steps=3,
)


class ProposalError(ValueError):
    pass


def concierge_task(message: str, user: dict, connected: dict[str, str], files: list[dict],
                   attached: list[str], history: list[dict], now: dt.datetime) -> str:
    tz = user.get("tz") or "UTC"
    local = now.astimezone(sched.zone(tz))
    prefs = user.get("prefs") or {}
    lines = [
        f"Now: {local.strftime('%A %Y-%m-%d %H:%M')} ({tz}).",
        f"Person: {user.get('name') or 'the user'}.",
        f"Preferences: {prefs or 'none saved'}.",
        "Connected: " + (", ".join(f"{k} ({v})" for k, v in connected.items()) or "nothing yet") + ".",
    ]
    if files:
        lines.append("Uploaded files: " + "; ".join(f"{f['name']} (id {f['id']})" for f in files[:20]) + ".")
    if attached:
        lines.append("Attached to this message: " + ", ".join(attached) + " (use skill file_task "
                     "unless another skill fits, and they will be passed along automatically).")
    lines += ["", "Skills:", catalogue_text(), ""]
    for turn in history[-6:]:
        lines.append(f"{'Person' if turn.get('role') == 'user' else 'Concierge'}: {turn.get('text', '')[:800]}")
    lines.append(f"Person: {message}")
    return "\n".join(lines)


def normalise(raw: dict, attached: list[str]) -> dict:
    """Check a proposal and turn it into what the app stores. Raises ProposalError."""
    p = Proposal.model_validate(raw)
    if p.skill not in SKILLS:
        raise ProposalError(f"unknown skill {p.skill!r}")
    skill = SKILLS[p.skill]
    if p.action == "reminder" and p.skill != "reminder":
        p.skill, skill = "reminder", SKILLS["reminder"]
    params = {k: v for k, v in {
        "topics": p.topics, "region": p.region, "city": p.city, "days": p.days, "url": p.url,
        "focus": p.focus, "topic": p.topic, "urls": p.urls, "goals": p.goals, "text": p.text,
    }.items() if v not in (None, "", [])}
    if attached:
        params["upload_ids"] = attached
    schedule = None
    if p.action in ("routine", "reminder"):
        if p.schedule is None:
            raise ProposalError("a routine or reminder needs a schedule")
        spec = p.schedule.model_dump()
        try:
            schedule = sched.validate({k: v for k, v in spec.items() if v not in (None, "", [])})
        except (ValueError, TypeError) as e:
            raise ProposalError(f"could not understand the schedule: {e}") from e
    # The smaller model sometimes leaves the title out; a plain one beats an error.
    if not p.title.strip():
        if p.skill == "reminder" and p.text.strip():
            p.title = p.text.strip()
        elif p.skill in ("news", "morning_brief") and p.topics:
            p.title = " and ".join(t.strip() for t in p.topics[:3]) + (" news" if p.skill == "news" else " brief")
        else:
            p.title = skill.title
    p.title = p.title.strip()[:1].upper() + p.title.strip()[1:]
    if p.skill == "reminder" and not params.get("text"):
        params["text"] = p.title
    if p.skill == "web_watch" and not params.get("url"):
        raise ProposalError("watching a page needs its address")
    return {
        "action": p.action, "skill": p.skill, "skill_title": skill.title, "title": p.title.strip()[:80],
        "instructions": p.instructions.strip(), "params": params, "schedule": schedule,
        "schedule_text": sched.describe(schedule) if schedule else "",
        "question": p.question.strip(), "message": p.message.strip(), "needs": list(skill.needs),
    }


def ask(message: str, user: dict, connected: dict[str, str], files: list[dict], attached: list[str],
        history: list[dict], client=None, model: str | None = None,
        now: dt.datetime | None = None) -> tuple[dict, int]:
    """Run the Concierge. Returns (proposal, tokens used)."""
    task = concierge_task(message, user, connected, files, attached, history,
                          now or dt.datetime.now(dt.UTC))
    with tempfile.TemporaryDirectory(prefix="concierge_") as tmp:
        result = run_agent(task, root=Path(tmp), client=client, model=model, role=CONCIERGE,
                           max_steps=CONCIERGE.max_steps, runs_dir=Path(tmp) / "traces")
    if result.output is None:
        raise ProposalError(result.error or "the Concierge did not make a proposal")
    return normalise(result.output, attached), result.total_tokens

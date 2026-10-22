"""Everything the assistant can do. The Concierge picks one skill per request; the skill decides
what material is gathered before the team starts, and whether a routine should stay quiet when
there is nothing to report.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    key: str
    title: str
    description: str  # for the Concierge: when to use it
    needs: tuple[str, ...] = ()  # connections it requires: gmail, calendar
    uses_ai: bool = True
    quiet_when_nothing_new: bool = False
    params: dict = field(default_factory=dict)  # parameter -> what it means


SKILLS: dict[str, Skill] = {s.key: s for s in [
    Skill("answer", "Ask anything",
          "A one-off piece of work with no outside material: write, explain, plan, brainstorm, "
          "answer a question, draft a message, make a web page.",
          params={}),
    Skill("news", "News briefing",
          "Headlines about chosen topics, usually on a schedule (e.g. every morning).",
          params={"topics": "list of topics, e.g. ['technology', 'Pakistan']",
                  "region": "two-letter country code for local news, e.g. PK"}),
    Skill("email_digest", "Email summary",
          "Summarise the person's recent Gmail: what needs a reply, deadlines, what can wait.",
          needs=("gmail",),
          params={"days": "how many days back to read: 1 for daily, 7 for weekly"}),
    Skill("morning_brief", "Morning brief",
          "One short start-of-day page: the weather, today's calendar, top news, and important "
          "new emails (whatever is connected).",
          params={"city": "city for the weather", "topics": "news topics"}),
    Skill("file_task", "Work on a file",
          "Do something with files the person uploaded: summarise, translate, rewrite, turn notes "
          "into flashcards or a quiz, extract action items, answer questions about it.",
          params={"upload_ids": "ids of the uploaded files to use"}),
    Skill("deadlines", "Deadline finder",
          "Find dates, deadlines, and things the person promised in recent emails and uploaded "
          "files, as a dated list.",
          needs=("gmail",),
          params={"days": "how many days of email to read (default 14)",
                  "upload_ids": "optional uploaded files to include"}),
    Skill("reply_drafts", "Reply drafts",
          "Draft replies to recent emails that need one. Drafts only: nothing is ever sent.",
          needs=("gmail",),
          params={"days": "how many days back (default 3)"}),
    Skill("research", "Research report",
          "A short, sourced report on a topic, from recent news, Wikipedia, and any web pages "
          "the person names.",
          params={"topic": "what to research", "urls": "optional pages to read"}),
    Skill("web_watch", "Watch a web page",
          "Check a page on a schedule and report only when it changes (a price, exam results, "
          "job postings, an announcement).",
          quiet_when_nothing_new=True,
          params={"url": "the page", "focus": "what part matters, e.g. 'the price'"}),
    Skill("weather_alert", "Weather heads-up",
          "Check tomorrow's weather every evening and speak up only for rain, storms, or "
          "unusual heat or cold.",
          uses_ai=False, quiet_when_nothing_new=True,
          params={"city": "the city"}),
    Skill("weekly_plan", "Weekly planner",
          "Plan the coming week around the person's goals and calendar.",
          params={"goals": "what they want to get done"}),
    Skill("reminder", "Reminder",
          "A notification with the person's own words at a time, once or repeating. No AI work.",
          uses_ai=False,
          params={"text": "what to remind them about"}),
]}

SUGGESTIONS = [
    {"label": "News every morning", "skill": "news",
     "prompt": "Every day at 7 AM, give me the top news about technology and Pakistan."},
    {"label": "Daily email summary", "skill": "email_digest",
     "prompt": "Every evening at 8 PM, summarise today's emails and tell me what needs a reply."},
    {"label": "Help me study a file", "skill": "file_task",
     "prompt": "Turn my uploaded notes into a one-page summary and 10 flashcards."},
    {"label": "Morning brief", "skill": "morning_brief",
     "prompt": "Every weekday at 7:30 AM, give me a morning brief for Islamabad: weather, my calendar, and the headlines."},
    {"label": "Remind me", "skill": "reminder",
     "prompt": "Remind me every day at 10 PM to plan tomorrow."},
    {"label": "Watch a page", "skill": "web_watch",
     "prompt": "Check https://example.com every 6 hours and tell me when it changes."},
    {"label": "Research a topic", "skill": "research",
     "prompt": "Write me a short report on what's new in AI agents this month."},
    {"label": "Plan my week", "skill": "weekly_plan",
     "prompt": "Every Sunday at 6 PM, plan my week: I want to study linear algebra 1 hour a day and go to the gym 3 times."},
]


def catalogue_text() -> str:
    """The skill list as the Concierge sees it."""
    lines = []
    for s in SKILLS.values():
        params = "; ".join(f"{k}: {v}" for k, v in s.params.items()) or "none"
        needs = f" Needs: {', '.join(s.needs)}." if s.needs else ""
        lines.append(f"- {s.key} ({s.title}): {s.description}{needs} Params: {params}")
    return "\n".join(lines)

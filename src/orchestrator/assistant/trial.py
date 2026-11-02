"""Try it out: up to three free tries of Relay for people without an invite.

A try is one job the crew runs (asking the Concierge is free, and so are reminders, which don't
use the AI). Relay runs on free AI models, so the tries are few on purpose: enough to see what the
crew can do. A guest is an ordinary user with an email at @trial.relay and a `trial_until` time in
their preferences, after which they are signed out and deleted. Everything that keeps it fair:

    - TRIAL_TRIES jobs per guest (3)
    - one trial per browser (a long-lived cookie remembers it) and at most a few per network
      in 30 days, so a shared campus Wi-Fi still works but nobody collects endless tries
    - a cap on new trials per day for the whole site
    - a small AI allowance per guest, and a shared daily pool for all guests together, so
      guests can never use up what the owner and their invited people get
    - no Gmail or Calendar, and everything a guest made is deleted a day after they start

Addresses and browser tokens are stored only as keyed hashes (HMAC with the site's secret),
never as they are.

Settings (environment variables): TRIALS (on/off; on by default on a public server),
TRIAL_TRIES (3), TRIAL_HOURS (24, how long a guest's things are kept), TRIAL_TOKENS (60000 per guest), TRIAL_DAILY_TOKENS (150000 for all guests),
TRIALS_PER_DAY (25), TRIALS_PER_NETWORK (3 in 30 days).
"""
import datetime as dt
import hashlib
import hmac
import os
import secrets

from orchestrator.assistant import crypto
from orchestrator.assistant.store import Store, now

GUEST_DOMAIN = "trial.relay"
BROWSER_COOKIE = "relay_tried"
NETWORK_WINDOW = dt.timedelta(days=30)


def _setting(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def enabled(public: bool) -> bool:
    return os.getenv("TRIALS", "on" if public else "off").strip().lower() in ("on", "1", "true", "yes")


def hours() -> int:
    return max(1, _setting("TRIAL_HOURS", 24))


def tries() -> int:
    return max(1, _setting("TRIAL_TRIES", 3))


def tries_left(store: Store, user: dict) -> int:
    """Free tries a guest has left: every job that used the AI counts as one."""
    from orchestrator.assistant.skills import SKILLS
    used = sum(1 for j in store.jobs_for(user["id"], limit=200)
               if getattr(SKILLS.get(j["skill"]), "uses_ai", True))
    return max(0, tries() - used)


OUT_OF_TRIES = ("That was your last free try. Thanks for trying Relay! Ask the person who runs it "
                "for an invite to keep going.")


def is_guest(user: dict | None) -> bool:
    return bool(user) and str(user.get("email", "")).endswith("@" + GUEST_DOMAIN)


def ends_at(user: dict | None) -> dt.datetime | None:
    if not is_guest(user):
        return None
    try:
        return dt.datetime.fromisoformat((user.get("prefs") or {}).get("trial_until", ""))
    except ValueError:
        return now()  # a guest without a valid end time has no time left


def expired(user: dict | None, at: dt.datetime | None = None) -> bool:
    end = ends_at(user)
    return end is not None and end <= (at or now())


def fingerprint(kind: str, value: str) -> str:
    """A keyed hash: the same address always gives the same key, but the key can't be reversed."""
    mac = hmac.new(crypto.secret_key().encode(), f"{kind}:{value}".encode(), hashlib.sha256)
    return f"{kind}:{mac.hexdigest()[:40]}"


class TrialRefused(Exception):
    """A plain sentence for the person saying why there are no free tries for them right now."""


def start(store: Store, network: str, browser_token: str | None) -> tuple[dict, str]:
    """Create a guest. Returns (user, browser token to remember in a cookie)."""
    if browser_token and store.trial_starts(fingerprint("browser", browser_token)):
        raise TrialRefused("This browser has already had its free tries. Ask the owner for an invite to keep using Relay.")
    month_ago = now() - NETWORK_WINDOW
    if store.trial_starts(fingerprint("network", network), since=month_ago) >= _setting("TRIALS_PER_NETWORK", 3):
        raise TrialRefused("Free tries from this network are used up for now. Ask the owner for an invite.")
    day_ago = now() - dt.timedelta(days=1)
    if store.trial_starts_total(since=day_ago) >= _setting("TRIALS_PER_DAY", 25):
        raise TrialRefused("Today's free tries are all taken. Try again tomorrow.")
    token = browser_token or secrets.token_urlsafe(24)
    until = now() + dt.timedelta(hours=hours())
    user = store.add_guest(GUEST_DOMAIN, until)
    store.add_trial_start([fingerprint("browser", token), fingerprint("network", network)])
    return user, token


def allowance_left(store: Store, user: dict) -> bool:
    """A guest's own allowance, and the pool all guests share (checked on top of the usual limits)."""
    return (store.usage_today(user["id"]) < _setting("TRIAL_TOKENS", 60_000)
            and store.guest_usage_today(GUEST_DOMAIN) < _setting("TRIAL_DAILY_TOKENS", 150_000))


def clean_up(store: Store, grace: dt.timedelta = dt.timedelta(hours=1)) -> int:
    """Delete guests whose time ended more than `grace` ago, with everything they made.
    Old trial records (only hashes) are dropped after the 30-day window."""
    cutoff = now() - grace
    gone = 0
    for user in store.guests(GUEST_DOMAIN):
        if expired(user, at=cutoff):
            store.delete_user_and_data(user["id"])
            gone += 1
    store.prune_trial_starts(before=now() - NETWORK_WINDOW)
    return gone

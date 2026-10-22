"""Google sign-in, plus read-only Gmail and Calendar.

Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and PUBLIC_URL (the site's address, used for the
redirect back after signing in). Without them, Relay runs in local mode with one local user.

Scopes are requested step by step: signing in asks only for name and email; Gmail and
Calendar are separate, optional connections. While the Google app is in "Testing" mode,
Google ends these connections after seven days; Relay then marks them expired and asks the
person to reconnect with one tap.
"""
import base64
import datetime as dt
import os
import re
from html import unescape
from urllib.parse import urlencode

import httpx

from orchestrator.assistant import crypto

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

SCOPES = {
    "signin": ["openid", "email", "profile"],
    "gmail": ["https://www.googleapis.com/auth/gmail.readonly"],
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
}
TIMEOUT = httpx.Timeout(20.0)


class ConnectionExpired(Exception):
    """Google no longer accepts the stored token; the person must reconnect."""


def configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def redirect_uri() -> str:
    return os.getenv("PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/") + "/auth/callback"


def auth_url(state: str, purpose: str, login_hint: str = "") -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES["signin"] + (SCOPES[purpose] if purpose != "signin" else [])),
        "state": state,
        "include_granted_scopes": "true",
    }
    if purpose != "signin":
        params.update(access_type="offline", prompt="consent")
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict:
    response = httpx.post(TOKEN_URL, timeout=TIMEOUT, data={
        "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
    })
    response.raise_for_status()
    token = response.json()
    token["expires_at"] = dt.datetime.now(dt.UTC).timestamp() + int(token.get("expires_in", 3600))
    return token


def userinfo(access_token: str) -> dict:
    response = httpx.get(USERINFO_URL, timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {access_token}"})
    response.raise_for_status()
    return response.json()


def _refresh(token: dict) -> dict:
    if not token.get("refresh_token"):
        raise ConnectionExpired("no refresh token")
    response = httpx.post(TOKEN_URL, timeout=TIMEOUT, data={
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
    })
    if response.status_code in (400, 401):  # invalid_grant: revoked or expired after 7 days
        raise ConnectionExpired(response.text[:200])
    response.raise_for_status()
    fresh = response.json()
    token = {**token, **fresh}
    token["expires_at"] = dt.datetime.now(dt.UTC).timestamp() + int(fresh.get("expires_in", 3600))
    return token


def access_token(store, user_id: str, provider: str) -> str:
    """A valid access token for the connection, refreshed and saved if needed."""
    row = store.connection(user_id, provider)
    if row is None or row["status"] != "ok":
        raise ConnectionExpired(f"{provider} is not connected")
    token = crypto.decrypt(row["token"]) or {}
    if token.get("expires_at", 0) - 60 < dt.datetime.now(dt.UTC).timestamp():
        try:
            token = _refresh(token)
        except ConnectionExpired:
            store.set_connection_status(user_id, provider, "expired")
            raise
        store.save_connection(user_id, provider, crypto.encrypt(token))
    return token["access_token"]


# ---------- Gmail (read-only) ----------


def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h.get("name", "").lower() == name.lower()), "")


def _body_text(payload: dict, limit: int = 1500) -> str:
    """The plain-text body of a message (or its HTML with the tags removed)."""
    def walk(part):
        yield part
        for child in part.get("parts", []) or []:
            yield from walk(child)
    plain = html = ""
    for part in walk(payload):
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        if part.get("mimeType") == "text/plain" and not plain:
            plain = text
        elif part.get("mimeType") == "text/html" and not html:
            html = text
    text = plain or unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def gmail_messages(token: str, days: int = 1, limit: int = 25) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    query = f"newer_than:{max(1, min(days, 31))}d -category:promotions -category:social"
    listing = httpx.get(f"{GMAIL_API}/messages", headers=headers, timeout=TIMEOUT,
                        params={"q": query, "maxResults": limit})
    if listing.status_code == 401:
        raise ConnectionExpired("gmail token rejected")
    listing.raise_for_status()
    messages = []
    for item in listing.json().get("messages", []):
        full = httpx.get(f"{GMAIL_API}/messages/{item['id']}", headers=headers, timeout=TIMEOUT,
                         params={"format": "full"})
        if full.status_code != 200:
            continue
        data = full.json()
        payload = data.get("payload", {})
        hs = payload.get("headers", [])
        messages.append({
            "from": _header(hs, "From"), "subject": _header(hs, "Subject"),
            "date": _header(hs, "Date"), "labels": data.get("labelIds", []),
            "snippet": unescape(data.get("snippet", "")), "body": _body_text(payload),
            "link": f"https://mail.google.com/mail/u/0/#all/{data.get('threadId', item['id'])}",
        })
    return messages


# ---------- Calendar (read-only) ----------


def calendar_events(token: str, days: int = 1, tz: str = "UTC") -> list[dict]:
    start = dt.datetime.now(dt.UTC)
    response = httpx.get(CALENDAR_API, timeout=TIMEOUT, headers={"Authorization": f"Bearer {token}"},
                         params={"timeMin": start.isoformat(),
                                 "timeMax": (start + dt.timedelta(days=days)).isoformat(),
                                 "singleEvents": "true", "orderBy": "startTime", "maxResults": 50,
                                 "timeZone": tz})
    if response.status_code == 401:
        raise ConnectionExpired("calendar token rejected")
    response.raise_for_status()
    events = []
    for e in response.json().get("items", []):
        events.append({
            "title": e.get("summary", "(no title)"),
            "start": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date", ""),
            "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date", ""),
            "location": e.get("location", ""),
        })
    return events

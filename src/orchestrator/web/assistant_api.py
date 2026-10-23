"""The assistant's web routes: sign-in, the Concierge, jobs, routines, files, notifications.

Every /api route that changes something requires the header "X-Relay: 1". Browsers only send
custom headers from the site's own pages, so other sites cannot make a signed-in visitor's
browser change anything (a simple CSRF defence on top of SameSite cookies).
"""
import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from orchestrator.assistant import concierge, google, push
from orchestrator.assistant import schedule as sched
from orchestrator.assistant.runner import allowance_left
from orchestrator.assistant.skills import SKILLS, SUGGESTIONS
from orchestrator.assistant.store import Store, now
from orchestrator.assistant.worker import Worker

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOADS = 40
UPLOAD_TYPES = (".pdf", ".docx", ".txt", ".md", ".csv", ".json", ".html", ".htm", ".py", ".js",
                ".ts", ".java", ".c", ".cpp", ".h", ".tex", ".rtf", ".xml", ".yaml", ".yml")
LOCAL_EMAIL = "you@this-computer"


def allowed_emails() -> set[str]:
    return {e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()}


def invites() -> dict[str, str]:
    """INVITES="you@gmail.com=CODE,friend@gmail.com=CODE2": sign-in without Google."""
    out = {}
    for item in os.getenv("INVITES", "").split(","):
        email, _, code = item.partition("=")
        if email.strip() and len(code.strip()) >= 8:
            out[email.strip().lower()] = code.strip()
    return out


class InviteBody(BaseModel):
    email: str = Field(max_length=320)
    code: str = Field(max_length=100)


class Throttle:
    """At most `limit` failed sign-ins per address in `window` seconds."""

    def __init__(self, limit: int = 8, window: float = 900):
        self.limit, self.window, self.fails = limit, window, {}

    def blocked(self, key: str) -> bool:
        now_s = time.monotonic()
        self.fails[key] = [t for t in self.fails.get(key, []) if now_s - t < self.window]
        return len(self.fails[key]) >= self.limit

    def fail(self, key: str) -> None:
        self.fails.setdefault(key, []).append(time.monotonic())


class AskBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list, max_length=12)
    upload_ids: list[str] = Field(default_factory=list, max_length=10)


class ConfirmBody(BaseModel):
    proposal: dict


class RoutinePatch(BaseModel):
    enabled: bool | None = None
    schedule: dict | None = None


class MePatch(BaseModel):
    tz: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=80)
    topics: list[str] | None = Field(default=None, max_length=8)
    about: str | None = Field(default=None, max_length=600)


class PushBody(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: dict


def _job_view(job: dict, full: bool = False) -> dict:
    view = {k: job.get(k) for k in ("id", "title", "skill", "status", "summary", "unread", "tokens",
                                    "routine_id", "created_at", "finished_at")}
    view["skill_title"] = SKILLS[job["skill"]].title if job["skill"] in SKILLS else job["skill"]
    if full:
        view.update(files=job.get("files") or {}, events=job.get("events") or [],
                    instructions=job.get("instructions", ""), error=bool(job.get("error")))
    return view


def _routine_view(r: dict) -> dict:
    return {
        "id": r["id"], "title": r["title"], "skill": r["skill"],
        "skill_title": SKILLS[r["skill"]].title if r["skill"] in SKILLS else r["skill"],
        "schedule": r["schedule"], "schedule_text": sched.describe(r["schedule"]),
        "enabled": r["enabled"], "next_run_at": r["next_run_at"], "last_run_at": r["last_run_at"],
        "instructions": r["instructions"], "params": r["params"],
    }


def assistant_router(store: Store, worker: Worker, public: bool) -> APIRouter:
    router = APIRouter()
    local_mode = not google.configured() and not public

    # ---------- who is asking ----------

    def current_user(request: Request) -> dict | None:
        uid = request.session.get("uid")
        if uid:
            return store.user(uid)
        if local_mode:  # one person on their own computer: no sign-in needed
            user = store.upsert_user(LOCAL_EMAIL, os.getenv("RELAY_LOCAL_NAME", "you"))
            request.session["uid"] = user["id"]
            return user
        return None

    def need_user(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "Please sign in.")
        return user

    def same_site(request: Request) -> None:
        if request.headers.get("x-relay") != "1":
            raise HTTPException(403, "Missing X-Relay header.")

    # ---------- sign-in and connections ----------

    @router.get("/auth/login")
    def login(request: Request):
        if not google.configured():
            if local_mode:
                return RedirectResponse("/", 303)
            raise HTTPException(503, "Sign-in is not set up on this server yet.")
        state = secrets.token_urlsafe(24)
        request.session.update(oauth_state=state, oauth_purpose="signin")
        return RedirectResponse(google.auth_url(state, "signin"), 303)

    @router.get("/auth/connect/{provider}")
    def connect(provider: str, request: Request):
        user = need_user(request)
        if provider not in ("gmail", "calendar"):
            raise HTTPException(404, "Unknown connection.")
        if not google.configured():
            raise HTTPException(503, "Google is not set up on this server. See docs/DEPLOY.md.")
        state = secrets.token_urlsafe(24)
        request.session.update(oauth_state=state, oauth_purpose=provider)
        return RedirectResponse(google.auth_url(state, provider, login_hint=user["email"]), 303)

    @router.get("/auth/callback")
    def callback(request: Request, state: str = "", code: str = "", error: str = ""):
        expected = request.session.pop("oauth_state", None)
        purpose = request.session.pop("oauth_purpose", "signin")
        if error or not code or not expected or not secrets.compare_digest(state, expected):
            return RedirectResponse("/?signin=cancelled", 303)
        token = google.exchange_code(code)
        info = google.userinfo(token["access_token"])
        email = (info.get("email") or "").lower()
        if not email or not info.get("email_verified", False):
            return RedirectResponse("/?signin=unverified", 303)
        allow = allowed_emails() | set(invites())
        if allow and email not in allow:
            return RedirectResponse("/?signin=not-invited", 303)
        if purpose == "signin":
            user = store.upsert_user(email, info.get("name", ""), info.get("picture", ""))
            request.session["uid"] = user["id"]
            return RedirectResponse("/", 303)
        user = need_user(request)
        if email != user["email"]:
            return RedirectResponse("/?connect=wrong-account", 303)
        if not token.get("refresh_token"):
            return RedirectResponse("/?connect=failed", 303)
        from orchestrator.assistant import crypto
        store.save_connection(user["id"], purpose, crypto.encrypt(token))
        return RedirectResponse(f"/?connected={purpose}", 303)

    throttle = Throttle()

    @router.post("/auth/invite", dependencies=[Depends(same_site)])
    def invite_sign_in(body: InviteBody, request: Request):
        """Sign in with an invite code from INVITES (no Google account needed)."""
        who = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "?")
        if throttle.blocked(who):
            raise HTTPException(429, "Too many tries. Wait 15 minutes and try again.")
        email = body.email.strip().lower()
        expected = invites().get(email)
        if not expected or not secrets.compare_digest(body.code.strip(), expected):
            throttle.fail(who)
            raise HTTPException(401, "That email and invite code don't match.")
        user = store.upsert_user(email, email.split("@")[0].replace(".", " ").title())
        request.session["uid"] = user["id"]
        return {"ok": True}

    @router.post("/auth/logout", dependencies=[Depends(same_site)])
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @router.post("/api/connections/{provider}/disconnect", dependencies=[Depends(same_site)])
    def disconnect(provider: str, request: Request):
        store.delete_connection(need_user(request)["id"], provider)
        return {"ok": True}

    # ---------- the person ----------

    @router.get("/api/me")
    def me(request: Request):
        user = current_user(request)
        if user is None:
            return {"signed_in": False, "google": google.configured(), "local": local_mode,
                    "invites": bool(invites())}
        connected = store.connections_for(user["id"])
        return {
            "signed_in": True, "local": local_mode, "google": google.configured(),
            "user": {"name": user["name"], "email": user["email"], "picture": user["picture"],
                     "tz": user["tz"], "prefs": user["prefs"] or {}},
            "connections": connected, "push_key": push.public_key(),
            "push_devices": len(store.pushes_for(user["id"])),
            "ai_ready": worker.client_factory is not None,
            "allowance_left": allowance_left(store, user["id"]),
            "suggestions": SUGGESTIONS,
            "skills": {k: {"title": s.title, "needs": list(s.needs)} for k, s in SKILLS.items()},
        }

    @router.patch("/api/me", dependencies=[Depends(same_site)])
    def update_me(body: MePatch, request: Request):
        user = need_user(request)
        prefs = dict(user["prefs"] or {})
        for key in ("city", "topics", "about"):
            value = getattr(body, key)
            if value is not None:
                prefs[key] = value
        values = {"prefs": prefs}
        if body.tz and sched.zone(body.tz) is not None:
            values["tz"] = body.tz
        store.update_user(user["id"], **values)
        return {"ok": True}

    @router.get("/api/demo-recordings")
    def demo_recordings(request: Request):
        """Recorded runs, so the office has something to show before your first job."""
        need_user(request)
        from orchestrator.web.site import load_recordings
        return load_recordings()

    # ---------- the Concierge ----------

    @router.post("/api/ask", dependencies=[Depends(same_site)])
    def ask(body: AskBody, request: Request):
        user = need_user(request)
        client = worker.client()
        if client is None:
            raise HTTPException(503, "The AI is not set up on this server yet.")
        if not allowance_left(store, user["id"]):
            raise HTTPException(429, "Today's AI allowance is used up. It resets at midnight (UTC).")
        files = store.uploads_for(user["id"])
        owned = {f["id"] for f in files}
        attached = [i for i in body.upload_ids if i in owned]
        try:
            proposal, tokens = concierge.ask(body.message, user, store.connections_for(user["id"]),
                                             files, attached, body.history, client=client,
                                             model=(worker.models or {}).get("concierge")
                                             or os.getenv("LLM_MODEL_CONCIERGE"))
        except concierge.ProposalError as e:
            raise HTTPException(422, f"Sorry, I could not work that out ({e}). Could you say it another way?") from e
        store.add_usage(user["id"], tokens)
        return proposal

    @router.post("/api/plans", dependencies=[Depends(same_site)])
    def confirm(body: ConfirmBody, request: Request):
        """Create what the Concierge proposed, after the person confirmed it."""
        user = need_user(request)
        p = body.proposal
        skill = p.get("skill")
        if skill not in SKILLS or p.get("action") not in ("job", "routine", "reminder"):
            raise HTTPException(400, "That plan is not valid.")
        owned = {f["id"] for f in store.uploads_for(user["id"])}
        params = dict(p.get("params") or {})
        params["upload_ids"] = [i for i in params.get("upload_ids", []) if i in owned]
        if not params["upload_ids"]:
            params.pop("upload_ids")
        title = str(p.get("title") or SKILLS[skill].title)[:80]
        instructions = str(p.get("instructions") or "")[:6000]
        if p["action"] == "job":
            job = store.add_job(user["id"], title, skill, params, instructions)
            worker.poke()
            return {"kind": "job", "job": _job_view(job)}
        try:
            schedule = sched.validate(p.get("schedule") or {})
        except (ValueError, TypeError) as e:
            raise HTTPException(400, f"That schedule is not valid: {e}") from e
        if len(store.routines_for(user["id"])) >= 25:
            raise HTTPException(400, "You already have 25 routines. Remove one first.")
        routine = store.add_routine(user["id"], title, skill, params, instructions, schedule,
                                    sched.next_run(schedule, user["tz"], now()))
        return {"kind": "routine", "routine": _routine_view(routine)}

    # ---------- jobs (the Inbox) ----------

    @router.get("/api/jobs")
    def list_jobs(request: Request):
        user = need_user(request)
        return [_job_view(j) for j in store.jobs_for(user["id"]) if j["status"] != "quiet"]

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        job = store.job(need_user(request)["id"], job_id)
        if job is None:
            raise HTTPException(404, "No such job.")
        view = _job_view(job, full=True)
        if job["status"] == "running":
            view["events"] = worker.live.get(job_id) or []
        return view

    @router.post("/api/jobs/{job_id}/read", dependencies=[Depends(same_site)])
    def mark_read(job_id: str, request: Request):
        if store.job(need_user(request)["id"], job_id) is None:
            raise HTTPException(404, "No such job.")
        store.update_job(job_id, unread=False)
        return {"ok": True}

    @router.delete("/api/jobs/{job_id}", dependencies=[Depends(same_site)])
    def delete_job(job_id: str, request: Request):
        if not store.delete_job(need_user(request)["id"], job_id):
            raise HTTPException(404, "No such job.")
        return {"ok": True}

    @router.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        user = need_user(request)
        if store.job(user["id"], job_id) is None:
            raise HTTPException(404, "No such job.")

        async def stream():
            sent = 0
            while True:
                live = worker.live.get(job_id)
                job = await asyncio.to_thread(store.job, user["id"], job_id)
                if live is None and job and job["status"] not in ("queued", "running"):
                    for event in (job["events"] or [])[sent:]:
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                    yield "event: end\ndata: {}\n\n"
                    return
                for event in (live or [])[sent:]:
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    sent += 1
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.4)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @router.get("/api/jobs/{job_id}/files/{name:path}")
    def download(job_id: str, name: str, request: Request):
        job = store.job(need_user(request)["id"], job_id)
        if job is None or name not in (job["files"] or {}):
            raise HTTPException(404, "No such file.")
        return Response((job["files"][name]).encode("utf-8"), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{Path(name).name}"'})

    # ---------- routines ----------

    @router.get("/api/routines")
    def list_routines(request: Request):
        return [_routine_view(r) for r in store.routines_for(need_user(request)["id"])]

    @router.patch("/api/routines/{routine_id}", dependencies=[Depends(same_site)])
    def update_routine(routine_id: str, body: RoutinePatch, request: Request):
        user = need_user(request)
        routine = store.routine(user["id"], routine_id)
        if routine is None:
            raise HTTPException(404, "No such routine.")
        values = {}
        schedule = routine["schedule"]
        if body.schedule is not None:
            try:
                schedule = values["schedule"] = sched.validate(body.schedule)
            except (ValueError, TypeError) as e:
                raise HTTPException(400, f"That schedule is not valid: {e}") from e
        if body.enabled is not None:
            values["enabled"] = body.enabled
        if values.get("enabled", routine["enabled"]):
            values["next_run_at"] = sched.next_run(schedule, user["tz"], now())
        store.update_routine(routine_id, **values)
        return _routine_view(store.routine(user["id"], routine_id))

    @router.delete("/api/routines/{routine_id}", dependencies=[Depends(same_site)])
    def delete_routine(routine_id: str, request: Request):
        if not store.delete_routine(need_user(request)["id"], routine_id):
            raise HTTPException(404, "No such routine.")
        return {"ok": True}

    @router.post("/api/routines/{routine_id}/run", dependencies=[Depends(same_site)])
    def run_now(routine_id: str, request: Request):
        user = need_user(request)
        r = store.routine(user["id"], routine_id)
        if r is None:
            raise HTTPException(404, "No such routine.")
        job = store.add_job(user["id"], r["title"], r["skill"], r["params"] or {}, r["instructions"],
                            routine_id=r["id"])
        worker.poke()
        return {"job": _job_view(job)}

    # ---------- files ----------

    @router.post("/api/uploads", dependencies=[Depends(same_site)])
    async def upload(request: Request, file: Annotated[UploadFile, File()]):
        user = await asyncio.to_thread(need_user, request)
        name = Path(file.filename or "file").name
        if not name.lower().endswith(UPLOAD_TYPES):
            raise HTTPException(400, "This kind of file is not supported yet. Try PDF, Word, or text.")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "Files can be up to 10 MB.")
        if len(await asyncio.to_thread(store.uploads_for, user["id"])) >= MAX_UPLOADS:
            raise HTTPException(400, f"You can keep up to {MAX_UPLOADS} files. Delete one first.")
        return await asyncio.to_thread(store.add_upload, user["id"], name, file.content_type or "", data)

    @router.get("/api/uploads")
    def list_uploads(request: Request):
        return store.uploads_for(need_user(request)["id"])

    @router.delete("/api/uploads/{upload_id}", dependencies=[Depends(same_site)])
    def delete_upload(upload_id: str, request: Request):
        if not store.delete_upload(need_user(request)["id"], upload_id):
            raise HTTPException(404, "No such file.")
        return {"ok": True}

    # ---------- notifications ----------

    @router.post("/api/push/subscribe", dependencies=[Depends(same_site)])
    def subscribe(body: PushBody, request: Request):
        user = need_user(request)
        if not body.endpoint.startswith("https://") or not {"p256dh", "auth"} <= set(body.keys):
            raise HTTPException(400, "That subscription is not valid.")
        store.add_push(user["id"], body.endpoint, {"p256dh": body.keys["p256dh"], "auth": body.keys["auth"]})
        return {"ok": True}

    @router.post("/api/push/test", dependencies=[Depends(same_site)])
    def test_push(request: Request):
        user = need_user(request)
        sent = push.send(store, user["id"], "Notifications are on",
                         "This is how Relay will tell you when something is ready.", url="/")
        return {"sent": sent}

    return router

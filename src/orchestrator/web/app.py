"""The Relay website: a personal assistant run by a team of AI agents.

Pages
    GET  /                          the assistant (static/app.html); people who aren't signed
                                    in see the front page, with sign-in and the free day
    GET  /office                    the robot office on its own, shown inside the assistant
    GET  /demo                      the office playing recorded runs (members only in public)
    GET  /healthz                   "is it up?" for the host and the keep-awake ping
    GET  /sw.js, /manifest.webmanifest, /icons/*   notifications and installing the app

The assistant's API (sign-in, the Concierge, jobs, routines, files, notifications) is in
assistant_api.py, and the protective layer (headers, limits) in security.py. The developer
playground below runs the team directly on a request or a sample project; it only works on
your own computer:

    GET  /api/status, /api/examples, /api/replays
    POST /api/runs                  start a live run {prompt, kind, sample?} -> {id}
    POST /api/replays/{name}        start a replay -> {id}
    GET  /api/runs/{id}/events      the run's events as a Server-Sent Events stream

On your own computer (the default) Relay listens on 127.0.0.1, signs you in automatically,
and keeps its data in relay.db. On a public server (RELAY_PUBLIC=1, as on Render):
    - people sign in with an invite code or Google, or try it out for a day as a guest
    - the AI key is read once at start-up and removed from the environment, and the process
      is made non-dumpable, so code the agents run can't read it from /proc
    - model-written code runs with SANDBOX=guarded (see orchestrator/guard)
    - daily AI allowances for the site, each person, and guests (assistant/runner.py, trial.py)
Use a key from a provider account with no payment method, so the AI can never cost money.
"""
import asyncio
import contextlib
import ctypes
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from orchestrator.agent import default_model, make_client
from orchestrator.assistant import crypto, trial
from orchestrator.assistant.store import Store
from orchestrator.assistant.worker import Worker
from orchestrator.orchestrator import run_orchestrator
from orchestrator.tools import sandbox_mode
from orchestrator.web.assistant_api import assistant_router
from orchestrator.web.security import BodyLimit, SecurityHeaders
from orchestrator.web.site import load_recordings, render_page

WEB_DIR = Path(__file__).parent
MAX_BODY_BYTES = 11 * 1024 * 1024  # uploads are capped at 10 MB, everything else is far smaller
STATIC_DIR = WEB_DIR / "static"
REPLAYS_DIR = WEB_DIR / "replays"
PROJECT_ROOT = WEB_DIR.parents[2]  # the repository root (src/orchestrator/web -> repo)
TASKS_DIR = PROJECT_ROOT / "evals" / "tasks"
MAX_PROMPT_CHARS = 8000
REPLAY_MAX_PAUSE_S = 1.6  # long pauses in a recording are shortened when replayed
SECRET_VARS = ("LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")

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


def _protect_process_memory() -> None:
    """Make this process non-dumpable so other processes of the same user cannot read
    /proc/<pid>/environ or its memory (Linux only; a no-op elsewhere)."""
    try:
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except (OSError, AttributeError, TypeError):
        pass


def create_app(
    runner: Runner = run_orchestrator,
    runs_dir: Path = Path("runs") / "web",
    replays_dir: Path = REPLAYS_DIR,
    replay_speed: float = 1.0,
    public: bool | None = None,
    client_factory: Callable[[], object] = make_client,
    store: Store | None = None,
    start_worker: bool = True,
    models: dict | None = None,
) -> FastAPI:
    """Build the app. Tests pass fakes for the model client, the runner, and the store."""
    load_dotenv()
    if public is None:
        public = os.getenv("RELAY_PUBLIC", "").strip().lower() in ("1", "true", "yes")
    holder: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        if start_worker:
            holder["worker"].start()
        yield
        holder["worker"].stop()

    # No /docs, /redoc or /openapi.json: a map of every endpoint helps attackers more than us.
    app = FastAPI(title="Relay", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=crypto.secret_key(), session_cookie="relay",
                       max_age=30 * 24 * 3600, same_site="lax", https_only=public)
    app.add_middleware(SecurityHeaders, public=public)
    app.add_middleware(BodyLimit, limit=MAX_BODY_BYTES)
    runs: dict[str, WebRun] = {}
    lock = threading.Lock()

    has_key = any(os.getenv(name) for name in SECRET_VARS)
    ai_client = None
    if public:
        for name, why in (("RELAY_SECRET_KEY", "sign-ins and Google connections will not survive a restart"),
                          ("DATABASE_URL", "data is kept in a local file that a restart may wipe")):
            if not os.getenv(name):
                logging.getLogger("relay").warning("%s is not set: %s. See docs/DEPLOY.md.", name, why)
        os.environ["SANDBOX"] = "guarded"
        os.environ["RELAY_GUARD_DENY"] = str(PROJECT_ROOT)
        if has_key:
            ai_client = client_factory()
        for name in SECRET_VARS:
            os.environ.pop(name, None)
        _protect_process_memory()
        factory = (lambda: ai_client) if ai_client is not None else None
    else:
        factory = client_factory if has_key else None

    store = store or Store()
    worker = holder["worker"] = Worker(store, factory, models=models)
    app.state.store, app.state.worker = store, worker
    app.include_router(assistant_router(store, worker, public))

    def active_live_run() -> WebRun | None:
        return next((r for r in runs.values() if not r.done and not r.replay), None)

    # On a public server only signed-in people see anything beyond the sign-in page: the
    # office, the recordings, and every API behind them. (Locally you are always signed in.)
    def signed_in(request: Request) -> bool:
        uid = request.session.get("uid")
        user = store.user(uid) if uid else None
        return user is not None and not trial.expired(user)

    def members_only(request: Request) -> None:
        if public and not signed_in(request):
            raise HTTPException(401, "Please sign in.")

    def members_page(request: Request) -> RedirectResponse | None:
        if public and not signed_in(request):
            return RedirectResponse("/", 303)
        return None

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "app.html", headers={"Cache-Control": "no-cache"})

    @app.get("/demo", response_class=HTMLResponse)
    def demo(request: Request):
        return members_page(request) or HTMLResponse(
            render_page(live=not public, recordings=load_recordings(replays_dir)))

    @app.get("/healthz")
    def healthz() -> dict:
        """For an uptime pinger: keeps a free host awake so routines run on time."""
        return {"ok": True}

    @app.get("/office", response_class=HTMLResponse)
    def office(request: Request):
        """The office alone, for "Show agents working" inside the assistant."""
        return members_page(request) or HTMLResponse(render_page(live=False, recordings=[], embed=True))

    @app.get("/static/relay.html")
    def office_template() -> None:
        """The office's raw template is served only through /office and /demo."""
        raise HTTPException(404, "Not found.")

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/icons/{name}")
    def icon(name: str) -> Response:
        path = STATIC_DIR / "icons" / name
        if not re.fullmatch(r"[\w.-]+\.png", name) or not path.is_file():
            raise HTTPException(404, "No such icon.")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/status", dependencies=[Depends(members_only)])
    def status() -> dict:
        active = active_live_run()
        return {
            "api_key_configured": factory is not None,
            "public": public,
            "model": default_model(),
            "sandbox": sandbox_mode(),
            "active_run": active.id if active else None,
        }

    @app.get("/api/examples", dependencies=[Depends(members_only)])
    def examples() -> dict:
        return {"create": CREATE_EXAMPLES, "fix": fix_samples()}

    @app.get("/api/replays", dependencies=[Depends(members_only)])
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

    @app.post("/api/runs", dependencies=[Depends(members_only)])
    def start_run(body: StartRun) -> dict:
        if public:  # the open playground is only for your own computer
            raise HTTPException(404, "Not available here. Sign in and ask the assistant instead.")
        with lock:
            if active_live_run():
                raise HTTPException(409, "The team is busy with another job.")
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
            run = WebRun(id=run_id, kind=body.kind, log_path=folder / "events.jsonl")
            runs[run_id] = run

        def work() -> None:
            try:
                runner(body.prompt, workspace, kind=body.kind, on_update=run.publish,
                       runs_dir=folder / "traces")
            except Exception as e:  # noqa: BLE001 - e.g. no API key; show it in the page
                run.publish({"type": "run_finished", "status": "error",
                             "error": f"{type(e).__name__}: {e}", "time": time.time(),
                             "files": {}, "diff": "", "total_tokens": 0, "rounds": 0})
            finally:
                run.done = True

        threading.Thread(target=work, daemon=True).start()
        return {"id": run_id}

    @app.post("/api/replays/{name}", dependencies=[Depends(members_only)])
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

    @app.get("/api/runs/{run_id}/events", dependencies=[Depends(members_only)])
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

"""The background worker: starts due routines and runs queued jobs, one at a time.

One job at a time keeps a free-tier key inside its per-minute limits. Routines are checked
every POLL_S seconds; a routine that was due while the server slept runs once when it wakes,
not once for every missed time.
"""
import datetime as dt
import logging
import os
import threading
import time
from collections.abc import Callable

import httpx

from orchestrator.assistant import push
from orchestrator.assistant import schedule as sched
from orchestrator.assistant.runner import LiveEvents, run_job
from orchestrator.assistant.store import Store, now

log = logging.getLogger(__name__)
POLL_S = 5
KEEP_AWAKE_S = 600


def start_due_routines(store: Store, at: dt.datetime | None = None) -> list[dict]:
    """Queue a job for every routine that is due, and move each routine to its next time."""
    at = at or now()
    queued = []
    for routine in store.due_routines(at):
        user = store.user(routine["user_id"])
        if user is None:
            continue
        nxt = sched.next_run(routine["schedule"], user.get("tz"), at)
        store.update_routine(routine["id"], last_run_at=at, next_run_at=nxt,
                             enabled=routine["enabled"] and nxt is not None)
        if routine["skill"] == "reminder":
            text = (routine["params"] or {}).get("text") or routine["title"]
            job = store.add_job(user["id"], routine["title"], "reminder", routine["params"] or {}, "",
                                routine_id=routine["id"], status="done")
            store.update_job(job["id"], summary=text, finished_at=at)
            push.send(store, user["id"], "Reminder", text, url=f"/?job={job['id']}", tag=routine["id"])
            continue
        queued.append(store.add_job(user["id"], routine["title"], routine["skill"],
                                    routine["params"] or {}, routine["instructions"],
                                    routine_id=routine["id"]))
    return queued


class Worker:
    def __init__(self, store: Store, client_factory: Callable[[], object] | None = None,
                 models: dict | None = None):
        self.store = store
        self.client_factory = client_factory
        self.models = models
        self.live = LiveEvents()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._client = None
        self._last_ping = -KEEP_AWAKE_S

    def client(self):
        if self._client is None and self.client_factory is not None:
            self._client = self.client_factory()
        return self._client

    def poke(self) -> None:
        """A job was queued: look now instead of at the next poll."""
        self._wake.set()

    def start(self) -> None:
        self.store.requeue_interrupted()
        self._thread = threading.Thread(target=self._loop, name="relay-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run_once(self) -> dict | None:
        start_due_routines(self.store)
        job = self.store.next_queued_job()
        if job is None:
            return None
        return run_job(self.store, job, client=self.client(), models=self.models, live=self.live)

    def keep_awake(self) -> None:
        """Free hosts (Render) put a service to sleep after 15 minutes without visits, which
        would stop scheduled routines. With KEEP_AWAKE_URL set, the server visits itself every
        10 minutes through its public address, which counts as a visit."""
        url = os.getenv("KEEP_AWAKE_URL", "").rstrip("/")
        if not url or time.monotonic() - self._last_ping < KEEP_AWAKE_S:
            return
        self._last_ping = time.monotonic()
        try:
            httpx.get(f"{url}/healthz", timeout=30)
        except httpx.HTTPError as e:
            log.warning("keep-awake ping failed: %s", e)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.keep_awake()
                if self.run_once() is not None:
                    continue  # there may be more work waiting
            except Exception:
                log.exception("worker step failed")
            self._wake.wait(POLL_S)
            self._wake.clear()

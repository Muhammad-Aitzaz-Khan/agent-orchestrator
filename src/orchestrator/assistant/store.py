"""The assistant's database: people, connections, routines, jobs, files, and notification
subscriptions.

SQLite by default (relay.db next to where the server starts); set DATABASE_URL to a Postgres
URL (e.g. a free Neon database) for a server whose disk does not persist. Tokens for Google
are stored encrypted (see crypto.py).
"""
import datetime as dt
import os
import uuid
from typing import Any

import sqlalchemy as sa

metadata = sa.MetaData()
UTCNow = sa.DateTime(timezone=True)

users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("email", sa.String(320), unique=True, nullable=False),
    sa.Column("name", sa.String(200), default=""),
    sa.Column("picture", sa.String(500), default=""),
    sa.Column("tz", sa.String(64), default="Asia/Karachi"),
    sa.Column("prefs", sa.JSON, default=dict),  # e.g. {"city": "Islamabad", "about": "..."}
    sa.Column("created_at", UTCNow),
)

connections = sa.Table(
    "connections", metadata,
    sa.Column("user_id", sa.String(32), primary_key=True),
    sa.Column("provider", sa.String(32), primary_key=True),  # gmail, calendar
    sa.Column("token", sa.Text, nullable=False),  # encrypted JSON
    sa.Column("status", sa.String(16), default="ok"),  # ok, expired
    sa.Column("updated_at", UTCNow),
)

routines = sa.Table(
    "routines", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("user_id", sa.String(32), index=True, nullable=False),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("skill", sa.String(32), nullable=False),
    sa.Column("params", sa.JSON, default=dict),
    sa.Column("instructions", sa.Text, default=""),
    sa.Column("schedule", sa.JSON, nullable=False),
    sa.Column("enabled", sa.Boolean, default=True),
    sa.Column("next_run_at", UTCNow, index=True),
    sa.Column("last_run_at", UTCNow),
    sa.Column("state", sa.JSON, default=dict),  # e.g. the last snapshot of a watched page
    sa.Column("created_at", UTCNow),
)

jobs = sa.Table(
    "jobs", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("user_id", sa.String(32), index=True, nullable=False),
    sa.Column("routine_id", sa.String(32)),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("skill", sa.String(32), nullable=False),
    sa.Column("params", sa.JSON, default=dict),
    sa.Column("instructions", sa.Text, default=""),
    sa.Column("status", sa.String(16), default="queued"),  # queued running done failed skipped
    sa.Column("summary", sa.Text, default=""),
    sa.Column("files", sa.JSON, default=dict),
    sa.Column("events", sa.JSON, default=list),
    sa.Column("tokens", sa.Integer, default=0),
    sa.Column("error", sa.Text, default=""),
    sa.Column("unread", sa.Boolean, default=True),
    sa.Column("created_at", UTCNow, index=True),
    sa.Column("finished_at", UTCNow),
)

uploads = sa.Table(
    "uploads", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("user_id", sa.String(32), index=True, nullable=False),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("mime", sa.String(100), default=""),
    sa.Column("size", sa.Integer, default=0),
    sa.Column("data", sa.LargeBinary, nullable=False),
    sa.Column("created_at", UTCNow),
)

push_subs = sa.Table(
    "push_subscriptions", metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("user_id", sa.String(32), index=True, nullable=False),
    sa.Column("endpoint", sa.Text, nullable=False),
    sa.Column("keys", sa.JSON, nullable=False),
    sa.Column("created_at", UTCNow),
)

usage = sa.Table(
    "usage", metadata,
    sa.Column("day", sa.String(10), primary_key=True),
    sa.Column("user_id", sa.String(32), primary_key=True),
    sa.Column("tokens", sa.Integer, default=0),
)


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def _aware(value):
    """SQLite returns naive datetimes; everything here is UTC."""
    if isinstance(value, dt.datetime) and value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _row(row) -> dict | None:
    if row is None:
        return None
    return {k: _aware(v) for k, v in row._mapping.items()}


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip() or "sqlite:///relay.db"
    if url.startswith("postgres://"):  # the scheme many hosts hand out
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Store:
    def __init__(self, url: str | None = None):
        url = url or database_url()
        kwargs: dict[str, Any] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine = sa.create_engine(url, **kwargs)
        metadata.create_all(self.engine)

    # ---------- helpers ----------

    def _one(self, query) -> dict | None:
        with self.engine.connect() as c:
            return _row(c.execute(query).first())

    def _all(self, query) -> list[dict]:
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(query)]

    def _run(self, statement) -> int:
        with self.engine.begin() as c:
            return c.execute(statement).rowcount

    # ---------- people ----------

    def upsert_user(self, email: str, name: str = "", picture: str = "") -> dict:
        email = email.strip().lower()
        user = self._one(sa.select(users).where(users.c.email == email))
        if user:
            self._run(users.update().where(users.c.id == user["id"]).values(name=name or user["name"],
                                                                              picture=picture or user["picture"]))
        else:
            self._run(users.insert().values(id=new_id(), email=email, name=name, picture=picture,
                                            tz="Asia/Karachi", prefs={}, created_at=now()))
        return self._one(sa.select(users).where(users.c.email == email))

    def user(self, user_id: str) -> dict | None:
        return self._one(sa.select(users).where(users.c.id == user_id))

    def update_user(self, user_id: str, **values) -> dict | None:
        allowed = {k: v for k, v in values.items() if k in ("name", "tz", "prefs")}
        if allowed:
            self._run(users.update().where(users.c.id == user_id).values(**allowed))
        return self.user(user_id)

    # ---------- connections ----------

    def save_connection(self, user_id: str, provider: str, token: str, status: str = "ok") -> None:
        with self.engine.begin() as c:
            c.execute(connections.delete().where((connections.c.user_id == user_id)
                                                 & (connections.c.provider == provider)))
            c.execute(connections.insert().values(user_id=user_id, provider=provider, token=token,
                                                  status=status, updated_at=now()))

    def connection(self, user_id: str, provider: str) -> dict | None:
        return self._one(sa.select(connections).where((connections.c.user_id == user_id)
                                                      & (connections.c.provider == provider)))

    def connections_for(self, user_id: str) -> dict[str, str]:
        rows = self._all(sa.select(connections.c.provider, connections.c.status)
                         .where(connections.c.user_id == user_id))
        return {r["provider"]: r["status"] for r in rows}

    def set_connection_status(self, user_id: str, provider: str, status: str) -> None:
        self._run(connections.update().where((connections.c.user_id == user_id)
                                             & (connections.c.provider == provider))
                  .values(status=status, updated_at=now()))

    def delete_connection(self, user_id: str, provider: str) -> None:
        self._run(connections.delete().where((connections.c.user_id == user_id)
                                             & (connections.c.provider == provider)))

    # ---------- routines ----------

    def add_routine(self, user_id: str, title: str, skill: str, params: dict, instructions: str,
                    schedule: dict, next_run_at: dt.datetime | None) -> dict:
        routine_id = new_id()
        self._run(routines.insert().values(
            id=routine_id, user_id=user_id, title=title[:200], skill=skill, params=params,
            instructions=instructions, schedule=schedule, enabled=True, next_run_at=next_run_at,
            state={}, created_at=now()))
        return self.routine(user_id, routine_id)

    def routine(self, user_id: str, routine_id: str) -> dict | None:
        return self._one(sa.select(routines).where((routines.c.id == routine_id)
                                                   & (routines.c.user_id == user_id)))

    def routines_for(self, user_id: str) -> list[dict]:
        return self._all(sa.select(routines).where(routines.c.user_id == user_id)
                         .order_by(routines.c.created_at))

    def update_routine(self, routine_id: str, **values) -> None:
        self._run(routines.update().where(routines.c.id == routine_id).values(**values))

    def delete_routine(self, user_id: str, routine_id: str) -> bool:
        return self._run(routines.delete().where((routines.c.id == routine_id)
                                                 & (routines.c.user_id == user_id))) > 0

    def due_routines(self, at: dt.datetime) -> list[dict]:
        return self._all(sa.select(routines).where(routines.c.enabled.is_(True)
                                                   & (routines.c.next_run_at <= at)))

    # ---------- jobs ----------

    def add_job(self, user_id: str, title: str, skill: str, params: dict, instructions: str,
                routine_id: str | None = None, status: str = "queued") -> dict:
        job_id = new_id()
        self._run(jobs.insert().values(
            id=job_id, user_id=user_id, routine_id=routine_id, title=title[:200], skill=skill,
            params=params, instructions=instructions, status=status, summary="", files={},
            events=[], tokens=0, error="", unread=True, created_at=now()))
        return self.job(user_id, job_id)

    def job(self, user_id: str, job_id: str) -> dict | None:
        return self._one(sa.select(jobs).where((jobs.c.id == job_id) & (jobs.c.user_id == user_id)))

    def job_by_id(self, job_id: str) -> dict | None:
        return self._one(sa.select(jobs).where(jobs.c.id == job_id))

    def jobs_for(self, user_id: str, limit: int = 50) -> list[dict]:
        columns = [c for c in jobs.c if c.name not in ("events", "files")]
        return self._all(sa.select(*columns).where(jobs.c.user_id == user_id)
                         .order_by(jobs.c.created_at.desc()).limit(limit))

    def update_job(self, job_id: str, **values) -> None:
        self._run(jobs.update().where(jobs.c.id == job_id).values(**values))

    def next_queued_job(self) -> dict | None:
        return self._one(sa.select(jobs).where(jobs.c.status == "queued")
                         .order_by(jobs.c.created_at).limit(1))

    def requeue_interrupted(self) -> int:
        """Jobs left running by a restart go back in the queue."""
        return self._run(jobs.update().where(jobs.c.status == "running").values(status="queued"))

    def delete_job(self, user_id: str, job_id: str) -> bool:
        return self._run(jobs.delete().where((jobs.c.id == job_id) & (jobs.c.user_id == user_id))) > 0

    # ---------- uploads ----------

    def add_upload(self, user_id: str, name: str, mime: str, data: bytes) -> dict:
        upload_id = new_id()
        self._run(uploads.insert().values(id=upload_id, user_id=user_id, name=name[:255],
                                          mime=mime[:100], size=len(data), data=data,
                                          created_at=now()))
        return self.upload(user_id, upload_id, with_data=False)

    def upload(self, user_id: str, upload_id: str, with_data: bool = True) -> dict | None:
        columns = list(uploads.c) if with_data else [c for c in uploads.c if c.name != "data"]
        return self._one(sa.select(*columns).where((uploads.c.id == upload_id)
                                                   & (uploads.c.user_id == user_id)))

    def uploads_for(self, user_id: str) -> list[dict]:
        columns = [c for c in uploads.c if c.name != "data"]
        return self._all(sa.select(*columns).where(uploads.c.user_id == user_id)
                         .order_by(uploads.c.created_at.desc()))

    def delete_upload(self, user_id: str, upload_id: str) -> bool:
        return self._run(uploads.delete().where((uploads.c.id == upload_id)
                                                & (uploads.c.user_id == user_id))) > 0

    # ---------- notifications ----------

    def add_push(self, user_id: str, endpoint: str, keys: dict) -> None:
        with self.engine.begin() as c:
            c.execute(push_subs.delete().where(push_subs.c.endpoint == endpoint))
            c.execute(push_subs.insert().values(id=new_id(), user_id=user_id, endpoint=endpoint,
                                                keys=keys, created_at=now()))

    def pushes_for(self, user_id: str) -> list[dict]:
        return self._all(sa.select(push_subs).where(push_subs.c.user_id == user_id))

    def delete_push(self, endpoint: str) -> None:
        self._run(push_subs.delete().where(push_subs.c.endpoint == endpoint))

    # ---------- daily usage ----------

    def add_usage(self, user_id: str, tokens: int, day: str | None = None) -> None:
        day = day or now().date().isoformat()
        with self.engine.begin() as c:
            row = c.execute(sa.select(usage.c.tokens).where((usage.c.day == day)
                                                            & (usage.c.user_id == user_id))).first()
            if row is None:
                c.execute(usage.insert().values(day=day, user_id=user_id, tokens=tokens))
            else:
                c.execute(usage.update().where((usage.c.day == day) & (usage.c.user_id == user_id))
                          .values(tokens=row[0] + tokens))

    def usage_today(self, user_id: str | None = None) -> int:
        day = now().date().isoformat()
        query = sa.select(sa.func.coalesce(sa.func.sum(usage.c.tokens), 0)).where(usage.c.day == day)
        if user_id:
            query = query.where(usage.c.user_id == user_id)
        with self.engine.connect() as c:
            return int(c.execute(query).scalar() or 0)

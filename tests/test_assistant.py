"""Tests for the personal assistant. The model is scripted and the network is replaced, so
nothing here calls a real API or reaches the internet."""
import datetime as dt
import io
import json

import pytest
from fastapi.testclient import TestClient
from test_agent import FakeClient
from test_orchestrator import APPROVE, call

from orchestrator.assistant import concierge, crypto, gather, runner, worker
from orchestrator.assistant import schedule as sched
from orchestrator.assistant.store import Store
from orchestrator.web.app import create_app

KARACHI = "Asia/Karachi"
MODELS = dict.fromkeys(("planner", "coder", "tester", "reviewer"), "fake")
RSS = """<rss><channel>
<item><title>Record tech exports - Dawn</title><link>https://dawn.example/1</link>
<pubDate>Thu, 22 Oct 2026 05:10:00 GMT</pubDate><source>Dawn</source></item>
<item><title>New AI rules - Reuters</title><link>https://reuters.example/2</link>
<pubDate>Thu, 22 Oct 2026 04:00:00 GMT</pubDate><source>Reuters</source></item>
</channel></rss>"""
BRIEF = "# Morning news\n\nRecord tech exports ([Dawn](https://dawn.example/1)).\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Store(f"sqlite:///{tmp_path / 'relay.db'}")


@pytest.fixture
def user(store):
    u = store.upsert_user("aitzaz@example.com", "Aitzaz Khan")
    store.update_user(u["id"], prefs={"city": "Islamabad", "topics": ["technology"]})
    return store.user(u["id"])


@pytest.fixture
def no_network(monkeypatch):
    def fake_get(url, params=None):
        if "news.google.com" in url:
            return RSS
        raise gather.GatherError("no network in tests")
    monkeypatch.setattr(gather, "http_get", fake_get)


def team_script(message="Your news is ready: record tech exports."):
    return [
        call("submit_plan", {"summary": "brief", "files_to_change": ["output.md"], "requirements": [
            {"id": "R1", "description": "one-line summary", "source": "task"}],
            "message": "Builder, keep it short."}),
        call("write_file", {"path": "output.md", "content": BRIEF}),
        call("submit_changes", {"summary": "wrote it", "files_changed": ["output.md"]}),
        call("submit_test_report", {"checks": [{"requirement_id": "R1", "passed": True, "evidence": "ok"}]}),
        call("submit_review", {**APPROVE, "message": message}),
    ]


# ---------- schedules ----------


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=sched.zone(KARACHI))


def test_weekday_mornings_skip_the_weekend():
    friday_evening = at("2026-10-23 20:00")
    nxt = sched.next_run({"time": "07:00", "days": [0, 1, 2, 3, 4]}, KARACHI, friday_evening)
    assert nxt.astimezone(sched.zone(KARACHI)) == at("2026-10-26 07:00")  # Monday
    assert nxt.tzinfo == dt.UTC


def test_same_day_when_the_time_is_still_ahead():
    nxt = sched.next_run({"time": "07:00"}, KARACHI, at("2026-10-22 06:59"))
    assert nxt.astimezone(sched.zone(KARACHI)) == at("2026-10-22 07:00")


def test_one_time_schedules_end():
    assert sched.next_run({"once": "2026-10-22 09:00"}, KARACHI, at("2026-10-22 10:00")) is None
    assert sched.next_run({"once": "2026-10-23 09:00"}, KARACHI, at("2026-10-22 10:00")) is not None


def test_schedules_in_plain_words():
    assert sched.describe({"time": "07:00", "days": [0, 1, 2, 3, 4]}) == "Weekdays at 7:00 AM"
    assert sched.describe({"time": "20:30"}) == "Every day at 8:30 PM"
    assert sched.describe({"time": "18:00", "days": [6]}) == "Every Sunday at 6:00 PM"
    assert sched.describe({"every_hours": 6}) == "Every 6 hours"


@pytest.mark.parametrize("bad", [{"time": "25:00"}, {"time": "07:00", "days": [9]},
                                 {"every_hours": 0}, {"once": "tomorrow"}])
def test_bad_schedules_are_rejected(bad):
    with pytest.raises(ValueError):
        sched.validate(bad)


# ---------- the Concierge ----------


def proposal(**overrides):
    base = {"action": "routine", "skill": "news", "title": "Morning news", "topics": ["technology"],
            "instructions": "A short news briefing.", "schedule": {"time": "07:00", "days": [0, 1, 2, 3, 4]},
            "message": "Here's what I'll set up."}
    return {**base, **overrides}


def test_the_concierge_proposes_a_routine(user):
    client = FakeClient([call("submit_proposal", proposal())])
    p, tokens = concierge.ask("News every weekday at 7", user, {}, [], [], [], client=client, model="fake")
    assert p["action"] == "routine" and p["schedule_text"] == "Weekdays at 7:00 AM"
    assert p["params"]["topics"] == ["technology"] and tokens > 0
    task = client.requests[0]["messages"][-1]["content"]
    assert "Asia/Karachi" in task and "email_digest" in task  # it sees the time and the skills


def test_reminders_always_use_the_reminder_skill():
    p = concierge.normalise(proposal(action="reminder", skill="answer", text="Plan tomorrow",
                                     schedule={"time": "22:00"}), [])
    assert p["skill"] == "reminder" and p["params"]["text"] == "Plan tomorrow"


@pytest.mark.parametrize("raw", [proposal(skill="launch_rockets"), proposal(schedule=None),
                                 proposal(skill="web_watch", url=""), proposal(schedule={"time": "99:00"})])
def test_bad_proposals_are_refused(raw):
    with pytest.raises(concierge.ProposalError):
        concierge.normalise(raw, [])


def test_attached_files_travel_with_the_proposal():
    p = concierge.normalise(proposal(action="job", skill="file_task", schedule=None), ["abc"])
    assert p["params"]["upload_ids"] == ["abc"]


# ---------- gathering ----------


def test_news_comes_from_the_feed(no_network):
    text = gather.news(["technology"], "PK")
    assert "Record tech exports (Dawn" in text and "https://dawn.example/1" in text


def test_private_addresses_are_never_fetched():
    with pytest.raises(gather.GatherError):
        gather.http_get("http://127.0.0.1:8000/api/me")
    with pytest.raises(gather.GatherError):
        gather.http_get("file:///etc/passwd")


def test_weather_alerts_only_when_it_matters():
    calm = {"place": "Islamabad", "days": [{}, {"code": 1, "sky": "clear", "rain_chance": 5, "high": 28,
                                               "low": 15, "wind": 10}]}
    rain = {"place": "Islamabad", "days": [{}, {"code": 63, "sky": "rain", "rain_chance": 80, "high": 22,
                                               "low": 14, "wind": 20}]}
    assert gather.weather_alert(calm) == ""
    assert "rain (80% chance)" in gather.weather_alert(rain)


def test_uploaded_files_are_read_as_text():
    import docx
    document = docx.Document()
    document.add_paragraph("Quiz on Friday at 9 AM.")
    buffer = io.BytesIO()
    document.save(buffer)
    assert "Quiz on Friday" in gather.file_text("notes.docx", "", buffer.getvalue())
    assert gather.file_text("notes.txt", "text/plain", b"hello") == "hello"
    assert "could not be read" in gather.file_text("x.pdf", "application/pdf", b"not a pdf")


def test_a_watched_page_is_quiet_until_it_changes(store, user, monkeypatch):
    monkeypatch.setattr(gather, "http_get", lambda url, params=None: "<p>Price: 100</p>")
    first = gather.gather("web_watch", {"url": "https://shop.example"}, user, store)
    again = gather.gather("web_watch", {"url": "https://shop.example"}, user, store, first.state)
    monkeypatch.setattr(gather, "http_get", lambda url, params=None: "<p>Price: 90</p>")
    changed = gather.gather("web_watch", {"url": "https://shop.example"}, user, store, first.state)
    assert not first.nothing_new and again.nothing_new and not changed.nothing_new
    assert "page_before.md" in changed.files


def test_missing_connections_become_notes(store, user):
    out = gather.gather("email_digest", {"days": 1}, user, store)
    assert out.files == {} and "Gmail is not connected" in out.notes[0]


# ---------- running jobs ----------


def test_a_job_runs_the_team_and_keeps_only_the_result(store, user, no_network, monkeypatch):
    sent = []
    monkeypatch.setattr(runner.push, "send", lambda *a, **k: sent.append((a, k)))
    job = store.add_job(user["id"], "Morning news", "news", {"topics": ["technology"]}, "A short brief.")
    done = runner.run_job(store, job, client=FakeClient(team_script()), models=MODELS, sleep=lambda s: None)
    assert done["status"] == "done"
    assert done["files"] == {"output.md": BRIEF}  # inputs/ is not part of the result
    assert done["summary"] == "Your news is ready: record tech exports."
    assert any(e["type"] == "agent_submitted" for e in done["events"])
    assert store.usage_today(user["id"]) == done["tokens"] > 0
    assert sent and sent[0][0][2] == "Morning news"


def test_the_team_sees_the_gathered_material(store, user, no_network):
    client = FakeClient(team_script())
    job = store.add_job(user["id"], "News", "news", {"topics": ["technology"]}, "A short brief.")
    runner.run_job(store, job, client=client, models=MODELS, sleep=lambda s: None)
    first = client.requests[0]["messages"][-1]["content"]
    assert "inputs/" in first and "news.md" in first


def test_reminders_need_no_ai(store, user, monkeypatch):
    monkeypatch.setattr(runner.push, "send", lambda *a, **k: 1)
    job = store.add_job(user["id"], "Plan tomorrow", "reminder", {"text": "Plan tomorrow"}, "")
    assert runner.run_job(store, job, client=None)["status"] == "done"


def test_jobs_are_skipped_when_the_allowance_is_used(store, user, no_network, monkeypatch):
    monkeypatch.setenv("ASSISTANT_USER_DAILY_TOKENS", "10")
    store.add_usage(user["id"], 50)
    job = store.add_job(user["id"], "News", "news", {}, "brief")
    assert runner.run_job(store, job, client=FakeClient([]))["status"] == "skipped"


def test_due_routines_become_jobs_and_move_on(store, user, monkeypatch):
    monkeypatch.setattr(worker.push, "send", lambda *a, **k: 1)
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    news = store.add_routine(user["id"], "News", "news", {}, "brief", {"time": "07:00"}, past)
    store.add_routine(user["id"], "Plan", "reminder", {"text": "Plan tomorrow"}, "", {"time": "22:00"}, past)
    queued = worker.start_due_routines(store)
    assert [j["title"] for j in queued] == ["News"]
    assert store.routine(user["id"], news["id"])["next_run_at"] > dt.datetime.now(dt.UTC)
    reminder_jobs = [j for j in store.jobs_for(user["id"]) if j["skill"] == "reminder"]
    assert reminder_jobs[0]["status"] == "done" and reminder_jobs[0]["summary"] == "Plan tomorrow"
    assert worker.start_due_routines(store) == []  # nothing is due twice


def test_tokens_are_encrypted_at_rest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the local key file is created here, not in the project
    secret = crypto.encrypt({"refresh_token": "abc"})
    assert "abc" not in secret and crypto.decrypt(secret) == {"refresh_token": "abc"}
    assert crypto.decrypt("garbage") is None


# ---------- the web app ----------

HDR = {"X-Relay": "1"}


@pytest.fixture
def app_client(store, monkeypatch, no_network):
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setattr("orchestrator.web.app.load_dotenv", lambda: None)
    fake = FakeClient([])  # the worker keeps one client, so tests add to its script
    app = create_app(store=store, start_worker=False, public=False, models=MODELS,
                     client_factory=lambda: fake)
    return TestClient(app), app, fake.script


def test_local_mode_signs_you_in(app_client):
    client, _, _ = app_client
    me = client.get("/api/me").json()
    assert me["signed_in"] and me["local"] and me["ai_ready"]
    assert any(s["skill"] == "news" for s in me["suggestions"])


def test_changes_need_the_relay_header(app_client):
    client, _, _ = app_client
    assert client.post("/api/ask", json={"message": "hi"}).status_code == 403


def test_ask_confirm_and_run_a_routine(app_client):
    client, app, script = app_client
    script.append(call("submit_proposal", proposal()))
    p = client.post("/api/ask", json={"message": "News on weekdays at 7"}, headers=HDR).json()
    assert p["action"] == "routine"
    made = client.post("/api/plans", json={"proposal": p}, headers=HDR).json()
    assert made["kind"] == "routine" and made["routine"]["schedule_text"] == "Weekdays at 7:00 AM"
    routine_id = made["routine"]["id"]
    assert client.patch(f"/api/routines/{routine_id}", json={"enabled": False}, headers=HDR).json()["enabled"] is False

    script.extend(team_script())
    job = client.post(f"/api/routines/{routine_id}/run", headers=HDR).json()["job"]
    app.state.worker.run_once()
    view = client.get(f"/api/jobs/{job['id']}").json()
    assert view["status"] == "done" and "output.md" in view["files"] and view["unread"]
    assert client.get(f"/api/jobs/{job['id']}/files/output.md").text == BRIEF
    events = [json.loads(line[6:]) for line in client.get(f"/api/jobs/{job['id']}/events").text.splitlines()
              if line.startswith("data: {\"type")]
    assert events[0]["type"] == "run_started"
    client.post(f"/api/jobs/{job['id']}/read", headers=HDR)
    assert client.get("/api/jobs").json()[0]["unread"] is False


def test_people_only_see_their_own_things(app_client, store):
    client, _, _ = app_client
    other = store.upsert_user("someone@example.com")
    theirs = store.add_job(other["id"], "Private", "answer", {}, "secret")
    assert client.get(f"/api/jobs/{theirs['id']}").status_code == 404
    assert client.delete(f"/api/jobs/{theirs['id']}", headers=HDR).status_code == 404


def test_uploads(app_client):
    client, _, _ = app_client
    ok = client.post("/api/uploads", files={"file": ("notes.txt", b"Exam on Monday", "text/plain")}, headers=HDR)
    assert ok.status_code == 200 and ok.json()["size"] == 14
    bad = client.post("/api/uploads", files={"file": ("virus.exe", b"MZ", "application/octet-stream")}, headers=HDR)
    assert bad.status_code == 400
    assert [f["name"] for f in client.get("/api/uploads").json()] == ["notes.txt"]
    assert client.delete(f"/api/uploads/{ok.json()['id']}", headers=HDR).status_code == 200


def test_push_subscriptions_are_checked(app_client):
    client, _, _ = app_client
    bad = client.post("/api/push/subscribe", json={"endpoint": "http://evil", "keys": {}}, headers=HDR)
    assert bad.status_code == 400
    good = client.post("/api/push/subscribe", headers=HDR,
                       json={"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}})
    assert good.status_code == 200 and client.get("/api/me").json()["push_devices"] == 1


def test_pages(app_client):
    client, _, _ = app_client
    assert "What can the team do" in client.get("/").text
    assert "RELAY_EMBED = true" in client.get("/office").text
    assert "self.addEventListener" in client.get("/sw.js").text
    assert client.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert client.get("/icons/icon-192.png").status_code == 200
    assert client.get("/icons/../app.py").status_code == 404


def test_public_mode_needs_sign_in(store, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("SANDBOX", raising=False)
    monkeypatch.delenv("RELAY_GUARD_DENY", raising=False)
    monkeypatch.setattr("orchestrator.web.app.load_dotenv", lambda: None)
    client = TestClient(create_app(store=store, start_worker=False, public=True))
    assert client.get("/api/me").json()["signed_in"] is False
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/auth/login", follow_redirects=False).status_code == 503
    assert client.post("/api/runs", json={"prompt": "x", "kind": "create"}).status_code == 401
    assert client.get("/office", follow_redirects=False).status_code == 303


def test_google_sign_in_and_gmail_connection(store, monkeypatch):
    from orchestrator.assistant import google
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ALLOWED_EMAILS", "aitzaz@example.com")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("orchestrator.web.app.load_dotenv", lambda: None)
    who = {"email": "aitzaz@example.com"}
    monkeypatch.setattr(google, "exchange_code", lambda code: {"access_token": "a", "refresh_token": "r", "expires_at": 9e12})
    monkeypatch.setattr(google, "userinfo", lambda token: {**who, "email_verified": True, "name": "Aitzaz"})
    client = TestClient(create_app(store=store, start_worker=False, public=False))

    def through_google(path):
        start = client.get(path, follow_redirects=False)
        state = start.headers["location"].split("state=")[1].split("&")[0]
        return client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False).headers["location"]

    who["email"] = "stranger@example.com"
    assert through_google("/auth/login") == "/?signin=not-invited"
    who["email"] = "aitzaz@example.com"
    assert through_google("/auth/login") == "/"
    assert client.get("/api/me").json()["user"]["name"] == "Aitzaz"
    assert through_google("/auth/connect/gmail") == "/?connected=gmail"
    assert client.get("/api/me").json()["connections"] == {"gmail": "ok"}
    stored = store.connection(store.upsert_user("aitzaz@example.com")["id"], "gmail")["token"]
    assert "r" not in crypto.decrypt(stored)["access_token"] and '"r"' not in stored  # encrypted
    assert client.get("/auth/callback?code=c&state=forged", follow_redirects=False).headers["location"] == "/?signin=cancelled"


def test_invite_codes_sign_people_in(store, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("SANDBOX", raising=False)
    monkeypatch.delenv("RELAY_GUARD_DENY", raising=False)
    monkeypatch.setenv("INVITES", "aitzaz@example.com=ABCD-EFGH-JKLM")
    monkeypatch.setattr("orchestrator.web.app.load_dotenv", lambda: None)
    client = TestClient(create_app(store=store, start_worker=False, public=True), base_url="https://testserver")
    assert client.get("/api/me").json()["invites"] is True
    wrong = client.post("/auth/invite", json={"email": "aitzaz@example.com", "code": "nope"}, headers=HDR)
    assert wrong.status_code == 401
    ok = client.post("/auth/invite", json={"email": "Aitzaz@Example.com ", "code": "ABCD-EFGH-JKLM"}, headers=HDR)
    assert ok.status_code == 200 and client.get("/api/me").json()["signed_in"] is True
    for _ in range(8):
        client.post("/auth/invite", json={"email": "x@example.com", "code": "guess-guess"}, headers=HDR)
    assert client.post("/auth/invite", json={"email": "x@example.com", "code": "guess"}, headers=HDR).status_code == 429


def test_the_server_keeps_itself_awake(store, monkeypatch):
    pings = []
    monkeypatch.setenv("KEEP_AWAKE_URL", "https://relay.example/")
    monkeypatch.setattr(worker.httpx, "get", lambda url, timeout: pings.append(url))
    w = worker.Worker(store)
    w.keep_awake()
    w.keep_awake()  # not again within 10 minutes
    assert pings == ["https://relay.example/healthz"]

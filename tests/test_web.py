"""Tests for the Relay web app. The model is a scripted fake; nothing calls a real API."""
import json
import re
import threading
from functools import partial

import pytest
from fastapi.testclient import TestClient
from test_agent import FakeClient
from test_orchestrator import create_script

from orchestrator.orchestrator import run_orchestrator
from orchestrator.web.app import REPLAYS_DIR, create_app

FAKE_MODELS = dict.fromkeys(("planner", "coder", "tester", "reviewer"), "fake")


def scripted_runner(script, gate=None):
    def runner(prompt, workspace, **kwargs):
        if gate is not None:
            gate.wait(5)
        return run_orchestrator(prompt, workspace, client=FakeClient(script()), models=FAKE_MODELS,
                                sleep=lambda s: None, **kwargs)
    return runner


def read_events(client, run_id):
    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        for line in response.iter_lines():
            if line.startswith("event: end"):
                break
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    return partial(create_app, runs_dir=tmp_path / "web")


def test_page_and_status(app_factory):
    client = TestClient(app_factory(runner=scripted_runner(create_script)))
    assert "Relay" in client.get("/").text
    status = client.get("/api/status").json()
    assert status["api_key_configured"] is True
    assert status["active_run"] is None


def test_examples_include_the_landing_page_prompt_and_fix_samples(app_factory):
    client = TestClient(app_factory(runner=scripted_runner(create_script)))
    examples = client.get("/api/examples").json()
    assert any("EXACTLY THREE PARAGRAPHS" in e["prompt"] for e in examples["create"])
    assert any(s["name"] == "hard_graph" for s in examples["fix"])


def test_live_create_run_streams_the_whole_conversation(app_factory, tmp_path):
    client = TestClient(app_factory(runner=scripted_runner(create_script)))
    run_id = client.post("/api/runs", json={"prompt": "Write three paragraphs.", "kind": "create"}).json()["id"]
    events = read_events(client, run_id)
    types = [e["type"] for e in events]
    assert types[0] == "run_started" and types[-1] == "run_finished"
    assert events[-1]["status"] == "completed"
    assert events[-1]["files"] == {"output.md": "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n"}
    # every event was also saved, so the run can become a replay later
    saved = (tmp_path / "web" / run_id / "events.jsonl").read_text().splitlines()
    assert len(saved) == len(events)


def test_only_one_live_run_at_a_time(app_factory):
    gate = threading.Event()
    client = TestClient(app_factory(runner=scripted_runner(create_script, gate)))
    first = client.post("/api/runs", json={"prompt": "one", "kind": "create"})
    second = client.post("/api/runs", json={"prompt": "two", "kind": "create"})
    assert first.status_code == 200
    assert second.status_code == 409
    gate.set()
    read_events(client, first.json()["id"])


def test_fix_run_needs_a_known_sample(app_factory):
    client = TestClient(app_factory(runner=scripted_runner(create_script)))
    response = client.post("/api/runs", json={"prompt": "fix it", "kind": "fix", "sample": "nope"})
    assert response.status_code == 400


def test_invalid_requests_are_rejected(app_factory):
    client = TestClient(app_factory(runner=scripted_runner(create_script)))
    assert client.post("/api/runs", json={"prompt": "", "kind": "create"}).status_code == 422
    assert client.post("/api/runs", json={"prompt": "x" * 9000, "kind": "create"}).status_code == 422
    assert client.post("/api/runs", json={"prompt": "x", "kind": "delete"}).status_code == 422


def test_runner_crash_is_reported_as_an_event(app_factory):
    def broken(prompt, workspace, **kwargs):
        raise RuntimeError("No API key found.")
    client = TestClient(app_factory(runner=broken))
    run_id = client.post("/api/runs", json={"prompt": "x", "kind": "create"}).json()["id"]
    events = read_events(client, run_id)
    assert events[-1]["type"] == "run_finished" and "No API key" in events[-1]["error"]


def test_bundled_replays_play_back(app_factory):
    client = TestClient(app_factory(runner=scripted_runner(create_script), replay_speed=1000))
    replays = client.get("/api/replays").json()
    assert len(replays) >= 3
    run_id = client.post(f"/api/replays/{replays[0]['name']}").json()["id"]
    events = read_events(client, run_id)
    assert events[0]["type"] == "run_started" and events[-1]["type"] == "run_finished"
    assert all(e.get("replay") for e in events)


def test_replay_names_cannot_escape_the_folder(app_factory):
    client = TestClient(app_factory(runner=scripted_runner(create_script)))
    assert client.post("/api/replays/..%2F..%2Fsecrets").status_code == 404
    assert client.post("/api/replays/does_not_exist").status_code == 404


def test_bundled_replays_contain_no_personal_paths():
    """Recordings must not reveal a user's name through paths like C:\\Users\\<name>."""
    user_path = re.compile(r"Users(?:\\\\|\\|/)+(?!you\b)[A-Za-z0-9_.-]+", re.IGNORECASE)
    for path in REPLAYS_DIR.glob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        assert not user_path.search(text) and "AppData" not in text, path.name


# ---------- the page ----------


def test_page_embeds_the_recordings_safely():
    from orchestrator.web.site import _script_json, load_recordings, render_fragment, render_page

    page = render_page(live=False)
    assert page.startswith("<!doctype html>") and "window.RELAY_LIVE = false" in page
    assert len(load_recordings()) >= 3 and '"events":[' in page
    assert "<html" not in render_fragment()
    assert "</script>" not in _script_json({"text": "</script><script>alert(1)</script>"})


def _public_client(tmp_path, monkeypatch, made=None):
    from orchestrator.assistant.store import Store

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "server-key")
    monkeypatch.setenv("INVITES", "me@example.com=ABCD-EFGH-JKLM")
    monkeypatch.delenv("SANDBOX", raising=False)
    monkeypatch.delenv("RELAY_GUARD_DENY", raising=False)
    monkeypatch.setattr("orchestrator.web.app.load_dotenv", lambda: None)
    made = [] if made is None else made
    app = create_app(public=True, start_worker=False, store=Store(f"sqlite:///{tmp_path}/r.db"),
                     client_factory=lambda: made.append(1) or object())
    return TestClient(app, base_url="https://testserver")  # the session cookie is https-only


def _sign_in(client):
    r = client.post("/auth/invite", json={"email": "me@example.com", "code": "ABCD-EFGH-JKLM"},
                    headers={"X-Relay": "1"})
    assert r.status_code == 200


def test_public_mode_hides_the_key_and_closes_the_playground(tmp_path, monkeypatch):
    import os

    made = []
    client = _public_client(tmp_path, monkeypatch, made)
    assert "GROQ_API_KEY" not in os.environ and made == [1]  # read once, then removed
    _sign_in(client)
    status = client.get("/api/status").json()
    assert status["public"] and status["api_key_configured"] and status["sandbox"] == "guarded"
    assert client.post("/api/runs", json={"prompt": "x", "kind": "create"}).status_code == 404


def test_public_mode_shows_nothing_but_sign_in_to_strangers(tmp_path, monkeypatch):
    client = _public_client(tmp_path, monkeypatch)
    for path in ("/demo", "/office"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", path
    for method, path in (("GET", "/api/status"), ("GET", "/api/examples"), ("GET", "/api/replays"),
                         ("POST", "/api/replays/fix_word_stats"), ("GET", "/api/runs/abc/events"),
                         ("GET", "/api/demo-recordings"), ("GET", "/api/jobs"), ("GET", "/api/routines"),
                         ("GET", "/api/uploads")):
        assert client.request(method, path, headers={"X-Relay": "1"}).status_code == 401, path
    assert client.get("/static/relay.html").status_code == 404
    assert client.get("/api/me").json()["signed_in"] is False
    assert client.get("/healthz").json() == {"ok": True}

    _sign_in(client)
    assert client.get("/office").status_code == 200 and client.get("/demo").status_code == 200
    assert client.get("/api/replays").status_code == 200
    assert client.get("/api/demo-recordings").status_code == 200


def test_the_sign_in_page_links_nowhere_past_sign_in():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "src/orchestrator/web/static/app.html").read_text(encoding="utf-8")
    assert '"/demo"' not in page and '"/office"' not in page.replace('src = "/office"', "").replace('src: "/office"', "")

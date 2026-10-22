"""Tests for the deploy script's own logic (nothing here talks to Render)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import deploy


def test_keys_are_checked_with_helpful_messages():
    good = {"RENDER_API_KEY": "rnd_x", "GROQ_API_KEY": "gsk_x", "DATABASE_URL": "postgresql://u:p@h/db",
            "EMAILS": "me@gmail.com, friend@gmail.com"}
    assert deploy.check_keys(good) == []
    problems = deploy.check_keys({**good, "RENDER_API_KEY": "", "EMAILS": "not-an-email"})
    assert len(problems) == 2 and "rnd_" in problems[0]


def test_secrets_are_made_once_and_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "GENERATED", tmp_path / "generated.env")
    first = deploy.generated_secrets(["me@gmail.com"])
    again = deploy.generated_secrets(["me@gmail.com", "friend@gmail.com"])
    assert first["RELAY_SECRET_KEY"] == again["RELAY_SECRET_KEY"]
    codes = dict(item.split("=") for item in again["INVITES"].split(","))
    assert codes["me@gmail.com"] == dict([first["INVITES"].split("=")])["me@gmail.com"]
    assert len(codes["friend@gmail.com"]) == 14  # XXXX-XXXX-XXXX


def test_env_files_round_trip(tmp_path):
    path = tmp_path / "x.env"
    deploy.write_env(path, {"A": "1", "B": "two=2"}, "# header")
    assert deploy.read_env(path) == {"A": "1", "B": "two=2"}


def test_render_calls(monkeypatch):
    calls = []

    def fake_call(self, method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("/services?"):
            return []
        if path.startswith("/owners"):
            return [{"owner": {"id": "tea-1"}}]
        if path == "/services":
            return {"service": {"id": "srv-1", "serviceDetails": {"url": "https://relay.onrender.com"}}}
        return {}

    monkeypatch.setattr(deploy.Render, "call", fake_call)
    render = deploy.Render("rnd_x")
    assert render.find_service("relay") is None
    service = render.create_service(render.owner_id(), "relay", "https://github.com/me/repo", {"A": "1"})
    assert service["id"] == "srv-1"
    body = calls[-1][2]
    assert body["serviceDetails"]["plan"] == "free" and body["serviceDetails"]["runtime"] == "docker"
    assert body["envVars"] == [{"key": "A", "value": "1"}]

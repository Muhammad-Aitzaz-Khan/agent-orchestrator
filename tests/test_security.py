"""The free tries ("Try it out") and the protections around the public site.

Each test is one promise from docs/security-review.md, checked the way an attacker or a curious
visitor would poke at it.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from orchestrator.assistant import trial
from orchestrator.assistant.store import Store, now
from orchestrator.web.app import create_app

HDR = {"X-Relay": "1"}
INVITE = "me@example.com=ABCD-EFGH-JKLM"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Store(f"sqlite:///{tmp_path / 'relay.db'}")


@pytest.fixture
def site(store, monkeypatch):
    """A public server, as on Render, with invites and free tries switched on."""
    for name in ("GOOGLE_CLIENT_ID", "SANDBOX", "RELAY_GUARD_DENY", "TRIALS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "server-key")
    monkeypatch.setenv("INVITES", INVITE)
    monkeypatch.setattr("orchestrator.web.app.load_dotenv", lambda: None)
    app = create_app(store=store, start_worker=False, public=True, client_factory=lambda: object())

    def visitor(ip="203.0.113.7"):
        # Render's proxy adds the real address as the last X-Forwarded-For entry
        return TestClient(app, base_url="https://testserver", headers={"X-Forwarded-For": ip})
    return visitor


def start_trial(client):
    return client.post("/auth/trial", json={}, headers=HDR)


# ---------- the free tries ----------


def test_a_visitor_can_try_relay_without_signing_up(site):
    client = site()
    assert client.get("/api/me").json()["trial"] is True
    r = start_trial(client)
    assert r.status_code == 200 and r.cookies.get(trial.BROWSER_COOKIE)
    me = client.get("/api/me").json()
    assert me["signed_in"] and me["guest"] and me["google"] is False
    ends = dt.datetime.fromisoformat(me["trial_until"])
    assert dt.timedelta(hours=23) < ends - now() <= dt.timedelta(hours=24)
    assert client.get("/office", follow_redirects=False).status_code == 200


def test_one_free_day_per_browser(site):
    client = site()
    assert start_trial(client).status_code == 200
    out = client.post("/auth/logout", headers=HDR)
    assert "cookies" not in out.headers["clear-site-data"]  # signing out must not erase the memory
    again = start_trial(client)  # same browser: the cookie remembers
    assert again.status_code == 403 and "already had its free tries" in again.json()["detail"]


def test_a_network_gets_a_few_free_days_not_endless_ones(site):
    for _ in range(3):  # e.g. three friends on the same campus Wi-Fi
        assert start_trial(site("198.51.100.4")).status_code == 200
    fourth = start_trial(site("198.51.100.4"))
    assert fourth.status_code == 403 and "network" in fourth.json()["detail"]
    assert start_trial(site("198.51.100.99")).status_code == 200  # another network is fine


def test_a_forged_address_does_not_help(site):
    """Earlier X-Forwarded-For entries come from the client; only the proxy's last one counts."""
    for n in range(3):
        assert start_trial(site(f"10.0.0.{n}, 198.51.100.4")).status_code == 200
    assert start_trial(site("10.9.9.9, 198.51.100.4")).status_code == 403


def test_bots_that_fill_the_hidden_field_get_nothing(site):
    r = site().post("/auth/trial", json={"website": "https://spam.example"}, headers=HDR)
    assert r.status_code == 400


def test_free_days_can_be_switched_off(site, monkeypatch):
    monkeypatch.setenv("TRIALS", "off")
    assert start_trial(site()).status_code == 404


def test_when_the_day_ends_the_guest_is_signed_out_and_later_deleted(site, store):
    client = site()
    start_trial(client)
    guest = next(iter(store.guests(trial.GUEST_DOMAIN)))
    store.add_job(guest["id"], "News", "news", {}, "x")
    store.update_user(guest["id"], prefs={"trial_until": (now() - dt.timedelta(minutes=5)).isoformat()})
    me = client.get("/api/me").json()
    assert me["signed_in"] is False and me["trial_over"] is True
    assert client.get("/api/jobs", headers=HDR).status_code == 401
    assert client.get("/office", follow_redirects=False).status_code == 303
    assert trial.clean_up(store, grace=dt.timedelta(0)) == 1
    assert store.user(guest["id"]) is None and store.jobs_for(guest["id"]) == []


def test_guests_cannot_connect_google_or_use_more_than_their_share(site, store, monkeypatch):
    from orchestrator.assistant.runner import allowance_left
    client = site()
    start_trial(client)
    assert client.get("/auth/connect/gmail", follow_redirects=False).status_code == 403
    guest = next(iter(store.guests(trial.GUEST_DOMAIN)))
    assert allowance_left(store, guest["id"])
    store.add_usage(guest["id"], 60_000)
    assert not allowance_left(store, guest["id"])  # the guest's own allowance
    member = store.upsert_user("me@example.com")
    assert allowance_left(store, member["id"])  # members are untouched


# ---------- the protective layer ----------


def test_pages_carry_protective_headers(site):
    r = site().get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self' 'sha256-" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'self'" in csp and "object-src 'none'" in csp
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "camera=()" in r.headers["permissions-policy"]
    assert r.headers["x-permitted-cross-domain-policies"] == "none"
    assert site().get("/api/me").headers["cache-control"] == "no-store"
    assert site().get("/healthz").headers["x-content-type-options"] == "nosniff"


def test_the_csp_lists_exactly_the_pages_own_scripts(site):
    from orchestrator.web.security import script_hashes
    r = site().get("/")
    hashes = script_hashes(r.content)
    assert hashes and all(h in r.headers["content-security-policy"] for h in hashes)


def test_no_map_of_the_api_is_published(site):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert site().get(path).status_code == 404


def test_oversized_requests_are_refused(site):
    big = b"x" * (12 * 1024 * 1024)
    r = site().post("/api/uploads", content=big, headers={**HDR, "Content-Type": "application/octet-stream"})
    assert r.status_code == 413


def test_sign_in_guessing_is_throttled_even_with_forged_addresses(site):
    for n in range(8):
        site(f"10.0.0.{n}, 192.0.2.50").post("/auth/invite", json={"email": "me@example.com", "code": "WRONG-CODE-0"},
                                              headers=HDR)
    r = site("10.1.1.1, 192.0.2.50").post("/auth/invite", json={"email": "me@example.com", "code": "ABCD-EFGH-JKLM"},
                                          headers=HDR)
    assert r.status_code == 429


def test_wrong_email_and_wrong_code_look_the_same(site):
    a = site("192.0.2.1").post("/auth/invite", json={"email": "nobody@example.com", "code": "x" * 12}, headers=HDR)
    b = site("192.0.2.2").post("/auth/invite", json={"email": "me@example.com", "code": "x" * 12}, headers=HDR)
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_state_changes_need_the_same_site_header(site):
    client = site()
    for path in ("/auth/trial", "/auth/invite", "/api/ask", "/api/plans", "/auth/logout"):
        assert client.post(path, json={}).status_code in (403, 422), path


def test_nobody_can_see_another_persons_things(site, store):
    alice, bob = store.upsert_user("alice@example.com"), store.upsert_user("bob@example.com")
    job = store.add_job(alice["id"], "Alice's news", "news", {}, "x")
    routine = store.add_routine(alice["id"], "Alice's routine", "news", {}, "x", {"time": "07:00"}, now())
    upload = store.add_upload(alice["id"], "notes.txt", "text/plain", b"secret")
    client = site()
    start_trial(client)  # signed in as somebody else entirely
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert client.delete(f"/api/jobs/{job['id']}", headers=HDR).status_code == 404
    assert client.patch(f"/api/routines/{routine['id']}", json={"enabled": False}, headers=HDR).status_code == 404
    assert client.delete(f"/api/uploads/{upload['id']}", headers=HDR).status_code == 404
    assert store.job(alice["id"], job["id"]) and store.upload(alice["id"], upload["id"])
    assert bob  # untouched


def test_download_names_cannot_break_out_of_the_header():
    from orchestrator.web.assistant_api import _safe_filename
    assert _safe_filename('evil"\r\nSet-Cookie: x=1.md') == "evil___Set-Cookie_ x_1.md"
    assert _safe_filename("../../etc/passwd") == "passwd"
    assert _safe_filename('"') == "result.txt"


def test_a_hostile_news_feed_cannot_blow_up_the_parser(monkeypatch):
    from orchestrator.assistant import gather
    bomb = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><rss><channel><item><title>&c;</title></item></channel></rss>')
    monkeypatch.setattr(gather, "http_get", lambda url, params=None: bomb)
    out = gather.news(["technology"])
    assert "Could not load news" in out and "aaaaaaaaaa" not in out


def test_a_guest_gets_three_free_tries(site, store):
    """Relay runs on free AI models, so a guest can hand the crew a few jobs, not endless ones."""
    client = site()
    start_trial(client)
    guest = next(iter(store.guests(trial.GUEST_DOMAIN)))
    assert client.get("/api/me").json()["tries_left"] == 3
    news = {"action": "job", "skill": "news", "title": "Tech news", "params": {"topics": ["technology"]}}
    for _ in range(3):
        assert client.post("/api/plans", json={"proposal": news}, headers=HDR).status_code == 200
    assert client.get("/api/me").json()["tries_left"] == 0
    refused = client.post("/api/plans", json={"proposal": news}, headers=HDR)
    assert refused.status_code == 403 and "last free try" in refused.json()["detail"]
    assert client.post("/api/ask", json={"message": "more news"}, headers=HDR).status_code == 403
    # reminders don't use the AI, so they never count
    reminder = {"action": "job", "skill": "reminder", "title": "Pay the bill", "params": {"text": "Pay the bill"}}
    assert client.post("/api/plans", json={"proposal": reminder}, headers=HDR).status_code == 200
    # and members are never counted
    member = store.upsert_user("me@example.com")
    assert trial.tries_left(store, guest) == 0 and not trial.is_guest(member)

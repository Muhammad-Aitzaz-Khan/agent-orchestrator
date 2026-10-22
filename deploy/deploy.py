"""Put Relay online in one command: python deploy/deploy.py

Relay runs on Render's free web service (no card), with its data in a free Neon database.
The first run creates deploy/keys.env for you to fill in. The next run:

    - creates your secrets (session key, notification keys, an invite code per person)
    - creates (or updates) the Render service from your GitHub repository
    - stores every secret in the service's settings, never in the code
    - turns on a keep-awake ping so scheduled routines run on time
    - prints your site's address and everyone's invite code

keys.env and generated.env stay on your computer; both are in .gitignore. Run it again any
time after pushing new code: secrets are kept, so nobody has to sign in again.
"""
import argparse
import json
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "deploy" / "keys.env"
GENERATED = ROOT / "deploy" / "generated.env"
WORKFLOW = ROOT / ".github" / "workflows" / "keep-awake.yml"
RENDER_API = "https://api.render.com/v1"

KEYS_TEMPLATE = """# Fill in the first four values, save, then run:  python deploy/deploy.py
# This file never leaves your computer (it is in .gitignore).

# 1. Render (sign up with GitHub at render.com): Account Settings > API Keys > Create API Key
RENDER_API_KEY=

# 2. Groq, from an account with NO card added: console.groq.com/keys > Create API Key
GROQ_API_KEY=

# 3. Neon: neon.tech > your project > Connect > copy the connection string (postgresql://...)
DATABASE_URL=

# 4. Who may use your Relay: your email first, then friends, separated by commas
EMAILS=

# Optional: the service's name (your site becomes https://<name>.onrender.com if it is free)
SERVICE_NAME=relay

# Optional, later: Google sign-in, Gmail, and Calendar (docs/DEPLOY.md, "Gmail and Calendar")
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
"""

WORKFLOW_TEMPLATE = """# Backup wake-up call. The server pings itself every 10 minutes to stay awake on Render's
# free plan; if it was ever stopped, this wakes it within 2 hours. Written by deploy/deploy.py.
name: Keep Relay awake
on:
  schedule:
    - cron: "23 */2 * * *"
  workflow_dispatch:
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: curl -fsS --retry 5 --retry-delay 20 --max-time 120 "{url}/healthz"
"""


class DeployError(Exception):
    pass


# ---------- local files ----------


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(path: Path, values: dict[str, str], header: str) -> None:
    lines = [header] + [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


def check_keys(keys: dict[str, str]) -> list[str]:
    problems = []
    if not keys.get("RENDER_API_KEY", "").startswith("rnd_"):
        problems.append("RENDER_API_KEY should start with rnd_ (render.com > Account Settings > API Keys).")
    if not keys.get("GROQ_API_KEY", "").startswith("gsk_"):
        problems.append("GROQ_API_KEY should start with gsk_ (from console.groq.com/keys).")
    if not re.match(r"^postgres(ql)?://", keys.get("DATABASE_URL", "")):
        problems.append("DATABASE_URL should start with postgresql:// (Neon's connection string).")
    emails = [e for e in keys.get("EMAILS", "").replace(" ", "").split(",") if e]
    if not emails or not all(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e) for e in emails):
        problems.append("EMAILS should list at least your own email, separated by commas.")
    return problems


def generated_secrets(emails: list[str]) -> dict[str, str]:
    """Created once and reused, so redeploying never signs anyone out."""
    sys.path.insert(0, str(ROOT / "src"))
    from orchestrator.assistant.push import generate_keys

    gen = read_env(GENERATED)
    if not gen.get("RELAY_SECRET_KEY"):
        gen["RELAY_SECRET_KEY"] = secrets.token_urlsafe(48)
    if not gen.get("VAPID_PRIVATE_KEY"):
        gen["VAPID_PUBLIC_KEY"], gen["VAPID_PRIVATE_KEY"] = generate_keys()
    codes = dict(item.split("=", 1) for item in gen.get("INVITES", "").split(",") if "=" in item)
    for email in emails:
        codes.setdefault(email.lower(), invite_code())
    gen["INVITES"] = ",".join(f"{e}={c}" for e, c in codes.items() if e in {x.lower() for x in emails})
    write_env(GENERATED, gen, "# Made by deploy/deploy.py. Keep it: redeploying reuses these. Never commit it.")
    return gen


def github_repo() -> str:
    """https://github.com/<owner>/<repo> from the git remote, and a check that it is pushed."""
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True,
                         text=True, check=False).stdout.strip()
    match = re.search(r"github\.com[:/](.+?)(\.git)?$", url)
    if not match:
        raise DeployError("This folder's git remote is not a GitHub repository.")
    subprocess.run(["git", "fetch", "-q"], cwd=ROOT, check=False)
    status = subprocess.run(["git", "status", "-sb"], cwd=ROOT, capture_output=True, text=True,
                            check=False).stdout.splitlines()[0]
    if "ahead" in status:
        raise DeployError("You have commits that are not on GitHub yet. Push them first "
                          "(VS Code: Source Control > Sync Changes), then run this again.")
    return f"https://github.com/{match.group(1)}"


# ---------- Render ----------


class Render:
    def __init__(self, key: str):
        self.key = key

    def call(self, method: str, path: str, body=None):
        request = urllib.request.Request(
            f"{RENDER_API}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 401:
                raise DeployError("Render did not accept RENDER_API_KEY. Create a new one and paste it again.") from e
            raise DeployError(f"Render answered {e.code} to {method} {path}: {detail}") from e

    def owner_id(self) -> str:
        owners = self.call("GET", "/owners?limit=20")
        if not owners:
            raise DeployError("Your Render account has no workspace yet. Open render.com once to finish signing up.")
        return owners[0]["owner"]["id"]

    def find_service(self, name: str) -> dict | None:
        for item in self.call("GET", f"/services?name={name}&limit=20"):
            service = item.get("service", item)
            if service.get("name") == name:
                return service
        return None

    def create_service(self, owner: str, name: str, repo: str, env: dict[str, str]) -> dict:
        body = {
            "type": "web_service", "name": name, "ownerId": owner, "repo": repo, "branch": "main",
            "autoDeploy": "yes",
            "envVars": [{"key": k, "value": v} for k, v in env.items()],
            "serviceDetails": {
                "runtime": "docker", "plan": "free", "region": "frankfurt",
                "healthCheckPath": "/healthz",
                "envSpecificDetails": {"dockerfilePath": "./deploy/render/Dockerfile", "dockerContext": "."},
            },
        }
        created = self.call("POST", "/services", body)
        return created.get("service", created)

    def set_env(self, service_id: str, env: dict[str, str]) -> None:
        for key, value in env.items():
            self.call("PUT", f"/services/{service_id}/env-vars/{key}", {"value": value})

    def deploy(self, service_id: str) -> None:
        self.call("POST", f"/services/{service_id}/deploys", {"clearCache": "do_not_clear"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Put Relay online on Render.")
    parser.parse_args()

    if not KEYS.is_file():
        KEYS.write_text(KEYS_TEMPLATE, encoding="utf-8")
        print(f"Created {KEYS.relative_to(ROOT)}. Fill in the first four values, save it, and run this again.")
        return
    keys = read_env(KEYS)
    problems = check_keys(keys)
    if problems:
        print("Almost there. Fix these in deploy/keys.env, then run this again:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    try:
        repo = github_repo()
        emails = [e for e in keys["EMAILS"].replace(" ", "").split(",") if e]
        gen = generated_secrets(emails)
        env = {
            "GROQ_API_KEY": keys["GROQ_API_KEY"],
            "DATABASE_URL": keys["DATABASE_URL"],
            "RELAY_SECRET_KEY": gen["RELAY_SECRET_KEY"],
            "VAPID_PUBLIC_KEY": gen["VAPID_PUBLIC_KEY"],
            "VAPID_PRIVATE_KEY": gen["VAPID_PRIVATE_KEY"],
            "VAPID_SUBJECT": f"mailto:{emails[0]}",
            "INVITES": gen["INVITES"],
            "ALLOWED_EMAILS": ",".join(emails),
            "LLM_MODEL_CONCIERGE": "openai/gpt-oss-20b",  # a separate free daily quota for the quick part
        }
        if keys.get("GOOGLE_CLIENT_ID") and keys.get("GOOGLE_CLIENT_SECRET"):
            env["GOOGLE_CLIENT_ID"] = keys["GOOGLE_CLIENT_ID"]
            env["GOOGLE_CLIENT_SECRET"] = keys["GOOGLE_CLIENT_SECRET"]

        render = Render(keys["RENDER_API_KEY"])
        name = keys.get("SERVICE_NAME") or "relay"
        service = render.find_service(name)
        if service is None:
            print(f"Creating the Render service '{name}' from {repo} ...")
            service = render.create_service(render.owner_id(), name, repo, env)
        else:
            print(f"Updating the Render service '{name}' ...")
            render.set_env(service["id"], env)
        url = (service.get("serviceDetails") or {}).get("url") or f"https://{name}.onrender.com"
        render.set_env(service["id"], {"PUBLIC_URL": url, "KEEP_AWAKE_URL": url})
        render.deploy(service["id"])
    except DeployError as e:
        print(f"\nStopped: {e}")
        sys.exit(1)

    WORKFLOW.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOW.write_text(WORKFLOW_TEMPLATE.format(url=url), encoding="utf-8")

    print("\nDone. Render is building it now; the first build takes about 5 to 8 minutes:")
    print(f"  {url}")
    print(f"  (progress: https://dashboard.render.com/web/{service['id']})")
    print("\nSign in with your email and invite code:")
    for item in gen["INVITES"].split(","):
        email, code = item.split("=", 1)
        print(f"  {email:<34} {code}")
    print("\nThen commit and push (VS Code Sync) once more, to turn on the backup keep-awake job.")


if __name__ == "__main__":
    main()

"""Put Relay online in one command: python deploy/deploy.py

The first run creates deploy/keys.env for you to fill in (three values from three free
accounts). The next run does everything else:

    - creates your secrets (session key, notification keys, an invite code per person)
    - builds the Space bundle and creates or updates your Hugging Face Space
    - stores every secret in the Space's settings (never in the code)
    - writes a GitHub Actions workflow that keeps the Space awake for scheduled routines
    - prints your site's address and everyone's invite code

keys.env and generated.env stay on your computer; both are in .gitignore. Run it again any
time to publish a new version: secrets are kept, so nobody has to sign in again.
"""
import argparse
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "deploy" / "keys.env"
GENERATED = ROOT / "deploy" / "generated.env"
WORKFLOW = ROOT / ".github" / "workflows" / "keep-awake.yml"

KEYS_TEMPLATE = """# Fill in the first four values, save, then run:  python deploy/deploy.py
# This file never leaves your computer (it is in .gitignore).

# 1. Hugging Face: huggingface.co/settings/tokens > Create new token > type "Write"
HF_TOKEN=

# 2. Groq, from an account with NO card added: console.groq.com/keys > Create API Key
GROQ_API_KEY=

# 3. Neon: neon.tech > New project > Connect > copy the connection string (postgresql://...)
DATABASE_URL=

# 4. Who may use your Relay: your email first, then friends, separated by commas
EMAILS=

# Optional: the Space's name (your site becomes https://<username>-<name>.hf.space)
SPACE_NAME=relay

# Optional, later: Google sign-in, Gmail, and Calendar (docs/DEPLOY.md, "Gmail and Calendar")
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
"""

WORKFLOW_TEMPLATE = """# Free Hugging Face Spaces pause after 48 hours without visits, which would stop scheduled
# routines. This visits the site every 6 hours. Written by deploy/deploy.py.
name: Keep Relay awake
on:
  schedule:
    - cron: "17 */6 * * *"
  workflow_dispatch:
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: curl -fsS --retry 3 --max-time 60 "{url}/healthz"
"""


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


def space_url(username: str, name: str) -> str:
    """Hugging Face's address for a Space: lowercase, with '_' and '.' turned into '-'."""
    slug = re.sub(r"[^a-z0-9-]", "-", f"{username}-{name}".lower())
    return f"https://{slug}.hf.space"


def invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))


def check_keys(keys: dict[str, str]) -> list[str]:
    problems = []
    if not keys.get("HF_TOKEN", "").startswith("hf_"):
        problems.append("HF_TOKEN should start with hf_ (a Write token from huggingface.co/settings/tokens).")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Put Relay online on Hugging Face Spaces.")
    parser.add_argument("--dry-run", action="store_true", help="check everything but change nothing online")
    args = parser.parse_args()

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

    emails = [e for e in keys["EMAILS"].replace(" ", "").split(",") if e]
    gen = generated_secrets(emails)

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("Installing the Hugging Face client (one time)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"], check=True)
        from huggingface_hub import HfApi

    api = HfApi(token=keys["HF_TOKEN"])
    username = api.whoami()["name"]
    name = keys.get("SPACE_NAME") or "relay"
    repo_id = f"{username}/{name}"
    url = space_url(username, name)
    print(f"Hugging Face account: {username}. Your site will be {url}")

    secrets_to_set = {
        "GROQ_API_KEY": keys["GROQ_API_KEY"],
        "DATABASE_URL": keys["DATABASE_URL"],
        "RELAY_SECRET_KEY": gen["RELAY_SECRET_KEY"],
        "VAPID_PRIVATE_KEY": gen["VAPID_PRIVATE_KEY"],
        "INVITES": gen["INVITES"],
    }
    if keys.get("GOOGLE_CLIENT_ID") and keys.get("GOOGLE_CLIENT_SECRET"):
        secrets_to_set["GOOGLE_CLIENT_ID"] = keys["GOOGLE_CLIENT_ID"]
        secrets_to_set["GOOGLE_CLIENT_SECRET"] = keys["GOOGLE_CLIENT_SECRET"]
    variables = {
        "PUBLIC_URL": url,
        "VAPID_PUBLIC_KEY": gen["VAPID_PUBLIC_KEY"],
        "VAPID_SUBJECT": f"mailto:{emails[0]}",
        "ALLOWED_EMAILS": ",".join(emails),
        "LLM_MODEL_CONCIERGE": "openai/gpt-oss-20b",  # a separate free daily quota for the quick part
    }

    sys.path.insert(0, str(ROOT / "deploy"))
    import build_space
    build_space.main()

    if args.dry_run:
        print(f"Dry run: would create {repo_id}, set {sorted(secrets_to_set)} and {sorted(variables)}, and upload.")
    else:
        api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
        for key, value in secrets_to_set.items():
            api.add_space_secret(repo_id, key, value)
        for key, value in variables.items():
            api.add_space_variable(repo_id, key, value)
        print("Secrets stored in the Space's settings. Uploading the app (about a minute)...")
        api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=str(ROOT / "dist" / "space"),
                          commit_message="Deploy Relay")

    WORKFLOW.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOW.write_text(WORKFLOW_TEMPLATE.format(url=url), encoding="utf-8")

    print("\nDone. Hugging Face is building it now; the site is ready in about 3 to 5 minutes:")
    print(f"  {url}")
    print("\nSign in with your email and invite code:")
    for item in gen["INVITES"].split(","):
        email, code = item.split("=", 1)
        print(f"  {email:<34} {code}")
    print("\nLast step: commit and push (VS Code Sync). That turns on .github/workflows/keep-awake.yml,")
    print("which keeps the site awake so 7 AM routines run even when nobody has visited.")


if __name__ == "__main__":
    main()

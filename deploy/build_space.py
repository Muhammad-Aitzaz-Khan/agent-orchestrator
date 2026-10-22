"""Assemble the folder to upload to a Hugging Face Space: python deploy/build_space.py

Writes dist/space/ with only what the public site needs: the code, the sample projects for
"Fix a buggy program", the recordings, and the Space's Dockerfile and README. No .env, no
traces, no results. Then upload the folder's contents on the Space's "Files" page.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "space"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.egg-info", ".env*")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(ROOT / "src", OUT / "src", ignore=IGNORE)
    shutil.copytree(ROOT / "evals" / "tasks", OUT / "evals" / "tasks", ignore=IGNORE)
    shutil.copy2(ROOT / "pyproject.toml", OUT / "pyproject.toml")
    for name in ("Dockerfile", "README.md"):
        shutil.copy2(ROOT / "deploy" / "huggingface" / name, OUT / name)
    leaked = [p for p in OUT.rglob("*") if p.name.startswith(".env")]
    if leaked:
        raise SystemExit(f"Refusing to continue: {leaked} would be uploaded.")
    files = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"Wrote {OUT} ({files} files). Upload everything inside it to your Space.")


if __name__ == "__main__":
    main()

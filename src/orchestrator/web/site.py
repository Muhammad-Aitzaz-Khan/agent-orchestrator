"""Build the Relay page with the recorded runs embedded in it.

One page (static/relay.html) serves three purposes:

    local      python -m orchestrator.web serves it with live runs switched on
    static     python -m orchestrator.web.site writes site/index.html, a single file that
               plays the recordings with no server and no API key (for GitHub Pages)
    fragment   the same without the <html>/<head> wrapper, for hosts that add their own

The recordings are inlined as JSON, so the static page needs no network requests apart
from its fonts.
"""
import argparse
import json
from pathlib import Path

WEB_DIR = Path(__file__).parent
PAGE = WEB_DIR / "static" / "relay.html"
UNIT_JS = WEB_DIR / "static" / "unit.js"  # the robots, inlined so the page stays one file
REPLAYS_DIR = WEB_DIR / "replays"
DEFAULT_OUT = WEB_DIR.parents[2] / "site" / "index.html"


def _describe(events: list[dict]) -> str:
    """A one-line summary for the job picker, e.g. '14 requirements, approved in 1 round, 45 s'."""
    parts = []
    plan = next((e for e in events if e.get("type") == "agent_submitted" and e.get("role") == "planner"), None)
    if plan:
        count = len((plan.get("output") or {}).get("requirements") or [])
        parts.append(f"{count} checks")
    end = next((e for e in reversed(events) if e.get("type") == "run_finished"), None)
    if end:
        rounds = end.get("rounds") or 1
        if end.get("status") == "completed":
            parts.append("approved" if rounds == 1 else f"approved in round {rounds}")
        else:
            parts.append(str(end.get("status")))
        if end.get("duration_s"):
            parts.append(f"{round(end['duration_s'])} s real time")
    return ", ".join(parts)


def load_recordings(replays_dir: Path = REPLAYS_DIR) -> list[dict]:
    recordings = []
    for path in sorted(replays_dir.glob("*.jsonl")):
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not events:
            continue
        first = events[0]
        recordings.append({
            "name": path.stem,
            "title": first.get("title") or path.stem.replace("_", " "),
            "task": _describe(events) or first.get("task", ""),
            "kind": first.get("kind", "fix"),
            "events": events,
        })
    return recordings


def _script_json(value) -> str:
    """JSON that is safe inside a <script> element (no '</script>' or '<!--' can appear)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")


def render_fragment(live: bool = False, recordings: list[dict] | None = None,
                    embed: bool = False) -> str:
    page = PAGE.read_text(encoding="utf-8")
    recs = load_recordings() if recordings is None else recordings
    page = page.replace("/*RECORDINGS*/[]", _script_json(recs), 1)
    page = page.replace("/*EMBED*/false", "true" if embed else "false", 1)
    page = page.replace("/*UNIT_JS*/", UNIT_JS.read_text(encoding="utf-8").replace("</", "<\\/"), 1)
    return page.replace("/*LIVE*/false", "true" if live else "false", 1)


def render_page(live: bool = False, recordings: list[dict] | None = None, embed: bool = False) -> str:
    """The full HTML document: the fragment's head part goes into <head>, the rest into <body>."""
    fragment = render_fragment(live, recordings, embed)
    split = fragment.index("<header")
    head, body = fragment[:split], fragment[split:]
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{head}</head>\n<body>\n{body}</body>\n</html>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="orchestrator.web.site",
                                     description="Write the static Relay page with the recordings embedded.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--fragment", action="store_true", help="write the page without <html>/<head>")
    args = parser.parse_args()
    html = render_fragment() if args.fragment else render_page()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()

"""Turn the traces of a finished team run into a recording the web app can replay.

    python -m orchestrator.web.record ORCH_RUN_ID --repo evals/tasks/hard_graph/repo \
        --name hard_graph --title "Fixing a graph library"

Every agent writes its own JSONL trace, and the orchestrator writes one more plus a state
file. This rebuilds the same events a live run streams (see orchestrator._Updates), using the
real timestamps, so a replay shows what the agents actually did. Local paths, including the
user's name in them, are replaced so recordings can be shared.

Tool outputs in traces are already shortened to 500 characters, so they look shortened in a
replay too. Live runs record full events themselves, in runs/web/<id>/events.jsonl.
"""
import argparse
import difflib
import json
import re
from datetime import datetime
from pathlib import Path

from orchestrator.roles import TEAMS


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ts(record: dict) -> float:
    return datetime.fromisoformat(record["time"]).timestamp()


def _sanitizer(root: str):
    patterns = [re.escape(root), re.escape(root.replace("\\", "/")),
                re.escape(root.replace("\\", "\\\\"))]
    users = re.compile(r"([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\s\"']+", re.IGNORECASE)
    home = re.compile(r"(/(?:home|Users|sessions)/)[^/\s\"']+")

    def clean(value):
        if isinstance(value, str):
            for pattern in patterns:
                value = re.sub(pattern, "project", value)
            value = users.sub(r"\1you", value)
            return home.sub(r"\1you", value)
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return clean


def _diff(repo: Path, files: dict[str, str]) -> str:
    chunks = []
    for rel, new in sorted(files.items()):
        old_path = repo / rel
        old = old_path.read_text(encoding="utf-8").splitlines(keepends=True) if old_path.is_file() else []
        chunks.extend(difflib.unified_diff(old, new.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))
    return "".join(chunks) or "(no changes)"


def events_from_traces(orch_run_id: str, runs_dir: Path, repo: Path | None, title: str) -> list[dict]:
    orch = _read(runs_dir / f"{orch_run_id}.jsonl")
    state = json.loads((runs_dir / f"{orch_run_id}.state.json").read_text(encoding="utf-8"))
    start = next(r for r in orch if r["event"] == "orchestrator_start")
    kind = start.get("kind", "fix")
    team = {role.name: role for role in TEAMS[kind]}
    clean = _sanitizer(start.get("root", ""))

    events = [{
        "type": "run_started", "time": _ts(start), "round": 0, "run_id": orch_run_id,
        "task": state["task"], "kind": kind, "title": title, "max_rounds": start.get("max_rounds", 3),
        "roles": [{"name": r.name, "title": r.title} for r in TEAMS[kind]],
    }]
    files: dict[str, str] = {}
    round_number = 0
    rounds = state.get("rounds", [])

    for run in (r for r in orch if r["event"] == "agent_run"):
        role = team[run["role"]]
        if role.name == "coder":
            round_number += 1
        trace = _read(runs_dir / f"{run['agent_run_id']}.jsonl")
        first = trace[0]
        events.append({"type": "agent_started", "time": _ts(first), "round": round_number,
                       "role": role.name, "title": role.title})
        steps: dict[int, dict] = {}
        for record in trace:
            if record["event"] == "model_call":
                steps[record["step"]] = {"type": "agent_step", "time": _ts(record),
                                         "round": round_number, "role": role.name,
                                         "text": record.get("text") or "", "tools": []}
            elif record["event"] == "tool_call" and record["step"] in steps:
                step = steps[record["step"]]
                step["time"] = _ts(record)
                if record["name"] == role.submit_tool:
                    continue
                step["tools"].append({"name": record["name"], "arguments": record["arguments"],
                                      "output": record.get("output", "")})
                if record["name"] == "write_file":
                    args = json.loads(record["arguments"] or "{}")
                    if "path" in args and "content" in args and not record["output"].startswith("Error"):
                        files[args["path"]] = args["content"]
            elif record["event"] == "submitted":
                events.extend(steps.values())
                steps = {}
                events.append({"type": "agent_submitted", "time": _ts(record),
                               "round": round_number, "role": role.name, "output": record["output"]})
            elif record["event"] == "run_end":
                events.extend(steps.values())
                steps = {}
                events.append({"type": "agent_finished", "time": _ts(record), "round": round_number,
                               "role": role.name, "status": record["status"],
                               "tokens": record.get("total_tokens", 0), "steps": record.get("steps", 0),
                               "error": record.get("error")})
                end_time = _ts(record)

        if round_number and round_number <= len(rounds):
            info = rounds[round_number - 1]
            if role.name == "coder" and info.get("visible_tests_passed") is not None:
                events.append({"type": "gate", "time": end_time + 0.2, "round": round_number,
                               "passed": info["visible_tests_passed"],
                               "output": info.get("visible_tests_output") or ""})
            decision = info.get("decision", "")
            last_role = ("coder" if decision == "revise: visible tests fail"
                         else "tester" if decision == "revise: tester found failing requirements"
                         else "reviewer")
            if decision and role.name == last_role:
                events.append({"type": "decision", "time": end_time + 0.4, "round": round_number,
                               "decision": decision})

    events.sort(key=lambda e: e["time"])
    end = next(r for r in orch if r["event"] == "orchestrator_end")
    by_role: dict[str, int] = {}
    for run in state.get("agent_runs", []):
        by_role[run["role"]] = by_role.get(run["role"], 0) + run["tokens"]
    events.append({
        "type": "run_finished", "time": max(_ts(end), events[-1]["time"] + 0.3),
        "round": round_number, "status": state["status"],
        "error": state.get("error"), "total_tokens": state["total_tokens"],
        "tokens_by_role": by_role, "duration_s": state["duration_s"], "rounds": len(rounds),
        "files": files, "diff": _diff(repo, files) if repo else "",
    })
    return clean(events)


def main() -> None:
    parser = argparse.ArgumentParser(description="Make a web replay from a team run's traces.")
    parser.add_argument("run_id", help="the orchestrator run id, e.g. orch-20261021-...")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--repo", type=Path, help="the original project, to compute the diff")
    parser.add_argument("--name", required=True, help="file name for the recording")
    parser.add_argument("--title", required=True, help="what the replay list shows")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "replays")
    args = parser.parse_args()

    events = events_from_traces(args.run_id, args.runs_dir, args.repo, args.title)
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"{args.name}.jsonl"
    target.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    print(f"Wrote {len(events)} events to {target}")


if __name__ == "__main__":
    main()

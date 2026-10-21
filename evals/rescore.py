"""Re-judge a finished eval against the current tasks, without running any agent again.

    python evals/rescore.py evals/results/<file>.json [--task medium_bank_account]

Use this after fixing a task's tests (for example removing a hidden test that checked
behaviour the docstrings never specified). Each run's final code is rebuilt from its traces:
the original repo plus every successful write_file call, in order, from the agent (single
mode) or from every Coder run (multi mode). The result is judged exactly like a normal eval.
The original results file is left untouched; a new file ending in -rescored.json is written.
"""
import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_evals import (
    TASKS_DIR,
    add_hidden_tests,
    judge,
    load_tasks,
    prepare_workspace,
    summarize,
)


def agent_traces(run_id: str, runs_dir: Path) -> list[Path]:
    if run_id.startswith("orch-"):
        state = json.loads((runs_dir / f"{run_id}.state.json").read_text(encoding="utf-8"))
        return [runs_dir / f"{a['run_id']}.jsonl" for a in state["agent_runs"] if a["role"] == "coder"]
    return [runs_dir / f"{run_id}.jsonl"]


def final_writes(traces: list[Path]) -> list[tuple[str, str]]:
    writes = []
    for trace in traces:
        for line in trace.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("event") != "tool_call" or event.get("name") != "write_file":
                continue
            if not str(event.get("output", "")).startswith("Wrote"):
                continue
            args = json.loads(event["arguments"])
            writes.append((args["path"], args["content"]))
    return writes


def rescore_row(row: dict, task: dict, runs_dir: Path) -> dict:
    if row["outcome"] == "error" or row["run_id"] == "-":
        return row
    with tempfile.TemporaryDirectory(prefix="rescore_", ignore_cleanup_errors=True) as tmp:
        workspace = prepare_workspace(task, Path(tmp))
        for path, content in final_writes(agent_traces(row["run_id"], runs_dir)):
            target = workspace / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        add_hidden_tests(task, workspace)
        passed, summary, failed = judge(workspace)
    outcome = "pass" if passed else "fail"
    return {**row, "outcome": outcome, "test_summary": summary, "failed_tests": failed,
            "rescored_from": row["outcome"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-judge an eval against the current tasks.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--task", help="only re-judge rows for this task (others are kept)")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args()

    data = json.loads(args.results.read_text(encoding="utf-8"))
    tasks = {t["id"]: t for t in load_tasks(tasks_dir=TASKS_DIR)}
    rows = []
    for row in data["results"]:
        if args.task and row["task"] != args.task:
            rows.append(row)
            continue
        new = rescore_row(row, tasks[row["task"]], args.runs_dir)
        if new.get("rescored_from") and new["rescored_from"] != new["outcome"]:
            print(f"{row['task']:<26} {row['run_id']:<28} {new['rescored_from']} -> {new['outcome']}")
        rows.append(new)

    out = args.results.with_name(args.results.stem + "-rescored.json")
    out.write_text(json.dumps({
        **data, "results": rows, "summary": summarize(rows),
        "rescored": {"date": datetime.now().isoformat(timespec="seconds"),
                     "from": args.results.name, "task": args.task},
    }, indent=2), encoding="utf-8")
    for level, s in summarize(rows).items():
        print(f"{level:<7}{s['passed']}/{s['scored']} passed ({100 * s['pass_rate']:.0f}%)")
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()

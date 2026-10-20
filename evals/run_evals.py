"""Eval harness: runs the agent on every task and measures how often it succeeds.

Usage:
    python evals/run_evals.py --check-tasks          # validate tasks, no API calls
    python evals/run_evals.py                        # run every task once
    python evals/run_evals.py --difficulty easy
    python evals/run_evals.py --task calculator --repeats 3

Each task folder looks like:
    evals/tasks/<name>/
        task.toml      difficulty + prompt
        repo/          the buggy project the agent works on
        hidden/        (optional) extra tests the agent never sees
        solution/      (optional) fixed source files, used only by --check-tasks
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from orchestrator.agent import default_model, run_agent
from orchestrator.state import RunResult
from orchestrator.tools import _clean_env, _is_protected

TASKS_DIR = Path(__file__).parent / "tasks"
RESULTS_DIR = Path(__file__).parent / "results"
JUDGE_TIMEOUT_S = 60
DIFFICULTIES = ["easy", "medium", "hard"]
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache")

Agent = Callable[..., RunResult]


# ---------- loading tasks ----------

def load_tasks(
    difficulty: str | None = None, name_filter: str | None = None, tasks_dir: Path = TASKS_DIR
) -> list[dict]:
    tasks = []
    for task_dir in sorted(tasks_dir.iterdir()):
        meta_file = task_dir / "task.toml"
        if not meta_file.is_file():
            continue
        meta = tomllib.loads(meta_file.read_text(encoding="utf-8"))
        task = {"id": task_dir.name, "dir": task_dir, **meta}
        if task.get("difficulty") not in DIFFICULTIES:
            sys.exit(f"{meta_file}: difficulty must be one of {DIFFICULTIES}")
        if difficulty and task["difficulty"] != difficulty:
            continue
        if name_filter and name_filter not in task["id"]:
            continue
        tasks.append(task)
    return tasks


# ---------- judging ----------

def prepare_workspace(task: dict, parent: Path) -> Path:
    """Copy the task's buggy repo into a fresh folder. The original is never touched."""
    workspace = parent / "repo"
    shutil.copytree(task["dir"] / "repo", workspace, ignore=IGNORE)
    return workspace


def restore_protected_files(original: Path, workspace: Path) -> None:
    """Put the original tests and config back, in case anything changed them."""
    for src in original.rglob("*"):
        rel = src.relative_to(original)
        if src.is_file() and _is_protected(rel) and "__pycache__" not in rel.parts:
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def add_hidden_tests(task: dict, workspace: Path) -> None:
    hidden = task["dir"] / "hidden"
    if hidden.is_dir():
        shutil.copytree(hidden, workspace, dirs_exist_ok=True, ignore=IGNORE)


def judge(workspace: Path) -> tuple[bool, str]:
    """Run the tests ourselves. Never trust the agent's own claim of success."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=JUDGE_TIMEOUT_S,
            env=_clean_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "judge timed out"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode == 0, (lines[-1] if lines else "(no output)")


# ---------- running the agent ----------

def run_one(task: dict, max_steps: int, agent: Agent = run_agent) -> dict:
    with tempfile.TemporaryDirectory(prefix="eval_", ignore_cleanup_errors=True) as tmp:
        workspace = prepare_workspace(task, Path(tmp))

        try:
            result = agent(task["prompt"], root=workspace, max_steps=max_steps)
        except Exception as e:  # e.g. missing API key; record it instead of losing the whole run
            result = RunResult(
                run_id="-", task=task["prompt"], status="error", error=f"{type(e).__name__}: {e}"
            )

        restore_protected_files(task["dir"] / "repo", workspace)
        add_hidden_tests(task, workspace)
        passed, test_summary = judge(workspace)

    if result.status == "error":
        outcome = "error"  # API problem (e.g. rate limit), not the agent's fault
    else:
        outcome = "pass" if passed else "fail"

    return {
        "task": task["id"],
        "difficulty": task["difficulty"],
        "outcome": outcome,
        "agent_status": result.status,
        "test_summary": test_summary,
        "steps": len(result.steps),
        "tokens": result.total_tokens,
        "duration_s": result.duration_s,
        "run_id": result.run_id,
        "error": result.error,
    }


# ---------- checking the tasks themselves ----------

def check_task(task: dict) -> list[str]:
    """Return a list of problems with a task. Empty means the task is valid."""
    problems = []
    with tempfile.TemporaryDirectory(prefix="check_", ignore_cleanup_errors=True) as tmp:
        workspace = prepare_workspace(task, Path(tmp))
        add_hidden_tests(task, workspace)
        passed, _ = judge(workspace)
        if passed:
            problems.append("buggy repo already passes all tests (nothing to fix)")

    solution = task["dir"] / "solution"
    if not solution.is_dir():
        problems.append("no solution/ folder, so solvability is unverified")
        return problems

    with tempfile.TemporaryDirectory(prefix="check_", ignore_cleanup_errors=True) as tmp:
        workspace = prepare_workspace(task, Path(tmp))
        shutil.copytree(solution, workspace, dirs_exist_ok=True, ignore=IGNORE)
        add_hidden_tests(task, workspace)
        passed, summary = judge(workspace)
        if not passed:
            problems.append(f"solution does not pass the tests ({summary})")
    return problems


def check_tasks(tasks: list[dict]) -> bool:
    all_ok = True
    for task in tasks:
        problems = check_task(task)
        status = "OK " if not problems else "BAD"
        print(f"{status} {task['id']:<28}{task['difficulty']}")
        for p in problems:
            print(f"      - {p}")
        all_ok = all_ok and not problems
    return all_ok


# ---------- reporting ----------

def summarize(rows: list[dict]) -> dict[str, dict]:
    """Pass rate and averages per difficulty, plus 'all'. API errors are excluded from scores."""
    summary = {}
    for level in DIFFICULTIES + ["all"]:
        group = rows if level == "all" else [r for r in rows if r["difficulty"] == level]
        if not group:
            continue
        scored = [r for r in group if r["outcome"] != "error"]
        passed = sum(r["outcome"] == "pass" for r in scored)
        n = len(scored)
        summary[level] = {
            "passed": passed,
            "scored": n,
            "errors": len(group) - n,
            "pass_rate": round(passed / n, 3) if n else 0.0,
            "avg_steps": round(sum(r["steps"] for r in scored) / n, 1) if n else 0.0,
            "avg_tokens": round(sum(r["tokens"] for r in scored) / n) if n else 0,
            "avg_duration_s": round(sum(r["duration_s"] for r in scored) / n, 1) if n else 0.0,
        }
    return summary


def print_report(rows: list[dict], summary: dict[str, dict]) -> None:
    print(f"\n{'task':<28}{'level':<8}{'result':<8}{'steps':>6}{'tokens':>9}{'time':>9}")
    for r in rows:
        print(
            f"{r['task']:<28}{r['difficulty']:<8}{r['outcome'].upper():<8}"
            f"{r['steps']:>6}{r['tokens']:>9}{r['duration_s']:>8.1f}s"
        )
    print()
    for level, s in summary.items():
        line = (
            f"{level:<7}{s['passed']}/{s['scored']} passed ({100 * s['pass_rate']:.0f}%)"
            f"   avg steps {s['avg_steps']}   avg tokens {s['avg_tokens']}"
            f"   avg time {s['avg_duration_s']}s"
        )
        if s["errors"]:
            line += f"   [{s['errors']} API error(s) excluded]"
        print(line)


# ---------- main ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent on the eval tasks.")
    parser.add_argument("--difficulty", choices=DIFFICULTIES)
    parser.add_argument("--task", help="only run tasks whose name contains this text")
    parser.add_argument("--repeats", type=int, default=1, help="run each task this many times")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--delay", type=float, default=5.0, help="seconds between runs (rate limits)")
    parser.add_argument("--check-tasks", action="store_true", help="validate tasks without the API")
    args = parser.parse_args()

    tasks = load_tasks(args.difficulty, args.task)
    if not tasks:
        sys.exit("No matching tasks found.")

    if args.check_tasks:
        sys.exit(0 if check_tasks(tasks) else 1)

    runs = [task for task in tasks for _ in range(args.repeats)]
    model = default_model()
    print(f"Running {len(runs)} run(s) with model {model}\n")

    rows = []
    for i, task in enumerate(runs, start=1):
        print(f"[{i}/{len(runs)}] {task['id']} ...", end=" ", flush=True)
        row = run_one(task, args.max_steps)
        rows.append(row)
        print(f"{row['outcome'].upper()}  ({row['error'] or row['test_summary']})")
        if i < len(runs):
            time.sleep(args.delay)

    summary = summarize(rows)
    print_report(rows, summary)

    RESULTS_DIR.mkdir(exist_ok=True)
    out_file = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out_file.write_text(
        json.dumps(
            {
                "date": datetime.now().isoformat(timespec="seconds"),
                "model": model,
                "max_steps": args.max_steps,
                "repeats": args.repeats,
                "summary": summary,
                "results": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nSaved: {out_file}")


if __name__ == "__main__":
    main()

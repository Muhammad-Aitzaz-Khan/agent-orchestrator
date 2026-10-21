"""Tests for the eval harness. These never call the LLM API."""
import importlib.util
import shutil
from pathlib import Path

import pytest

from orchestrator.state import RunResult

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
run_evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_evals)

TASKS = run_evals.load_tasks()


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_every_task_is_broken_and_solvable(task):
    assert run_evals.check_task(task) == []


def solving_agent(task, root, max_steps):
    """A fake agent that 'fixes' the easy task by copying in the solution."""
    shutil.copytree(ROOT / "evals/tasks/easy_calculator/solution", root, dirs_exist_ok=True)
    return RunResult(run_id="fake", task=task, status="completed")


def cheating_agent(task, root, max_steps):
    """A fake agent that deletes the tests instead of fixing the code."""
    (root / "test_calculator.py").write_text("def test_nothing():\n    pass\n")
    return RunResult(run_id="fake", task=task, status="completed")


def crashing_agent(task, root, max_steps):
    raise RuntimeError("GROQ_API_KEY is not set")


def easy_task():
    return run_evals.load_tasks(name_filter="easy_calculator")[0]


def test_solving_agent_passes():
    assert run_evals.run_one(easy_task(), 5, agent=solving_agent)["outcome"] == "pass"


def test_cheating_agent_fails():
    """Edited tests are restored before judging, so cheating cannot pass."""
    assert run_evals.run_one(easy_task(), 5, agent=cheating_agent)["outcome"] == "fail"


def test_crashing_agent_is_recorded_as_error():
    row = run_evals.run_one(easy_task(), 5, agent=crashing_agent)
    assert row["outcome"] == "error"
    assert "GROQ_API_KEY" in row["error"]


def test_original_task_files_are_never_modified():
    before = (easy_task()["dir"] / "repo" / "calculator.py").read_text()
    run_evals.run_one(easy_task(), 5, agent=solving_agent)
    assert (easy_task()["dir"] / "repo" / "calculator.py").read_text() == before


def test_summary_excludes_api_errors():
    rows = [
        {"difficulty": "easy", "outcome": "pass", "steps": 4, "tokens": 100, "duration_s": 2.0},
        {"difficulty": "easy", "outcome": "fail", "steps": 6, "tokens": 300, "duration_s": 4.0},
        {"difficulty": "easy", "outcome": "error", "steps": 0, "tokens": 0, "duration_s": 0.0},
    ]
    s = run_evals.summarize(rows)["easy"]
    assert (s["passed"], s["scored"], s["errors"]) == (1, 2, 1)
    assert s["pass_rate"] == 0.5
    assert s["avg_tokens"] == 200


def test_judge_reports_failing_test_names(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_x.py").write_text("def test_good():\n    pass\n\ndef test_bad():\n    assert False\n")
    passed, _, failed = run_evals.judge(tmp_path)
    assert not passed
    assert failed == ["test_x.py::test_bad"]


def row(task, outcome, error=None):
    return {"task": task, "outcome": outcome, "error": error}


def test_merge_replaces_errors_with_new_attempts():
    previous = [row("a", "pass"), row("b", "error", "x"), row("b", "error", "x"), row("c", "fail")]
    new = [row("b", "pass"), row("b", "fail")]
    merged = run_evals.merge_results(previous, new)
    assert [r["outcome"] for r in merged] == ["pass", "pass", "fail", "fail"]


def test_merge_keeps_errors_that_were_not_rerun():
    previous = [row("a", "error", "x"), row("b", "error", "x")]
    merged = run_evals.merge_results(previous, [row("a", "pass")])
    assert [r["outcome"] for r in merged] == ["pass", "error"]


def test_daily_limit_row_detection():
    assert run_evals.is_daily_limit_row(row("a", "error", "DailyLimitReached: tokens per day"))
    assert not run_evals.is_daily_limit_row(row("a", "error", "RateLimitError: per minute"))
    assert not run_evals.is_daily_limit_row(row("a", "fail"))

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

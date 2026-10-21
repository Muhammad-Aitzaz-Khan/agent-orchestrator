"""Tests for the four-agent orchestrator, using a scripted fake model client.

The model is fake, but the tools are real: files are really written, tests really run, and
run_python really executes, all inside a temporary project.
"""
import pytest
from test_agent import FakeClient, completion, rate_limit_error

from orchestrator.orchestrator import run_orchestrator

BUGGY = "def double(x):\n    return x + 2\n"
FIXED = "def double(x):\n    return x * 2\n"

PLAN = {
    "summary": "double adds 2 instead of multiplying by 2.",
    "files_to_change": ["mathlib.py"],
    "requirements": [
        {"id": "R1", "description": "double(3) returns 6", "source": "test"},
        {"id": "R2", "description": "double(0) returns 0", "source": "docstring"},
    ],
}
CHANGES = {"summary": "multiply instead of add", "files_changed": ["mathlib.py"]}
ALL_PASS = {"checks": [
    {"requirement_id": "R1", "passed": True, "evidence": "printed 6"},
    {"requirement_id": "R2", "passed": True, "evidence": "printed 0"},
]}
APPROVE = {"verdict": "approve", "issues": []}


def call(name, args=None):
    return completion(tool_calls=[(name, args or {})])


def planner_ok():
    return [call("read_file", {"path": "mathlib.py"}), call("submit_plan", PLAN)]


def coder_fixes():
    return [call("write_file", {"path": "mathlib.py", "content": FIXED}),
            call("submit_changes", CHANGES)]


def coder_does_nothing():
    return [call("submit_changes", {"summary": "no change", "files_changed": []})]


def all_checks_pass():
    code = "from mathlib import double\nprint(double(3), double(0))"
    return [call("run_python", {"code": code}), call("submit_test_report", ALL_PASS)]


def reviewer_approves():
    return [call("show_diff"), call("submit_review", APPROVE)]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # traces go to tmp_path/runs
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\n")
    (repo / "mathlib.py").write_text(BUGGY)
    (repo / "test_mathlib.py").write_text(
        "from mathlib import double\n\ndef test_double():\n    assert double(3) == 6\n"
    )
    return repo


def run(project, script, **kwargs):
    client = FakeClient(script)
    result = run_orchestrator("Fix the failing tests.", project, client=client, sleep=lambda s: None,
                              models={r: "fake" for r in ("planner", "coder", "tester", "reviewer")},
                              **kwargs)
    return result, client


def tool_results(client):
    """Every tool result the agents received, in order."""
    seen, out = set(), []
    for request in client.requests:
        for m in request["messages"]:
            if m.get("role") == "tool":
                key = (m["tool_call_id"], m["content"])
                if key not in seen:
                    seen.add(key)
                    out.append(m["content"])
    return out


def test_happy_path_one_round(project):
    result, client = run(project, planner_ok() + coder_fixes() + all_checks_pass() + reviewer_approves())
    assert result.status == "completed"
    assert len(result.rounds) == 1
    assert [r.role for r in result.agent_runs] == ["planner", "coder", "tester", "reviewer"]
    assert result.rounds[0].visible_tests_passed
    assert result.rounds[0].decision.startswith("accept")
    assert (project / "mathlib.py").read_text() == FIXED
    outputs = tool_results(client)
    assert any(o.startswith("exit code 0") and "6 0" in o for o in outputs)  # tester ran code
    assert any("+    return x * 2" in o for o in outputs)  # reviewer saw the diff
    assert result.total_tokens == 800  # 8 fake model calls x 100 tokens
    assert result.tokens_by_role() == {"planner": 200, "coder": 200, "tester": 200, "reviewer": 200}


def test_state_file_is_saved(project):
    result, _ = run(project, planner_ok() + coder_fixes() + all_checks_pass() + reviewer_approves())
    saved = (project.parent / "runs" / f"{result.run_id}.state.json").read_text()
    assert '"status": "completed"' in saved and '"R2"' in saved


def test_failing_visible_tests_send_coder_back(project):
    script = planner_ok() + coder_does_nothing() + coder_fixes() + all_checks_pass() + reviewer_approves()
    result, client = run(project, script)
    assert result.status == "completed"
    assert len(result.rounds) == 2
    assert result.rounds[0].decision == "revise: visible tests fail"
    assert [r.role for r in result.agent_runs] == ["planner", "coder", "coder", "tester", "reviewer"]
    second_coder_task = client.requests[3]["messages"][1]["content"]
    assert "not accepted" in second_coder_task and "visible tests still fail" in second_coder_task


def test_tester_failure_sends_specific_evidence_to_coder(project):
    failing = {"checks": [
        {"requirement_id": "R1", "passed": True, "evidence": "printed 6"},
        {"requirement_id": "R2", "passed": False, "evidence": "double(0) printed 2"},
    ]}
    script = (planner_ok() + coder_fixes() + [call("submit_test_report", failing)]
              + coder_fixes() + all_checks_pass() + reviewer_approves())
    result, client = run(project, script)
    assert result.status == "completed"
    assert result.rounds[0].decision == "revise: tester found failing requirements"
    coder_round2 = next(r for r in client.requests if "not accepted" in r["messages"][1]["content"])
    assert "Requirement R2 fails. Evidence: double(0) printed 2" in coder_round2["messages"][1]["content"]


def test_reviewer_blocker_triggers_revision(project):
    revise = {"verdict": "revise", "issues": [
        {"severity": "blocker", "description": "also handle floats", "file": "mathlib.py"}]}
    script = (planner_ok() + coder_fixes() + all_checks_pass() + [call("submit_review", revise)]
              + coder_fixes() + all_checks_pass() + reviewer_approves())
    result, client = run(project, script)
    assert result.status == "completed"
    assert result.rounds[0].decision == "revise: reviewer found 1 blocker(s)"
    assert any("Reviewer [blocker] (mathlib.py): also handle floats" in r["messages"][1]["content"]
               for r in client.requests)


def test_minor_issues_do_not_block(project):
    minor = {"verdict": "revise", "issues": [{"severity": "minor", "description": "rename x"}]}
    script = planner_ok() + coder_fixes() + all_checks_pass() + [call("submit_review", minor)]
    result, _ = run(project, script)
    assert result.status == "completed"
    assert len(result.rounds) == 1


def test_planner_cannot_write_files(project):
    script = ([call("write_file", {"path": "mathlib.py", "content": "hacked"}),
               call("submit_plan", PLAN)]
              + coder_fixes() + all_checks_pass() + reviewer_approves())
    result, client = run(project, script)
    assert "the write_file tool is not available to this agent" in tool_results(client)[0]
    assert result.status == "completed"
    # the planner was never offered write_file in the first place
    offered = [t["function"]["name"] for t in client.requests[0]["tools"]]
    assert "write_file" not in offered and "submit_plan" in offered


def test_tester_scripts_cannot_change_the_project(project):
    vandal = "open('mathlib.py', 'w').write('broken')\nprint('done')"
    script = (planner_ok() + coder_fixes()
              + [call("run_python", {"code": vandal}), call("submit_test_report", ALL_PASS)]
              + reviewer_approves())
    result, _ = run(project, script)
    assert (project / "mathlib.py").read_text() == FIXED
    assert result.status == "completed"


def test_invalid_submission_is_rejected_and_retried(project):
    bad_plan = {"summary": "x", "files_to_change": [], "requirements": []}
    script = ([call("submit_plan", bad_plan), call("submit_plan", PLAN)]
              + coder_fixes() + all_checks_pass() + reviewer_approves())
    result, client = run(project, script)
    assert "invalid submission" in tool_results(client)[0]
    assert result.plan.requirements[0].id == "R1"
    assert not result.plan_is_fallback


def test_planner_that_never_submits_gets_a_fallback_plan(project):
    silent = [completion(content="I think it's double.")] * 3  # first answer + two reminders
    script = silent + coder_fixes() + all_checks_pass() + reviewer_approves()
    result, client = run(project, script)
    assert result.plan_is_fallback
    assert result.status == "completed"
    assert "You have not submitted" in client.requests[1]["messages"][-1]["content"]


def test_daily_limit_aborts_the_whole_run(project):
    result, _ = run(project, [rate_limit_error("Limit on tokens per day (TPD)", "300")])
    assert result.status == "error"
    assert result.error.startswith("DailyLimitReached")


def test_token_budget_stops_the_run(project):
    result, _ = run(project, planner_ok() + coder_fixes(), token_budget=250)
    assert result.status == "budget_exhausted"
    assert [r.role for r in result.agent_runs] == ["planner", "coder"]


def test_unresolved_after_max_rounds(project):
    result, _ = run(project, planner_ok() + coder_does_nothing() * 2, max_rounds=2)
    assert result.status == "unresolved"
    assert [r.decision for r in result.rounds] == ["revise: visible tests fail"] * 2

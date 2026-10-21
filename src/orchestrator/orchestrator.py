"""The multi-agent orchestrator: a state machine that coordinates four roles.

    PLAN ──► CODE ──► gate: visible tests ──► TEST ──► REVIEW ──► DONE
              ▲            │ fail                │ fail      │ revise
              └────────────┴─────────────────────┴───────────┘   (up to max_rounds)

- The Planner turns the task and docstrings into checkable requirements.
- The Coder changes the code to satisfy them.
- A deterministic gate runs the visible tests (no model call), so tokens are not spent
  checking a fix that does not even pass the existing tests.
- The Tester checks every requirement by running code in a throwaway copy.
- The Reviewer reads the diff, but only once the Tester reports everything passes.
Anything that fails sends specific feedback back to the Coder for another round.
"""
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from openai import OpenAI

from orchestrator.agent import default_model, make_client, new_run_id, run_agent
from orchestrator.roles import (
    CODER,
    PLANNER,
    REVIEWER,
    TESTER,
    Role,
    coder_task,
    feedback_from,
    planner_task,
    reviewer_task,
    tester_task,
)
from orchestrator.state import (
    AgentRunSummary,
    ChangeSummary,
    OrchestratorResult,
    Plan,
    Requirement,
    Review,
    Round,
    TestReport,
)
from orchestrator.tools import copy_project, run_tests
from orchestrator.tracing import Tracer

DEFAULT_MAX_ROUNDS = 3  # the first attempt plus up to two revisions
DEFAULT_TOKEN_BUDGET = 100_000  # per task; protects a free-tier daily quota

EventHandler = Callable[[str, str], None]


class _Abort(Exception):
    """An agent hit an error it cannot recover from (for example the daily API limit)."""


class _BudgetExhausted(Exception):
    pass


def model_for(role: Role, overrides: dict[str, str] | None = None) -> str:
    """Each role can use its own model: LLM_MODEL_PLANNER, LLM_MODEL_CODER, ... in .env.

    On Groq every model has its own daily limit, so spreading roles across models also
    spreads the quota.
    """
    if overrides and role.name in overrides:
        return overrides[role.name]
    return os.getenv(f"LLM_MODEL_{role.name.upper()}") or default_model()


def fallback_plan(task: str) -> Plan:
    """Used when the Planner fails to submit, so the team can still attempt the task."""
    return Plan(
        summary="The planner did not produce a plan; working from the task alone.",
        files_to_change=[],
        requirements=[
            Requirement(id="R1", description=task, source="task"),
            Requirement(
                id="R2",
                description="Every behaviour stated in the docstrings of the changed code holds.",
                source="docstrings",
            ),
            Requirement(id="R3", description="All existing tests pass.", source="tests"),
        ],
    )


def run_orchestrator(
    task: str,
    root: Path,
    client: OpenAI | None = None,
    models: dict[str, str] | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    token_budget: int | None = None,
    on_event: EventHandler | None = None,
    sleep: Callable[[float], None] = time.sleep,
    context_chars: int | None = None,
    runs_dir: Path = Path("runs"),
) -> OrchestratorResult:
    """Run the four-agent team on a task in the project at root."""
    root = root.resolve()
    client = client or make_client()
    budget = token_budget or int(os.getenv("ORCH_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET))
    run_id = "orch-" + new_run_id()
    tracer = Tracer(run_id, runs_dir)
    state = OrchestratorResult(run_id=run_id, task=task)
    state_file = runs_dir / f"{run_id}.state.json"
    started = time.perf_counter()

    def event(phase: str, detail: str = "") -> None:
        tracer.log("phase", phase=phase, detail=detail)
        if on_event:
            on_event(phase, detail)

    def save() -> None:
        state.duration_s = round(time.perf_counter() - started, 2)
        state_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    def run_role(role: Role, message: str, baseline: Path):
        result = run_agent(
            message,
            root=root,
            max_steps=role.max_steps,
            model=model_for(role, models),
            client=client,
            sleep=sleep,
            context_chars=context_chars,
            role=role,
            baseline=baseline,
        )
        state.agent_runs.append(
            AgentRunSummary(
                role=role.name,
                run_id=result.run_id,
                status=result.status,
                steps=len(result.steps),
                tokens=result.total_tokens,
                duration_s=result.duration_s,
                submitted=result.output is not None,
            )
        )
        state.total_tokens += result.total_tokens
        state.total_steps += len(result.steps)
        tracer.log(
            "agent_run", role=role.name, agent_run_id=result.run_id, status=result.status,
            tokens=result.total_tokens, submitted=result.output is not None,
        )
        if result.status == "error":
            raise _Abort(result.error or "agent error")
        if state.total_tokens > budget:
            raise _BudgetExhausted()
        return result

    tracer.log("orchestrator_start", task=task, root=str(root), max_rounds=max_rounds,
               token_budget=budget)
    with tempfile.TemporaryDirectory(prefix="baseline_", ignore_cleanup_errors=True) as tmp:
        baseline = copy_project(root, Path(tmp) / "baseline")  # the untouched project, for diffs
        try:
            _run_rounds(task, root, baseline, state, run_role, event, save, max_rounds)
        except _Abort as e:
            state.status = "error"
            state.error = str(e)
            event("aborted", state.error[:200])
        except _BudgetExhausted:
            state.status = "budget_exhausted"
            event("budget_exhausted", f"{state.total_tokens} tokens used, budget {budget}")

    save()
    tracer.log("orchestrator_end", status=state.status, rounds=len(state.rounds),
               total_tokens=state.total_tokens, duration_s=state.duration_s)
    return state


def _run_rounds(task, root, baseline, state, run_role, event, save, max_rounds) -> None:
    # ---- PLAN ----
    event("plan", "planner is investigating")
    planned = run_role(PLANNER, planner_task(task), baseline)
    if planned.output is not None:
        state.plan = Plan.model_validate(planned.output)
    else:
        state.plan = fallback_plan(task)
        state.plan_is_fallback = True
    event("plan", f"{len(state.plan.requirements)} requirements"
          + (" (fallback plan)" if state.plan_is_fallback else ""))
    save()

    feedback = None
    for number in range(1, max_rounds + 1):
        round_ = Round(number=number)
        state.rounds.append(round_)

        # ---- CODE ----
        event("code", f"round {number}")
        coded = run_role(CODER, coder_task(task, state.plan, feedback), baseline)
        if coded.output is not None:
            round_.changes = ChangeSummary.model_validate(coded.output)

        # ---- GATE: visible tests, no model involved ----
        gate = run_tests(root)
        round_.visible_tests_passed = gate.startswith("Tests PASSED")
        round_.visible_tests_output = gate[-1500:]
        event("gate", "visible tests pass" if round_.visible_tests_passed else "visible tests FAIL")
        if not round_.visible_tests_passed:
            round_.decision = "revise: visible tests fail"
            feedback = feedback_from(round_)
            save()
            continue

        # ---- TEST ----
        event("test", f"checking {len(state.plan.requirements)} requirements")
        tested = run_role(TESTER, tester_task(task, state.plan), baseline)
        if tested.output is not None:
            round_.test_report = TestReport.model_validate(tested.output)
            failures = round_.test_report.failures
            event("test", f"{len(round_.test_report.checks) - len(failures)} passed, "
                  f"{len(failures)} failed")
            if failures:
                round_.decision = "revise: tester found failing requirements"
                feedback = feedback_from(round_)
                save()
                continue
        else:
            event("test", "tester did not submit a report; continuing unverified")

        # ---- REVIEW ----
        event("review", "reviewer is reading the diff")
        reviewed = run_role(REVIEWER, reviewer_task(task, state.plan, round_.test_report),
                            baseline)
        if reviewed.output is None:
            round_.decision = "accept: visible tests pass, reviewer did not submit"
            state.status = "completed"
            event("done", round_.decision)
            save()
            return
        round_.review = Review.model_validate(reviewed.output)
        blockers = [i for i in round_.review.issues if i.severity == "blocker"]
        if round_.review.verdict == "approve" or not blockers:
            round_.decision = "accept: approved by reviewer"
            state.status = "completed"
            event("done", round_.decision)
            save()
            return
        round_.decision = f"revise: reviewer found {len(blockers)} blocker(s)"
        event("review", round_.decision)
        feedback = feedback_from(round_)
        save()

    state.status = "unresolved"
    event("done", f"not accepted after {max_rounds} rounds")

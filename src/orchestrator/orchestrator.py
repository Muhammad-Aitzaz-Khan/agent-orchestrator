"""The multi-agent orchestrator: a state machine that coordinates four roles.

    PLAN ──► BUILD ──► gate: visible tests ──► TEST ──► REVIEW ──► DONE
               ▲             │ fail                │ fail      │ revise
               └─────────────┴─────────────────────┴───────────┘   (up to max_rounds)

Two kinds of work use the same machine with different role prompts (see roles.TEAMS):
- "fix":    repair an existing project; the Planner reads the code and its docstrings.
- "create": make something new from a request (writing, a web page, code).

- The Planner turns the request into checkable requirements.
- The Coder (called the Builder in create mode) does the work.
- A deterministic gate runs the visible tests, if the project has any (no model call), so
  tokens are not spent checking work that does not pass the existing tests.
- The Tester checks every requirement by reading and by running code in a throwaway copy.
- The Reviewer reviews the change, but only once the Tester reports everything passes.
Anything that fails sends specific feedback back to the Coder for another round.

Two kinds of progress callbacks are available: on_event(phase, detail) for short text, and
on_update(dict) for structured events that a user interface can render (see _Updates).
"""
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from openai import OpenAI

from orchestrator.agent import default_model, make_client, new_run_id, run_agent
from orchestrator.roles import (
    TEAMS,
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
    Step,
    TestReport,
)
from orchestrator.tools import copy_project, list_files, run_tests, show_diff
from orchestrator.tracing import Tracer

DEFAULT_MAX_ROUNDS = 3  # the first attempt plus up to two revisions
DEFAULT_TOKEN_BUDGET = 100_000  # per task; protects a free-tier daily quota
MAX_UPDATE_TEXT = 4000  # longest tool output or message sent to a user interface
MAX_DELIVERABLE_CHARS = 200_000

EventHandler = Callable[[str, str], None]
UpdateHandler = Callable[[dict], None]


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


def has_tests(root: Path) -> bool:
    names = [Path(p).name for p in list_files(root).splitlines()]
    return any(n.startswith("test_") and n.endswith(".py") or n.endswith("_test.py")
               for n in names)


def _clip(text: str | None, limit: int = MAX_UPDATE_TEXT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n... (shortened)"


class _Updates:
    """Builds the structured events sent to on_update. Every event has a "type":

    run_started, phase, agent_started, agent_step, agent_submitted, agent_finished,
    gate, decision, run_finished.
    """

    def __init__(self, handler: UpdateHandler | None) -> None:
        self.handler = handler
        self.round = 0

    def send(self, type_: str, **data) -> None:
        if self.handler:
            self.handler({"type": type_, "round": self.round, "time": time.time(), **data})

    def step(self, role: Role, step: Step) -> None:
        self.send(
            "agent_step",
            role=role.name,
            text=_clip(step.text),
            tools=[
                {"name": c.name, "arguments": _clip(c.arguments), "output": _clip(c.output)}
                for c in step.tool_calls
                if c.name != role.submit_tool  # the submission gets its own event
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
    kind: str = "fix",
    on_update: UpdateHandler | None = None,
    checkers: bool = True,
) -> OrchestratorResult:
    """Run the four-agent team on a task in the project folder at root.

    checkers=False runs only the Planner and the Coder (plus the deterministic test gate).
    It exists to measure what the Tester and Reviewer add, at what cost.
    """
    if kind not in TEAMS:
        raise ValueError(f"kind must be one of {sorted(TEAMS)}, not {kind!r}")
    team = TEAMS[kind]
    root = root.resolve()
    client = client or make_client()
    budget = token_budget or int(os.getenv("ORCH_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET))
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_id = "orch-" + new_run_id()
    tracer = Tracer(run_id, runs_dir)
    state = OrchestratorResult(run_id=run_id, task=task, kind=kind)
    state_file = runs_dir / f"{run_id}.state.json"
    started = time.perf_counter()
    updates = _Updates(on_update)

    def event(phase: str, detail: str = "") -> None:
        tracer.log("phase", phase=phase, detail=detail)
        updates.send("phase", phase=phase, detail=detail)
        if on_event:
            on_event(phase, detail)

    def save() -> None:
        state.duration_s = round(time.perf_counter() - started, 2)
        state_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    def run_role(role: Role, message: str, baseline: Path):
        updates.send("agent_started", role=role.name, title=role.title)
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
            on_step=lambda step: updates.step(role, step),
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
        if result.output is not None:
            updates.send("agent_submitted", role=role.name, output=result.output)
        updates.send(
            "agent_finished", role=role.name, status=result.status,
            tokens=result.total_tokens, steps=len(result.steps), error=result.error,
        )
        if result.status == "error":
            raise _Abort(result.error or "agent error")
        if state.total_tokens > budget:
            raise _BudgetExhausted()
        return result

    tracer.log("orchestrator_start", task=task, kind=kind, root=str(root),
               max_rounds=max_rounds, token_budget=budget)
    updates.send(
        "run_started", run_id=run_id, task=task, kind=kind, max_rounds=max_rounds,
        roles=[{"name": r.name, "title": r.title} for r in team],
    )
    with tempfile.TemporaryDirectory(prefix="baseline_", ignore_cleanup_errors=True) as tmp:
        baseline = copy_project(root, Path(tmp) / "baseline")  # the untouched project, for diffs
        try:
            _run_rounds(task, root, baseline, state, team, run_role, event, updates, save,
                        max_rounds, checkers)
        except _Abort as e:
            state.status = "error"
            state.error = str(e)
            event("aborted", state.error[:200])
        except _BudgetExhausted:
            state.status = "budget_exhausted"
            event("budget_exhausted", f"{state.total_tokens} tokens used, budget {budget}")
        state.files, state.diff = _deliverables(root, baseline)

    save()
    tracer.log("orchestrator_end", status=state.status, rounds=len(state.rounds),
               total_tokens=state.total_tokens, duration_s=state.duration_s)
    updates.send(
        "run_finished", status=state.status, error=state.error, total_tokens=state.total_tokens,
        tokens_by_role=state.tokens_by_role(), duration_s=state.duration_s,
        rounds=len(state.rounds), files=state.files, diff=state.diff,
    )
    return state


def _deliverables(root: Path, baseline: Path) -> tuple[dict[str, str], str]:
    """Files that were created or changed, with their contents, plus the full diff."""
    files: dict[str, str] = {}
    total = 0
    for rel in list_files(root).splitlines():
        if rel == "(no files)":
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        before = baseline / rel
        unchanged = before.is_file() and before.read_text(encoding="utf-8", errors="replace") == text
        if unchanged or total + len(text) > MAX_DELIVERABLE_CHARS:
            continue
        files[rel] = text
        total += len(text)
    return files, show_diff(root, baseline)


def _run_rounds(task, root, baseline, state, team, run_role, event, updates, save,
                max_rounds, checkers=True) -> None:
    planner, coder, tester, reviewer = team

    # ---- PLAN ----
    event("plan", "planner is investigating")
    planned = run_role(planner, planner_task(task), baseline)
    if planned.output is not None:
        state.plan = Plan.model_validate(planned.output)
    else:
        state.plan = fallback_plan(task)
        state.plan_is_fallback = True
        updates.send("agent_submitted", role=planner.name, output=state.plan.model_dump(),
                     fallback=True)
    event("plan", f"{len(state.plan.requirements)} requirements"
          + (" (fallback plan)" if state.plan_is_fallback else ""))
    save()

    feedback = None
    for number in range(1, max_rounds + 1):
        round_ = Round(number=number)
        state.rounds.append(round_)
        updates.round = number

        def decide(decision: str, round_: Round = round_) -> None:
            round_.decision = decision
            updates.send("decision", decision=decision)
            save()

        # ---- BUILD ----
        event("code", f"round {number}")
        coded = run_role(coder, coder_task(task, state.plan, feedback), baseline)
        if coded.output is not None:
            round_.changes = ChangeSummary.model_validate(coded.output)

        # ---- GATE: visible tests, no model involved ----
        if has_tests(root):
            gate = run_tests(root)
            round_.visible_tests_passed = gate.startswith("Tests PASSED")
            round_.visible_tests_output = gate[-1500:]
            updates.send("gate", passed=round_.visible_tests_passed, output=_clip(gate, 1500))
            event("gate",
                  "visible tests pass" if round_.visible_tests_passed else "visible tests FAIL")
            if not round_.visible_tests_passed:
                decide("revise: visible tests fail")
                feedback = feedback_from(round_)
                continue

        if not checkers:  # Planner + Coder only: accept once the gate (if any) passes
            state.status = "completed"
            decide("accept: checkers disabled")
            event("done", round_.decision)
            return

        # ---- TEST ----
        event("test", f"checking {len(state.plan.requirements)} requirements")
        note = round_.changes.message if round_.changes else ""
        tested = run_role(tester, tester_task(task, state.plan, note), baseline)
        if tested.output is not None:
            round_.test_report = TestReport.model_validate(tested.output)
            failures = round_.test_report.failures
            event("test", f"{len(round_.test_report.checks) - len(failures)} passed, "
                  f"{len(failures)} failed")
            if failures:
                decide("revise: tester found failing requirements")
                feedback = feedback_from(round_)
                continue
        else:
            event("test", "tester did not submit a report; continuing unverified")

        # ---- REVIEW ----
        event("review", "reviewer is reading the changes")
        reviewed = run_role(reviewer, reviewer_task(task, state.plan, round_.test_report),
                            baseline)
        if reviewed.output is None:
            state.status = "completed"
            decide("accept: checks pass, reviewer did not submit")
            event("done", round_.decision)
            return
        round_.review = Review.model_validate(reviewed.output)
        blockers = [i for i in round_.review.issues if i.severity == "blocker"]
        if round_.review.verdict == "approve" or not blockers:
            state.status = "completed"
            decide("accept: approved by reviewer")
            event("done", round_.decision)
            return
        decide(f"revise: reviewer found {len(blockers)} blocker(s)")
        event("review", round_.decision)
        feedback = feedback_from(round_)

    state.status = "unresolved"
    event("done", f"not accepted after {max_rounds} rounds")

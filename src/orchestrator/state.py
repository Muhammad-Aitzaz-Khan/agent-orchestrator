"""What gets passed around: the record of an agent's run, and what each role hands to the
next one (a plan, a change summary, a test report, a review), validated by pydantic."""
from typing import Literal

from pydantic import BaseModel, Field

# ---------- a single agent run ----------


class ToolCall(BaseModel):
    """One tool the model asked for, and what it returned."""

    name: str
    arguments: str
    output: str


class Step(BaseModel):
    """One round of the loop: a model call plus any tool calls it made."""

    number: int
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tokens: int = 0
    latency_ms: int = 0


class RunResult(BaseModel):
    """Everything that happened during one agent run."""

    run_id: str
    task: str
    status: Literal["completed", "max_steps", "error"]
    role: str | None = None
    final_answer: str | None = None
    output: dict | None = None  # validated arguments of the role's submit tool, if any
    steps: list[Step] = Field(default_factory=list)
    total_tokens: int = 0
    duration_s: float = 0.0
    error: str | None = None


# ---------- what each role submits ----------


class Requirement(BaseModel):
    id: str = Field(description="Short identifier such as R1, R2, ...")
    description: str = Field(description="One precise, checkable statement of required behaviour")
    source: str = Field(
        description="Where it comes from: a docstring, a failing test, the task, or a bug report"
    )


class Plan(BaseModel):
    """Submitted by the Planner."""

    summary: str = Field(description="What is wrong, in two or three sentences")
    files_to_change: list[str] = Field(description="Source files that need changes")
    requirements: list[Requirement] = Field(
        min_length=1, description="Every behaviour the fixed code must have, edge cases included"
    )
    message: str = Field(default="", description="A note to the Coder (or Builder) who works next: the one thing they most need to know. One or two sentences in plain, friendly English, the way a colleague talks. No code, no requirement IDs.")


class ChangeSummary(BaseModel):
    """Submitted by the Coder."""

    summary: str = Field(description="What was changed and why")
    files_changed: list[str] = Field(description="Files that were modified")
    message: str = Field(default="", description="A note to the Tester who checks your work next: what you did and what to look at closely. One or two sentences in plain, friendly English, the way a colleague talks. No code, no requirement IDs.")


class RequirementCheck(BaseModel):
    requirement_id: str
    passed: bool
    evidence: str = Field(description="What was run and what it printed")


class TestReport(BaseModel):
    """Submitted by the Tester."""

    __test__ = False  # not a pytest test class

    checks: list[RequirementCheck] = Field(min_length=1)
    message: str = Field(default="", description="A note to the next teammate: if everything passed, tell the Reviewer; if something failed, tell the Coder what broke. One or two sentences in plain, friendly English, the way a colleague talks. No code, no requirement IDs.")

    @property
    def all_passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[RequirementCheck]:
        return [check for check in self.checks if not check.passed]


class ReviewIssue(BaseModel):
    severity: Literal["blocker", "minor"]
    description: str
    file: str | None = None


class Review(BaseModel):
    """Submitted by the Reviewer."""

    verdict: Literal["approve", "revise"]
    issues: list[ReviewIssue] = Field(default_factory=list)
    message: str = Field(default="", description="If you approve, a note to the person who asked for the work; if you send it back, a note to the Coder about what to change. One or two sentences in plain, friendly English, the way a colleague talks. No code, no requirement IDs.")


# ---------- the orchestrated run ----------


class AgentRunSummary(BaseModel):
    role: str
    run_id: str
    status: str
    steps: int
    tokens: int
    duration_s: float
    submitted: bool


class Round(BaseModel):
    """One attempt: the Coder changes the code, then the gates and checkers look at it."""

    number: int
    changes: ChangeSummary | None = None
    visible_tests_passed: bool | None = None
    visible_tests_output: str | None = None
    test_report: TestReport | None = None
    review: Review | None = None
    decision: str = ""


class OrchestratorResult(BaseModel):
    run_id: str
    task: str
    kind: Literal["fix", "create", "assist"] = "fix"
    status: Literal["completed", "unresolved", "budget_exhausted", "error"] = "unresolved"
    plan: Plan | None = None
    plan_is_fallback: bool = False
    rounds: list[Round] = Field(default_factory=list)
    agent_runs: list[AgentRunSummary] = Field(default_factory=list)
    total_tokens: int = 0
    total_steps: int = 0
    duration_s: float = 0.0
    error: str | None = None
    files: dict[str, str] = Field(default_factory=dict)  # created or changed files
    diff: str = ""

    def tokens_by_role(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for run in self.agent_runs:
            totals[run.role] = totals.get(run.role, 0) + run.tokens
        return totals

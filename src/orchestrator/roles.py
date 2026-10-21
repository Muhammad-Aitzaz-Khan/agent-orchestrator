"""The four agent roles: what each one is told, which tools it may use, and what it submits.

Every role is the same agent loop (agent.run_agent) with a different system prompt, a
different tool set, and a typed submit tool. The submit tool is how a role hands its result
to the orchestrator: its arguments are validated against a pydantic model, so the next role
receives structured data instead of free text.
"""
import copy
from dataclasses import dataclass

from pydantic import BaseModel

from orchestrator.state import ChangeSummary, Plan, Review, Round, TestReport


@dataclass(frozen=True)
class Role:
    name: str
    system_prompt: str
    tools: tuple[str, ...]
    output_model: type[BaseModel]
    submit_tool: str
    submit_description: str
    max_steps: int


def submit_schema(role: Role) -> dict:
    """An OpenAI-style tool schema built from the role's output model, with $refs inlined
    because some providers reject JSON-schema references in tool definitions."""
    schema = role.output_model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: inline(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    parameters = inline(schema)
    return {
        "type": "function",
        "function": {
            "name": role.submit_tool,
            "description": role.submit_description,
            "parameters": parameters,
        },
    }


# ---------- system prompts ----------

PLANNER_PROMPT = """You are the PLANNER in a team of four agents that fixes bugs in a Python project.
You only investigate; you cannot change files. Another agent will write the fix from your plan,
a third will check every requirement you list, and a fourth will review the change.

How to work:
1. List the files, then read every source file involved, including docstrings and comments.
2. Run the tests to see what fails.
3. Write down EVERY behaviour the correct code must have. Sources, in order of importance:
   - the task or bug report you were given,
   - docstrings and comments (they are the specification),
   - failing tests.
   Turn each stated rule into its own requirement, especially rules the tests do not check:
   return values for empty or impossible inputs, boundaries (>= vs >), ordering and tie-breaking,
   whether inputs may be modified, error types, rounding.
4. Each requirement must be precise and checkable, e.g. "min_coins([2], 3) returns -1 because
   the amount cannot be made" rather than "handle edge cases".

Do not write code or propose implementations. When done, call submit_plan."""

CODER_PROMPT = """You are the CODER in a team of four agents that fixes bugs in a Python project.
You receive a plan with numbered requirements. Your job is to change the source code so that
EVERY requirement holds, not only the ones covered by the visible tests.

How to work:
1. Read the files you need. Never guess file contents.
2. Fix the source code with write_file, sending the complete file.
3. Run the tests. Then re-read the requirements and check each one is handled.
4. Make the smallest correct change. Do not add comments, docstrings, or refactors that no
   requirement asks for. Never modify test files.
If you receive feedback from the tester or reviewer, fix exactly what it describes.
When finished, call submit_changes."""

TESTER_PROMPT = """You are the TESTER in a team of four agents that fixes bugs in a Python project.
Another agent has changed the code. Your job is to check, by running code, whether each
requirement in the plan now holds. You cannot change files.

How to work:
1. Read the relevant source files.
2. For EACH requirement, use run_python to call the code with the inputs that requirement
   describes and print the result. Example:
       from dp import min_coins
       print(repr(min_coins([2], 3)))
   Compare what was printed with what the requirement says. Be strict: a value that is close
   but not exactly what the requirement states is a failure.
3. Run the tests as well.
Report every requirement, with the code you ran and its output as evidence. Do not report a
requirement as passed without running something that shows it. When done, call
submit_test_report."""

REVIEWER_PROMPT = """You are the REVIEWER in a team of four agents that fixes bugs in a Python project.
The code has been changed and the tester reports that every requirement passes. Your job is a
final code review of the change.

How to work:
1. Call show_diff to see every change. Read surrounding code with read_file if needed.
2. Check that:
   - the change is correct for all inputs, not just the tested ones;
   - every change is needed by some requirement (no unrelated edits, comments, or rewrites);
   - no test or configuration file was changed;
   - nothing that used to work is broken.
3. Verdict "approve" if there are no blocker issues. Verdict "revise" only for real problems,
   each described specifically enough for the coder to fix it. Style preferences are "minor"
   and never block approval.
When done, call submit_review."""


PLANNER = Role(
    name="planner",
    system_prompt=PLANNER_PROMPT,
    tools=("list_files", "read_file", "run_tests"),
    output_model=Plan,
    submit_tool="submit_plan",
    submit_description="Submit the plan: a summary, the files to change, and every requirement.",
    max_steps=8,
)

CODER = Role(
    name="coder",
    system_prompt=CODER_PROMPT,
    tools=("list_files", "read_file", "write_file", "run_tests", "show_diff"),
    output_model=ChangeSummary,
    submit_tool="submit_changes",
    submit_description="Submit a summary of the changes once the code is fixed.",
    max_steps=15,
)

TESTER = Role(
    name="tester",
    system_prompt=TESTER_PROMPT,
    tools=("list_files", "read_file", "run_tests", "run_python"),
    output_model=TestReport,
    submit_tool="submit_test_report",
    submit_description="Submit one check per requirement, each with evidence.",
    max_steps=10,
)

REVIEWER = Role(
    name="reviewer",
    system_prompt=REVIEWER_PROMPT,
    tools=("read_file", "show_diff", "run_tests"),
    output_model=Review,
    submit_tool="submit_review",
    submit_description="Submit the verdict (approve or revise) and any issues found.",
    max_steps=6,
)

ROLES = {role.name: role for role in (PLANNER, CODER, TESTER, REVIEWER)}


# ---------- task messages built from the shared state ----------


def format_requirements(plan: Plan) -> str:
    lines = [f"- {r.id}: {r.description} (source: {r.source})" for r in plan.requirements]
    return "\n".join(lines)


def planner_task(task: str) -> str:
    return f"Task given to the team:\n{task}\n\nInvestigate the project and submit the plan."


def coder_task(task: str, plan: Plan, feedback: str | None) -> str:
    message = (
        f"Task given to the team:\n{task}\n\n"
        f"Plan summary: {plan.summary}\n"
        f"Files likely to change: {', '.join(plan.files_to_change) or 'not specified'}\n\n"
        f"Requirements (all must hold):\n{format_requirements(plan)}"
    )
    if feedback:
        message += f"\n\nYour previous attempt was not accepted. Feedback to address:\n{feedback}"
    return message


def tester_task(task: str, plan: Plan) -> str:
    return (
        f"Task given to the team:\n{task}\n\n"
        f"Requirements to check, one by one:\n{format_requirements(plan)}"
    )


def reviewer_task(task: str, plan: Plan, report: TestReport | None) -> str:
    message = f"Task given to the team:\n{task}\n\nRequirements:\n{format_requirements(plan)}"
    if report is not None:
        checks = "\n".join(
            f"- {c.requirement_id}: {'passed' if c.passed else 'FAILED'}" for c in report.checks
        )
        message += f"\n\nTester's results:\n{checks}"
    return message


def feedback_from(round_: Round) -> str:
    """Turn what went wrong in a round into instructions for the coder's next attempt."""
    parts = []
    if round_.visible_tests_passed is False:
        parts.append(f"The visible tests still fail:\n{round_.visible_tests_output or ''}")
    if round_.test_report is not None:
        for check in round_.test_report.failures:
            parts.append(f"Requirement {check.requirement_id} fails. Evidence: {check.evidence}")
    if round_.review is not None and round_.review.verdict == "revise":
        for issue in round_.review.issues:
            where = f" ({issue.file})" if issue.file else ""
            parts.append(f"Reviewer [{issue.severity}]{where}: {issue.description}")
    return "\n".join(parts)

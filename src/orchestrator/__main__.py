"""Command-line interface.

Examples:
    python -m orchestrator --root path/to/project "Fix the failing tests."
    python -m orchestrator --mode multi --root path/to/project "Fix the failing tests."
"""
import argparse
from pathlib import Path

from orchestrator.agent import run_agent
from orchestrator.orchestrator import DEFAULT_MAX_ROUNDS, run_orchestrator
from orchestrator.state import OrchestratorResult, RunResult, Step


def short(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def print_step(step: Step) -> None:
    for call in step.tool_calls:
        print(f"[step {step.number}] {call.name}({short(call.arguments, 60)})")
        print(f"          -> {short(call.output.splitlines()[0] if call.output else '')}")


def print_event(phase: str, detail: str) -> None:
    print(f"[{phase:<7}] {detail}")


def report_single(result: RunResult) -> None:
    print()
    if result.final_answer:
        print(result.final_answer)
    if result.error:
        print(f"Error: {result.error}")
    print(
        f"\n--- status: {result.status} | steps: {len(result.steps)} | "
        f"tokens: {result.total_tokens} | time: {result.duration_s}s | "
        f"trace: runs/{result.run_id}.jsonl"
    )


def report_team(result: OrchestratorResult) -> None:
    print()
    if result.plan:
        print("Requirements:")
        for r in result.plan.requirements:
            print(f"  {r.id}: {r.description}")
    for round_ in result.rounds:
        print(f"Round {round_.number}: {round_.decision or 'in progress'}")
    if result.error:
        print(f"Error: {result.error}")
    roles = ", ".join(f"{role} {tokens}" for role, tokens in result.tokens_by_role().items())
    print(
        f"\n--- status: {result.status} | rounds: {len(result.rounds)} | "
        f"steps: {result.total_steps} | tokens: {result.total_tokens} ({roles}) | "
        f"time: {result.duration_s}s | state: runs/{result.run_id}.state.json"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="orchestrator", description="Run AI agents on a project.")
    parser.add_argument("task", nargs="*", help="what the agent should do")
    parser.add_argument("--root", type=Path, required=True, help="project folder the agent works in")
    parser.add_argument(
        "--mode", choices=["single", "multi"], default="single",
        help="single: one agent. multi: planner, coder, tester, and reviewer",
    )
    parser.add_argument("--max-steps", type=int, default=15, help="single mode only")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS, help="multi mode only")
    args = parser.parse_args()

    if not args.root.is_dir():
        parser.error(f"--root folder not found: {args.root}")

    task = " ".join(args.task) or input("Task: ")
    if args.mode == "multi":
        report_team(
            run_orchestrator(task, args.root, max_rounds=args.max_rounds, on_event=print_event)
        )
    else:
        report_single(run_agent(task, root=args.root, max_steps=args.max_steps, on_step=print_step))


if __name__ == "__main__":
    main()

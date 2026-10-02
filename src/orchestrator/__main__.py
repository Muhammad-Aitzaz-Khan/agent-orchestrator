"""Command-line interface.

Example:
    python -m orchestrator --root playground/buggy_calculator "Fix the failing tests."
"""
import argparse
from pathlib import Path

from orchestrator.agent import run_agent
from orchestrator.state import Step


def short(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def print_step(step: Step) -> None:
    for call in step.tool_calls:
        print(f"[step {step.number}] {call.name}({short(call.arguments, 60)})")
        print(f"          -> {short(call.output.splitlines()[0] if call.output else '')}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="orchestrator", description="Run an AI agent on a project.")
    parser.add_argument("task", nargs="*", help="what the agent should do")
    parser.add_argument("--root", type=Path, required=True, help="project folder the agent works in")
    parser.add_argument("--max-steps", type=int, default=15)
    args = parser.parse_args()

    if not args.root.is_dir():
        parser.error(f"--root folder not found: {args.root}")

    task = " ".join(args.task) or input("Task: ")
    result = run_agent(task, root=args.root, max_steps=args.max_steps, on_step=print_step)

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


if __name__ == "__main__":
    main()
"""Command-line interface: python -m orchestrator "your task"."""
import sys

from orchestrator.agent import run_agent
from orchestrator.state import Step


def print_step(step: Step) -> None:
    for call in step.tool_calls:
        print(f"[step {step.number}] {call.name}({call.arguments})  ({step.latency_ms} ms)")


def main() -> None:
    task = " ".join(sys.argv[1:]) or input("Task: ")
    result = run_agent(task, on_step=print_step)

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
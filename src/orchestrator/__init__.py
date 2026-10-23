"""Relay: a personal assistant run by a small team of AI agents.

run_agent runs one agent; run_orchestrator runs the team of four (plan, build, check,
review). The assistant on top of them lives in orchestrator.assistant, the website in
orchestrator.web.
"""
from orchestrator.agent import run_agent
from orchestrator.orchestrator import run_orchestrator
from orchestrator.state import OrchestratorResult, RunResult, Step, ToolCall

__all__ = ["OrchestratorResult", "RunResult", "Step", "ToolCall", "run_agent", "run_orchestrator"]

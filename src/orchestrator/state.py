"""Data models describing an agent run."""
from typing import Literal

from pydantic import BaseModel, Field


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
    final_answer: str | None = None
    steps: list[Step] = Field(default_factory=list)
    total_tokens: int = 0
    duration_s: float = 0.0
    error: str | None = None
"""Tests for the agent loop, using a fake model client. These never call the real API."""
import json

import openai
import pytest

from orchestrator import agent
from orchestrator.agent import run_agent

try:  # newer openai versions use httpx2, older ones httpx
    import httpx2 as httpx
except ImportError:
    import httpx


def completion(content=None, tool_calls=None, tokens=100):
    """Build a real ChatCompletion object like the API returns."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(tool_calls)
        ]
    return openai.types.chat.ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "fake",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens},
    })


def rate_limit_error(message, retry_after="1"):
    response = httpx.Response(
        429, headers={"retry-after": retry_after}, request=httpx.Request("POST", "http://test")
    )
    return openai.RateLimitError(message, response=response, body=None)


class FakeClient:
    """Returns (or raises) the scripted responses in order and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    """Run in a temp folder so traces don't land in the real runs/ folder."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.py").write_text("print('hi')\n")
    return tmp_path


def run(client, **kwargs):
    waits = []
    result = run_agent("task", root=kwargs.pop("root", None), client=client, model="fake",
                       sleep=waits.append, **kwargs)
    return result, waits


def test_tool_call_then_answer(in_tmp):
    client = FakeClient([
        completion(tool_calls=[("read_file", {"path": "hello.py"})]),
        completion(content="It prints hi."),
    ])
    result, _ = run(client, root=in_tmp)
    assert result.status == "completed"
    assert result.final_answer == "It prints hi."
    assert result.steps[0].tool_calls[0].output == "print('hi')\n"
    assert result.total_tokens == 200
    # the tool result was sent back to the model on the second call
    assert client.requests[1]["messages"][-1]["content"] == "print('hi')\n"


def test_stops_at_max_steps(in_tmp):
    client = FakeClient([completion(tool_calls=[("list_files", {})])] * 3)
    result, _ = run(client, root=in_tmp, max_steps=3)
    assert result.status == "max_steps"
    assert len(result.steps) == 3


def test_waits_and_retries_on_per_minute_limit(in_tmp):
    client = FakeClient([
        rate_limit_error("Rate limit on tokens per minute (TPM)", retry_after="2"),
        completion(content="done"),
    ])
    result, waits = run(client, root=in_tmp)
    assert result.status == "completed"
    assert waits == [3.0]  # retry-after + 1 second


def test_gives_up_after_too_many_rate_limits(in_tmp):
    errors = [rate_limit_error("tokens per minute (TPM)")] * (agent.MAX_RATE_LIMIT_WAITS + 1)
    result, waits = run(FakeClient(errors), root=in_tmp)
    assert result.status == "error"
    assert "RateLimitError" in result.error
    assert len(waits) == agent.MAX_RATE_LIMIT_WAITS


def test_daily_limit_stops_without_waiting(in_tmp):
    client = FakeClient([rate_limit_error("Limit reached on tokens per day (TPD)", "300")])
    result, waits = run(client, root=in_tmp)
    assert result.status == "error"
    assert result.error.startswith("DailyLimitReached")
    assert waits == []


def test_long_retry_after_is_capped(in_tmp):
    client = FakeClient([rate_limit_error("tokens per minute", retry_after="9999"),
                         completion(content="ok")])
    _, waits = run(client, root=in_tmp)
    assert waits == [agent.MAX_WAIT_S]

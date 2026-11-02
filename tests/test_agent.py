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


def bad_request(message):
    response = httpx.Response(400, request=httpx.Request("POST", "http://test"))
    return openai.BadRequestError(message, response=response, body=None)


TOOL_VALIDATION = "Tool call validation failed: parameters for tool write_file did not match schema"


def test_retries_malformed_tool_call(in_tmp):
    client = FakeClient([bad_request(TOOL_VALIDATION), completion(content="done")])
    result, waits = run(client, root=in_tmp)
    assert result.status == "completed"
    assert len(client.requests) == 2
    assert waits == []  # no waiting needed, just ask again


def test_gives_up_on_repeated_malformed_tool_calls(in_tmp):
    client = FakeClient([bad_request(TOOL_VALIDATION)] * (agent.MAX_BAD_TOOL_CALL_RETRIES + 1))
    result, _ = run(client, root=in_tmp)
    assert result.status == "error"
    assert "BadRequestError" in result.error


def test_other_bad_requests_are_not_retried(in_tmp):
    client = FakeClient([bad_request("model does not exist")])
    result, _ = run(client, root=in_tmp)
    assert result.status == "error"
    assert len(client.requests) == 1


def test_requests_stay_under_context_budget(in_tmp):
    (in_tmp / "big.py").write_text("x = 1\n" * 1500)  # read_file returns 5,000 characters
    client = FakeClient(
        [completion(tool_calls=[("read_file", {"path": "big.py"})])] * 8 + [completion(content="ok")]
    )
    result, _ = run(client, root=in_tmp, max_steps=10, context_chars=12_000)
    assert result.status == "completed"
    sizes = [agent.conversation_size(r["messages"]) for r in client.requests]
    # without compaction the last request would be 8 reads x 5,000 characters = 40,000+;
    # with it, the size levels off instead of growing every step
    assert max(sizes) < 14_000
    assert sizes[-1] - sizes[-4] < 500


def server_error(status=503, message="This model is currently experiencing high demand."):
    response = httpx.Response(status, request=httpx.Request("POST", "http://test"))
    return openai.InternalServerError(message, response=response, body=None)


def test_waits_longer_each_time_provider_is_overloaded(in_tmp):
    client = FakeClient([server_error(), server_error(), completion(content="ok")])
    result, waits = run(client, root=in_tmp)
    assert result.status == "completed"
    assert waits == [10.0, 20.0]


def test_gives_up_when_provider_stays_down(in_tmp):
    client = FakeClient([server_error()] * (agent.MAX_UNAVAILABLE_RETRIES + 1))
    result, waits = run(client, root=in_tmp)
    assert result.status == "error"
    assert "InternalServerError" in result.error
    assert len(waits) == agent.MAX_UNAVAILABLE_RETRIES


def test_malformed_tool_call_retry_explains_the_problem(in_tmp):
    client = FakeClient([bad_request(TOOL_VALIDATION), completion(content="done")])
    run(client, root=in_tmp)
    retry_messages = client.requests[1]["messages"]
    assert "Your last tool call was rejected" in retry_messages[-1]["content"]
    assert len(client.requests[0]["messages"]) == 2  # the note is not added to the history
    assert len(retry_messages) == 3


def test_loop_detection_nudges_after_repeated_identical_calls(in_tmp):
    same = completion(tool_calls=[("read_file", {"path": "hello.py"})])
    client = FakeClient([same, same, same, completion(content="ok")])
    result, _ = run(client, root=in_tmp)
    assert result.status == "completed"
    last = client.requests[3]["messages"][-1]
    assert last["role"] == "user" and "same tool call 3 times" in last["content"]


def test_different_calls_are_not_a_loop(in_tmp):
    client = FakeClient([
        completion(tool_calls=[("read_file", {"path": "hello.py"})]),
        completion(tool_calls=[("list_files", {})]),
        completion(tool_calls=[("read_file", {"path": "hello.py"})]),
        completion(content="ok"),
    ])
    run(client, root=in_tmp)
    assert all("same tool call" not in str(r["messages"][-1].get("content")) for r in client.requests)


def test_role_is_warned_before_running_out_of_steps(in_tmp):
    from orchestrator.roles import TESTER

    calls = [completion(tool_calls=[("run_python", {"code": f"print({i})"})]) for i in range(4)]
    client = FakeClient(calls)
    result, _ = run(client, root=in_tmp, max_steps=4, role=TESTER)
    assert result.status == "max_steps"
    warned = [r for r in client.requests if "steps left" in str(r["messages"][-1].get("content"))]
    assert len(warned) == 1  # sent once, after step 2 of 4
    assert client.requests.index(warned[0]) == 2


def test_a_tool_call_that_is_not_json_gets_an_example_of_the_right_shape(in_tmp):
    not_json = bad_request("Failed to parse tool call arguments as JSON (tool_use_failed)")
    client = FakeClient([not_json, completion(content="done")])
    run(client, root=in_tmp)
    note = client.requests[1]["messages"][-1]["content"]
    assert "one JSON object" in note and '{"code":' in note


def test_the_last_step_must_be_the_submission(in_tmp):
    from orchestrator.roles import TEAMS
    role = TEAMS["assist"][2]
    looking = completion(tool_calls=[("list_files", {})])
    client = FakeClient([looking] * (role.max_steps - 1) + [completion(content="ok")])
    run_agent("check it", root=in_tmp, client=client, sleep=lambda s: None, role=role,
              max_steps=role.max_steps, runs_dir=in_tmp / "runs")
    assert client.requests[-1]["tool_choice"]["function"]["name"] == role.submit_tool
    assert "tool_choice" not in client.requests[0]


TOO_LARGE = ("Request too large for model `openai/gpt-oss-120b` on tokens per minute (TPM): "
             "Limit 8000, Requested 9214, please reduce your message size and try again.")


def too_large_status():
    response = httpx.Response(413, request=httpx.Request("POST", "http://test"))
    return openai.APIStatusError(TOO_LARGE, response=response, body=None)


def test_request_too_large_is_retried_smaller_without_waiting(in_tmp):
    """Found in real use: a news job died after the Builder read a feed full of long links and
    wrote the briefing. Groq said the single request was over its limit; waiting never helps."""
    (in_tmp / "news.md").write_text("- headline https://news.google.com/rss/articles/" + "CBMiq" * 900 + "\n")
    client = FakeClient([
        completion(tool_calls=[("read_file", {"path": "news.md"})]),
        completion(tool_calls=[("write_file", {"path": "out.md", "content": "brief " * 700})]),
        rate_limit_error(TOO_LARGE),
        completion(content="done"),
    ])
    result, waits = run(client, root=in_tmp, context_chars=12_000)
    assert result.status == "completed"
    assert waits == []
    refused, retried = (agent.conversation_size(r["messages"]) for r in client.requests[2:4])
    assert retried < refused * 0.6
    # the long feed it read was shortened, while its latest step (the write) is still there
    sent = json.dumps(client.requests[3]["messages"])
    assert "CBMiq" * 200 not in sent and "Wrote 4200 characters" in sent


def test_413_from_the_provider_counts_as_too_large(in_tmp):
    client = FakeClient([too_large_status(), completion(content="done")])
    result, waits = run(client, root=in_tmp)
    assert result.status == "completed" and waits == []


def test_gives_up_when_even_the_smallest_request_is_too_large(in_tmp):
    client = FakeClient([too_large_status()] * len(agent.SQUEEZES))
    result, _ = run(client, root=in_tmp)
    assert result.status == "error"
    assert result.error.startswith("RequestTooLarge")
    assert len(client.requests) == len(agent.SQUEEZES)

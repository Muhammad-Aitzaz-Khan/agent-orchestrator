"""Tests for keeping the conversation under the size budget."""
import json

from orchestrator.context import compact, conversation_size

SYSTEM = {"role": "system", "content": "system prompt"}
TASK = {"role": "user", "content": "fix the bug"}


def tool_output(text):
    return {"role": "tool", "tool_call_id": "c", "content": text}


def write_call(content):
    return {
        "role": "assistant",
        "tool_calls": [{
            "id": "c", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"path": "a.py", "content": content})},
        }],
    }


def small_messages(n):
    return [{"role": "assistant", "content": f"step {i}"} for i in range(n)]


def test_under_budget_is_unchanged():
    messages = [SYSTEM, TASK, tool_output("short")]
    assert compact(messages, 10_000) == messages


def test_shortens_old_tool_outputs_first():
    messages = [SYSTEM, TASK, tool_output("A" * 5000), tool_output("B" * 5000)] + small_messages(6)
    result = compact(messages, 7_000)
    assert result[2]["content"].startswith("[shortened")
    assert result[3]["content"] == "B" * 5000  # newer output kept once under budget
    assert conversation_size(result) <= 7_000


def test_keeps_system_task_and_recent_messages():
    recent = tool_output("R" * 5000)
    messages = [SYSTEM, TASK, tool_output("A" * 5000)] + small_messages(5) + [recent]
    result = compact(messages, 100)
    assert result[0] == SYSTEM and result[1] == TASK
    assert result[-1] == recent


def test_old_write_file_content_is_omitted():
    messages = [SYSTEM, TASK, write_call("print('x')\n" * 500)] + small_messages(6)
    result = compact(messages, 1_000)
    args = json.loads(result[2]["tool_calls"][0]["function"]["arguments"])
    assert args["path"] == "a.py"
    assert args["content"].startswith("[omitted")


def test_does_not_modify_input():
    messages = [SYSTEM, TASK, tool_output("A" * 5000)] + small_messages(6)
    before = json.dumps(messages)
    compact(messages, 100)
    assert json.dumps(messages) == before


def step(i, output):
    call = {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
    return [{"role": "assistant", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": output}]


def test_drops_oldest_whole_steps_when_still_too_big():
    messages = [SYSTEM, TASK] + [m for i in range(30) for m in step(i, "x" * 400)]
    result = compact(messages, 3_000)
    assert conversation_size(result) <= 3_000
    assert result[2]["content"].startswith("[Note:")
    # every tool result still follows the assistant message that requested it
    for i, m in enumerate(result):
        if m["role"] == "tool":
            assert result[i - 1]["role"] in ("assistant", "tool")
    assert result[-1] == messages[-1]

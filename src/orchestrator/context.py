"""Keeps the conversation sent to the model under a size budget.

Every step resends the whole conversation, so long runs grow until a single request is too
large for the provider (Groq's free tier rejects any request over ~8,000 tokens). When the
conversation is over budget, the oldest bulky parts are shortened first:

1. Old tool outputs (file contents, test logs) are cut to a short preview, and old
   write_file calls have their file content replaced with a placeholder.
2. If that is not enough, old tool outputs are removed entirely and old assistant
   messages lose their reasoning text.
3. If it is still too big, the oldest whole steps (a model call plus its tool results, which
   the API requires to stay together) are dropped and replaced by a one-line note.

The system prompt, the task, and the most recent steps are always kept in full, so the model
still knows what it is doing and can re-read any file it needs.
"""
import json

PREVIEW_CHARS = 300
KEEP_RECENT_MESSAGES = 4  # the newest messages (about two steps) are never shortened


def conversation_size(messages: list[dict]) -> int:
    """Approximate size in characters (roughly 4 characters per token)."""
    return len(json.dumps(messages, default=str))


def _shorten_tool_output(message: dict) -> bool:
    content = message.get("content") or ""
    if len(content) <= PREVIEW_CHARS or content.startswith("[shortened"):
        return False
    message["content"] = (
        f"[shortened: older output, {len(content)} characters. Call the tool again if needed.]\n"
        + content[:PREVIEW_CHARS]
    )
    return True


def _shorten_write_calls(message: dict) -> bool:
    changed = False
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        if function.get("name") != "write_file":
            continue
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            continue
        content = args.get("content")
        if isinstance(content, str) and not content.startswith("[omitted"):
            args["content"] = f"[omitted: {len(content)} characters written earlier]"
            function["arguments"] = json.dumps(args)
            changed = True
    return changed


def _remove_tool_output(message: dict) -> None:
    if not (message.get("content") or "").startswith("[removed"):
        message["content"] = "[removed: older output. Call the tool again if needed.]"


def _strip_reasoning(message: dict) -> None:
    message.pop("reasoning", None)
    content = message.get("content")
    if isinstance(content, str) and len(content) > PREVIEW_CHARS:
        message["content"] = content[:PREVIEW_CHARS] + " [...]"


def compact(messages: list[dict], max_chars: int) -> list[dict]:
    """Return a copy of messages that fits in max_chars where possible. Never mutates the input."""
    result = json.loads(json.dumps(messages, default=str))  # deep copy of plain data
    if conversation_size(result) <= max_chars:
        return result

    # messages[0] is the system prompt and messages[1] the task; both stay intact.
    old = range(2, max(2, len(result) - KEEP_RECENT_MESSAGES))
    passes = [
        {"tool": _shorten_tool_output, "assistant": _shorten_write_calls},
        {"tool": _remove_tool_output, "assistant": _strip_reasoning},
    ]
    for shorteners in passes:
        for i in old:  # oldest first
            shorten = shorteners.get(result[i].get("role"))
            if shorten:
                shorten(result[i])
            if conversation_size(result) <= max_chars:
                return result
    return _drop_oldest_steps(result, max_chars)


def _drop_oldest_steps(messages: list[dict], max_chars: int) -> list[dict]:
    head, body = messages[:2], messages[2:]
    dropped = 0
    while len(body) > KEEP_RECENT_MESSAGES:
        candidate = head + [_dropped_note(dropped)] + body
        if dropped and conversation_size(candidate) <= max_chars:
            break
        # remove one step: an assistant message and the tool results that answer it
        end = 1
        while end < len(body) and body[end].get("role") == "tool":
            end += 1
        if len(body) - end < KEEP_RECENT_MESSAGES:
            break
        body = body[end:]
        dropped += 1
    return head + ([_dropped_note(dropped)] if dropped else []) + body


def _dropped_note(count: int) -> dict:
    return {
        "role": "user",
        "content": f"[Note: {count} earlier step(s) were removed to save space. "
        "Re-read files or re-run tests if you need that information.]",
    }

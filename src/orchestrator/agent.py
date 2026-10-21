"""The core agent loop."""
import json
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import ValidationError

from orchestrator.context import compact, conversation_size
from orchestrator.roles import Role, submit_schema
from orchestrator.state import RunResult, Step, ToolCall
from orchestrator.tools import DEFAULT_TOOLS, ToolContext, execute_tool, schemas_for
from orchestrator.tracing import Tracer

SYSTEM_PROMPT = (
    "You are a software engineering agent working inside a project folder. "
    "Use the tools to inspect files, make changes, and verify them.\n"
    "Workflow for fixing bugs:\n"
    "1. List the files and read the relevant source code.\n"
    "2. Run the tests to see exactly what fails.\n"
    "3. Fix the source code with write_file, sending the complete file.\n"
    "4. Run the tests again to confirm the fix.\n"
    "Never modify test files. Never guess file contents. If a tool returns an error, "
    "say so honestly. When finished, summarize what you changed and the final test result."
)
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_MAX_CONTEXT_CHARS = 20_000  # about 5,000 tokens; Groq's free tier caps a request at ~8,000

MAX_RATE_LIMIT_WAITS = 5  # retries per model call for short (per-minute) limits
MAX_WAIT_S = 90  # never sleep longer than this for one retry
MAX_BAD_TOOL_CALL_RETRIES = 2  # retries when the model produces a malformed tool call
STEPS_LEFT_WARNING = 2  # remind a role to submit when this many steps remain
LOOP_REPEATS = 3  # identical tool calls in this many consecutive steps count as a loop
MAX_SUBMIT_NUDGES = 2  # reminders to a role that stops without calling its submit tool
MAX_UNAVAILABLE_RETRIES = 4  # retries when the provider is overloaded or unreachable (503, network)


class DailyLimitReached(Exception):
    """The provider's per-day quota is used up. Retrying today will not help."""


# ---------- configuration ----------

def make_client() -> OpenAI:
    """Any OpenAI-compatible provider works: set LLM_API_KEY and LLM_BASE_URL in .env."""
    load_dotenv()
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("No API key found. Set LLM_API_KEY (or GROQ_API_KEY) in your .env file.")
    return OpenAI(api_key=api_key, base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL))


def default_model() -> str:
    load_dotenv()
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def max_context_chars() -> int:
    load_dotenv()
    return int(os.getenv("LLM_MAX_CONTEXT_CHARS", DEFAULT_MAX_CONTEXT_CHARS))


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


# ---------- calling the model ----------

def is_daily_limit(error: Exception) -> bool:
    text = str(error).lower().replace("_", " ")
    return any(marker in text for marker in ("per day", "(tpd)", "(rpd)", "perday"))


def is_bad_tool_call(error: Exception) -> bool:
    """The provider rejected a tool call the model generated (wrong or missing arguments)."""
    text = str(error).lower()
    return "tool call validation failed" in text or "tool_use_failed" in text


def _api_error_message(error: Exception) -> str:
    """The provider's human-readable message, without the surrounding JSON."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])[:300]
    return str(error)[:300]


def retry_after_seconds(error: Exception, attempt: int) -> float:
    """How long to wait before retrying: the provider's retry-after header if present,
    otherwise exponential backoff (2, 4, 8, ... seconds)."""
    response = getattr(error, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    try:
        wait = float(header) + 1 if header is not None else 2.0 ** (attempt + 1)
    except ValueError:
        wait = 2.0 ** (attempt + 1)
    return min(wait, MAX_WAIT_S)


def call_model(
    client: OpenAI, tracer: Tracer, sleep: Callable[[float], None] = time.sleep, **kwargs
):
    """Call the model, recovering from problems that a retry can fix.

    - Per-minute rate limits: wait as long as the provider asks, then retry.
    - Malformed tool calls rejected by the provider: ask again, adding a note that says
      what was wrong. Resending the identical request tends to reproduce the same mistake.
    - Provider overloaded or unreachable (503 "high demand", network errors): wait
      10, 20, 40, 80 seconds and retry.
    Raises DailyLimitReached when the daily quota is exhausted.
    """
    rate_limit_waits = 0
    bad_tool_calls = 0
    unavailable_waits = 0
    original_messages = kwargs.get("messages", [])
    while True:
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            if is_daily_limit(e):
                raise DailyLimitReached(str(e)) from e
            if rate_limit_waits == MAX_RATE_LIMIT_WAITS:
                raise
            wait = retry_after_seconds(e, rate_limit_waits)
            rate_limit_waits += 1
            tracer.log("rate_limited", attempt=rate_limit_waits, wait_s=wait)
            sleep(wait)
        except BadRequestError as e:
            if not is_bad_tool_call(e) or bad_tool_calls == MAX_BAD_TOOL_CALL_RETRIES:
                raise
            bad_tool_calls += 1
            tracer.log("bad_tool_call_retry", attempt=bad_tool_calls, error=str(e)[:300])
            correction = {
                "role": "user",
                "content": "Your last reply was rejected because a tool call was malformed "
                f"({_api_error_message(e)}). Every tool call must include all of its required "
                "arguments with the correct names. Try again.",
            }
            kwargs = {**kwargs, "messages": [*original_messages, correction]}
        except (InternalServerError, APIConnectionError, APITimeoutError) as e:
            if unavailable_waits == MAX_UNAVAILABLE_RETRIES:
                raise
            wait = min(10.0 * 2**unavailable_waits, MAX_WAIT_S)
            unavailable_waits += 1
            tracer.log("provider_unavailable", attempt=unavailable_waits, wait_s=wait,
                       error=str(e)[:200])
            sleep(wait)


# ---------- the loop ----------

def _accept_submission(role: Role, raw_args: str | None, result: RunResult) -> str:
    """Validate a role's submission. Invalid ones go back to the model as an error to fix."""
    try:
        data = json.loads(raw_args or "{}")
        submission = role.output_model.model_validate(data)
    except json.JSONDecodeError:
        return "Error: submission arguments were not valid JSON. Submit again."
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'submission'}: {err['msg']}"
            for err in e.errors()[:5]
        )
        return f"Error: invalid submission ({problems}). Fix it and submit again."
    result.output = submission.model_dump()
    return "Submitted."


def run_agent(
    task: str,
    root: Path | None = None,
    max_steps: int = 10,
    model: str | None = None,
    client: OpenAI | None = None,
    on_step: Callable[[Step], None] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    sleep: Callable[[float], None] = time.sleep,
    context_chars: int | None = None,
    role: Role | None = None,
    baseline: Path | None = None,
) -> RunResult:
    """Run one agent on a task and return everything that happened.

    Without a role this is the original single agent. With a role, the agent uses the role's
    prompt and tools, and finishes by calling the role's submit tool; the validated submission
    is returned in result.output.
    """
    root = (root or Path.cwd()).resolve()
    client = client or make_client()
    model = model or default_model()
    context_chars = context_chars or max_context_chars()

    tool_names = list(role.tools) if role else list(DEFAULT_TOOLS)
    schemas = schemas_for(tool_names) + ([submit_schema(role)] if role else [])
    tool_context = ToolContext(root=root, allowed=frozenset(tool_names), baseline=baseline)
    if role:
        system_prompt = role.system_prompt

    run_id = new_run_id()
    tracer = Tracer(run_id)
    result = RunResult(
        run_id=run_id, task=task, status="max_steps", role=role.name if role else None
    )
    started = time.perf_counter()
    tracer.log(
        "run_start", task=task, model=model, root=str(root), max_steps=max_steps,
        role=role.name if role else None, tools=tool_names,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    nudges = 0
    recent_calls: list[tuple] = []  # tool calls of the latest steps, for loop detection

    try:
        for number in range(1, max_steps + 1):
            to_send = compact(messages, context_chars)  # full history stays in `messages`
            sent_chars = conversation_size(to_send)
            t0 = time.perf_counter()
            response = call_model(
                client, tracer, sleep, model=model, messages=to_send, tools=schemas
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)

            message = response.choices[0].message
            tokens = response.usage.total_tokens if response.usage else 0
            messages.append(message.model_dump(exclude_none=True))

            step = Step(number=number, text=message.content, tokens=tokens, latency_ms=latency_ms)
            tool_calls = message.tool_calls or []
            tracer.log(
                "model_call",
                step=number,
                latency_ms=latency_ms,
                tokens=tokens,
                sent_chars=sent_chars,
                text=message.content,
                tools_requested=[c.function.name for c in tool_calls],
            )

            for call in tool_calls:
                if role and call.function.name == role.submit_tool:
                    output = _accept_submission(role, call.function.arguments, result)
                else:
                    output = execute_tool(
                        call.function.name, call.function.arguments, root, tool_context
                    )
                step.tool_calls.append(
                    ToolCall(name=call.function.name, arguments=call.function.arguments, output=output)
                )
                tracer.log(
                    "tool_call",
                    step=number,
                    name=call.function.name,
                    arguments=call.function.arguments,
                    output=output[:500],
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

            result.steps.append(step)
            result.total_tokens += tokens
            if on_step:
                on_step(step)

            signature = tuple((c.function.name, c.function.arguments) for c in tool_calls)
            recent_calls = [*recent_calls, signature][-LOOP_REPEATS:]
            if signature and len(recent_calls) == LOOP_REPEATS and len(set(recent_calls)) == 1:
                tracer.log("loop_detected", step=number, calls=[name for name, _ in signature])
                messages.append({
                    "role": "user",
                    "content": f"You have made the same tool call {LOOP_REPEATS} times in a row "
                    "and got the same result. Repeating it will not help: try a different "
                    "approach" + (f", or call {role.submit_tool} if you are done." if role else "."),
                })
                recent_calls = []

            if result.output is not None:
                result.status = "completed"
                result.final_answer = message.content
                tracer.log("submitted", step=number, role=role.name, output=result.output)
                break

            if role and tool_calls and max_steps - number == STEPS_LEFT_WARNING:
                tracer.log("steps_left_warning", step=number)
                messages.append({
                    "role": "user",
                    "content": f"You have {STEPS_LEFT_WARNING} steps left. Call "
                    f"{role.submit_tool} now with everything you have so far; a submission "
                    "that covers part of the work is far more useful than none.",
                })

            if not tool_calls:
                if role and nudges < MAX_SUBMIT_NUDGES:
                    nudges += 1
                    messages.append({
                        "role": "user",
                        "content": f"You have not submitted your result yet. "
                        f"Call {role.submit_tool} now.",
                    })
                    continue
                result.status = "completed"
                result.final_answer = message.content
                break
    except DailyLimitReached as e:
        result.status = "error"
        result.error = f"DailyLimitReached: {e}"
    except OpenAIError as e:
        result.status = "error"
        result.error = f"{type(e).__name__}: {e}"

    result.duration_s = round(time.perf_counter() - started, 2)
    tracer.log(
        "run_end",
        status=result.status,
        steps=len(result.steps),
        total_tokens=result.total_tokens,
        duration_s=result.duration_s,
        error=result.error,
    )
    return result

"""The core agent loop."""
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError, RateLimitError

from orchestrator.state import RunResult, Step, ToolCall
from orchestrator.tools import TOOL_SCHEMAS, execute_tool
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

MAX_RATE_LIMIT_WAITS = 5  # retries per model call for short (per-minute) limits
MAX_WAIT_S = 90  # never sleep longer than this for one retry


class DailyLimitReached(Exception):
    """The provider's per-day quota is used up. Retrying today will not help."""


def is_daily_limit(error: Exception) -> bool:
    text = str(error).lower()
    return "per day" in text or "(tpd)" in text or "(rpd)" in text


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
    """Call the model, waiting and retrying on short rate limits.

    Raises DailyLimitReached when the daily quota is exhausted, and re-raises the last
    RateLimitError if waiting did not help.
    """
    for attempt in range(MAX_RATE_LIMIT_WAITS + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            if is_daily_limit(e):
                raise DailyLimitReached(str(e)) from e
            if attempt == MAX_RATE_LIMIT_WAITS:
                raise
            wait = retry_after_seconds(e, attempt)
            tracer.log("rate_limited", attempt=attempt + 1, wait_s=wait)
            sleep(wait)


def make_client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")
    return OpenAI(api_key=api_key, base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL))


def default_model() -> str:
    load_dotenv()
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def run_agent(
    task: str,
    root: Path | None = None,
    max_steps: int = 10,
    model: str | None = None,
    client: OpenAI | None = None,
    on_step: Callable[[Step], None] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    sleep: Callable[[float], None] = time.sleep,
) -> RunResult:
    """Run one agent on a task and return everything that happened."""
    root = (root or Path.cwd()).resolve()
    client = client or make_client()
    model = model or default_model()

    run_id = new_run_id()
    tracer = Tracer(run_id)
    result = RunResult(run_id=run_id, task=task, status="max_steps")
    started = time.perf_counter()
    tracer.log("run_start", task=task, model=model, root=str(root), max_steps=max_steps)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    try:
        for number in range(1, max_steps + 1):
            t0 = time.perf_counter()
            response = call_model(
                client, tracer, sleep, model=model, messages=messages, tools=TOOL_SCHEMAS
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
                text=message.content,
                tools_requested=[c.function.name for c in tool_calls],
            )

            for call in tool_calls:
                output = execute_tool(call.function.name, call.function.arguments, root)
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

            if not tool_calls:
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
"""Checks the API setup. The API key itself is never printed.

    python -m orchestrator.doctor                      check the configured model
    python -m orchestrator.doctor --models             also list every model the key can use
    python -m orchestrator.doctor --try MODEL [MODEL ...]
                                                       test several models with a real tool call

The tool-call test matters: the agent only works if the model can call tools, so a model that
answers plain questions but cannot call tools is reported as unusable.
"""
import os
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

from orchestrator.agent import DEFAULT_BASE_URL, default_model, make_client, max_context_chars

PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the text contents of one file in the project.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to the file"}},
            "required": ["path"],
        },
    },
}
PROBE_MESSAGES = [{"role": "user", "content": "Use the read_file tool to read the file calculator.py."}]


def probe(client: OpenAI, model: str) -> tuple[bool, str]:
    """Ask the model to make one tool call. Returns (usable, explanation)."""
    started = time.perf_counter()
    try:
        reply = client.chat.completions.create(model=model, messages=PROBE_MESSAGES, tools=[PROBE_TOOL])
    except OpenAIError as e:
        return False, f"{type(e).__name__}: {str(e)[:160]}"
    seconds = time.perf_counter() - started
    calls = reply.choices[0].message.tool_calls or []
    if not calls:
        return False, f"answered without calling the tool ({seconds:.1f}s)"
    call = calls[0].function
    if call.name != "read_file" or "calculator.py" not in (call.arguments or ""):
        return False, f"made a wrong tool call: {call.name}({call.arguments}) ({seconds:.1f}s)"
    return True, f"tool call OK ({seconds:.1f}s)"


def main() -> None:
    load_dotenv()
    args = sys.argv[1:]
    key_source = "LLM_API_KEY" if os.getenv("LLM_API_KEY") else "GROQ_API_KEY"
    model = default_model()
    print(f"provider:      {os.getenv('LLM_BASE_URL', DEFAULT_BASE_URL)}")
    print(f"key from:      {key_source}")
    print(f"model:         {model}")
    print(f"context limit: {max_context_chars()} characters per request")

    try:
        client = make_client()
        models = sorted(m.id.removeprefix("models/") for m in client.models.list())
    except (OpenAIError, RuntimeError) as e:
        print(f"\nCould not reach the provider: {type(e).__name__}: {str(e)[:300]}")
        sys.exit(1)

    if "--try" in args:
        candidates = args[args.index("--try") + 1:]
        print(f"\nTesting {len(candidates)} model(s) with a real tool call:")
        for name in candidates:
            if name not in models:
                print(f"    {name:<28} NOT AVAILABLE to this key")
                continue
            usable, detail = probe(client, name)
            print(f"    {name:<28} {'USABLE' if usable else 'NOT USABLE':<11} {detail}")
        return

    available = model in models
    print(f"\n{len(models)} models available to this key. Configured model: "
          f"{'FOUND' if available else 'NOT FOUND'}")
    if not available or "--models" in args:
        print("Models you can use for LLM_MODEL in .env:")
        for m in models:
            print(f"    {m}")
    if not available:
        sys.exit(1)

    usable, detail = probe(client, model)
    print(f"Tool-call test: {'OK' if usable else 'FAILED'} - {detail}")
    if not usable:
        sys.exit(1)


if __name__ == "__main__":
    main()

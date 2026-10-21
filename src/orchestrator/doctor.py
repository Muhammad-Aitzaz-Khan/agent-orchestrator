"""Checks the API setup: python -m orchestrator.doctor   (add --models to list every model)

Prints which provider and model are configured, whether the model is available to your key,
and makes one tiny request. The API key itself is never printed.
"""
import os
import sys

from dotenv import load_dotenv
from openai import OpenAIError

from orchestrator.agent import DEFAULT_BASE_URL, default_model, make_client, max_context_chars


def main() -> None:
    load_dotenv()
    key_source = "LLM_API_KEY" if os.getenv("LLM_API_KEY") else "GROQ_API_KEY"
    model = default_model()
    print(f"provider:      {os.getenv('LLM_BASE_URL', DEFAULT_BASE_URL)}")
    print(f"key from:      {key_source}")
    print(f"model:         {model}")
    print(f"context limit: {max_context_chars()} characters per request")

    try:
        client = make_client()
        models = sorted(m.id for m in client.models.list())
    except (OpenAIError, RuntimeError) as e:
        sys.exit(f"\nCould not reach the provider: {type(e).__name__}: {str(e)[:300]}")

    available = model in models or f"models/{model}" in models
    print(f"\n{len(models)} models available to this key. Configured model: "
          f"{'FOUND' if available else 'NOT FOUND'}")
    if not available or "--models" in sys.argv:
        print("Models you can use for LLM_MODEL in .env:")
        for m in models:
            print(f"    {m.removeprefix('models/')}")
    if not available:
        sys.exit(1)

    try:
        reply = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Reply with the word ok."}]
        )
        print(f"Test request: OK ({(reply.choices[0].message.content or '').strip()[:40]!r})")
    except OpenAIError as e:
        sys.exit(f"Test request failed: {type(e).__name__}: {str(e)[:300]}")


if __name__ == "__main__":
    main()

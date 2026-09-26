"""A minimal AI agent that explores this project's files, running on Groq's free tier."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv()  # reads GROQ_API_KEY from the .env file

client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)
MODEL = "openai/gpt-oss-safeguard-20b"
ROOT = Path.cwd().resolve()
SKIP = {".venv", ".git", "__pycache__"}

SYSTEM_PROMPT = (
    "You are a helpful agent that answers questions about a software project. "
    "Use the tools to look at real files before answering. Never guess file contents."
)

# 1. Tool schemas: what the model is told it can do
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List every file in the project folder. Use this first to see what exists.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the text contents of one file in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the project root, e.g. 'pyproject.toml'",
                    }
                },
                "required": ["path"],
            },
        },
    },
]


# 2. Tool functions: the code that actually runs
def list_files() -> str:
    files = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.is_file() and not SKIP.intersection(rel.parts) and p.name != ".env":
            files.append(rel.as_posix())
    return "\n".join(sorted(files)) or "(no files)"


def read_file(path: str) -> str:
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT):  # safety: stay inside the project
        return "Error: path is outside the project."
    if target.name == ".env":  # safety: never show secrets to the model
        return "Error: access to .env is not allowed."
    if not target.is_file():
        return f"Error: {path} does not exist."
    text = target.read_text(encoding="utf-8", errors="replace")
    return text[:5000] or "(file is empty)"


def execute_tool(name: str, raw_args: str) -> str:
    try:
        args = json.loads(raw_args or "{}")
        if name == "list_files":
            return list_files()
        if name == "read_file":
            return read_file(**args)
        return f"Error: unknown tool {name}"
    except Exception as e:  # report errors to the model instead of crashing
        return f"Error: {e}"


# 3. The agent loop
def run_agent(task: str, max_steps: int = 10) -> None:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    for step in range(1, max_steps + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS
            )
        except RateLimitError:
            print("Rate limit hit. Wait a minute and try again.")
            return

        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))

        if message.content:
            print(f"\n[step {step}] Agent: {message.content}")

        if not message.tool_calls:
            return  # no tool requested, so this is the final answer

        for call in message.tool_calls:
            print(f"[step {step}] Tool call: {call.function.name}({call.function.arguments})")
            output = execute_tool(call.function.name, call.function.arguments)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )

    print("\nStopped: reached max_steps.")


if __name__ == "__main__":
    run_agent(input("Task: "))
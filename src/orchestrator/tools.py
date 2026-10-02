"""Tools the agent can use, and the code that runs them."""
import json
import os
from pathlib import Path

SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "runs"}
BLOCKED_FILES = {".env"}
MAX_OUTPUT_CHARS = 5000

TOOL_SCHEMAS = [
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


def _is_hidden(rel: Path) -> bool:
    """True for paths the agent must never see."""
    if rel.name in BLOCKED_FILES:
        return True
    return any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts)


def list_files(root: Path) -> str:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune skipped folders so os.walk never enters them
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            if name not in BLOCKED_FILES:
                files.append((Path(dirpath) / name).relative_to(root).as_posix())
    return "\n".join(sorted(files)) or "(no files)"


def read_file(root: Path, path: str) -> str:
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return "Error: path is outside the project."
    if _is_hidden(target.relative_to(root)):
        return f"Error: access to {path} is not allowed."
    if not target.is_file():
        return f"Error: {path} does not exist."
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
    return text or "(file is empty)"


def execute_tool(name: str, raw_args: str | None, root: Path) -> str:
    """Run one tool call from the model. Always returns a string, never raises."""
    root = root.resolve()
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        return "Error: tool arguments were not valid JSON."
    if not isinstance(args, dict):
        return "Error: tool arguments must be a JSON object."

    try:
        if name == "list_files":
            return list_files(root)  # takes no arguments; extra ones are ignored
        if name == "read_file":
            if "path" not in args:
                return "Error: read_file needs a 'path' argument."
            return read_file(root, str(args["path"]))
        return f"Error: unknown tool '{name}'."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"
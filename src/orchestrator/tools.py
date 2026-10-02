"""Tools the agent can use, and the code that runs them."""
import json
import os
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "runs"}
BLOCKED_FILES = {".env"}
PROTECTED_FILES = {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

MAX_READ_CHARS = 5000
MAX_WRITE_CHARS = 100_000
MAX_TEST_OUTPUT_CHARS = 4000
TEST_TIMEOUT_S = 30


class ToolError(Exception):
    """A mistake the model made and can fix. Its message is sent back to the model."""


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
                        "description": "Path relative to the project root, e.g. 'calculator.py'",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Overwrite a file with new content. Always send the COMPLETE new file, "
                "not just the changed lines. Read the file first. Test files cannot be modified."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root"},
                    "content": {"type": "string", "description": "The complete new file contents"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the project's test suite with pytest and return the results.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ---------- safety helpers ----------

def _is_hidden(rel: Path) -> bool:
    """Paths the agent must never see."""
    if rel.name in BLOCKED_FILES:
        return True
    return any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts)


def _is_protected(rel: Path) -> bool:
    """Files the agent may read but never modify: tests and config."""
    name = rel.name
    return (
        name in PROTECTED_FILES
        or name.startswith("test_")
        or name.endswith("_test.py")
        or "tests" in rel.parts[:-1]
    )


def _safe_path(root: Path, path: str) -> Path:
    """Turn a model-supplied path into a real path, refusing anything unsafe."""
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ToolError("path is outside the project.")
    if target == root:
        raise ToolError("path must point to a file, not the project folder.")
    if _is_hidden(target.relative_to(root)):
        raise ToolError(f"access to {path} is not allowed.")
    return target


def _clean_env() -> dict[str, str]:
    """Environment for subprocesses, with secrets such as API keys removed."""
    return {
        k: v for k, v in os.environ.items()
        if not any(word in k.upper() for word in SECRET_ENV_WORDS)
    }


# ---------- the tools ----------

def list_files(root: Path) -> str:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            if name not in BLOCKED_FILES:
                files.append((Path(dirpath) / name).relative_to(root).as_posix())
    return "\n".join(sorted(files)) or "(no files)"


def read_file(root: Path, path: str) -> str:
    target = _safe_path(root, path)
    if not target.is_file():
        raise ToolError(f"{path} does not exist.")
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + "\n... (truncated)"
    return text or "(file is empty)"


def write_file(root: Path, path: str, content: str) -> str:
    target = _safe_path(root, path)
    if _is_protected(target.relative_to(root)):
        raise ToolError(f"{path} is protected. Fix the source code, not the tests or config.")
    if len(content) > MAX_WRITE_CHARS:
        raise ToolError(f"content is too large ({len(content)} characters).")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {path}."


def run_tests(root: Path) -> str:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."]
    try:
        proc = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEST_TIMEOUT_S,
            env=_clean_env(),
        )
    except subprocess.TimeoutExpired:
        raise ToolError(
            f"tests ran longer than {TEST_TIMEOUT_S}s and were stopped (possible infinite loop)."
        )

    output = (proc.stdout + proc.stderr).strip()
    if len(output) > MAX_TEST_OUTPUT_CHARS:
        output = "... (truncated)\n" + output[-MAX_TEST_OUTPUT_CHARS:]  # the summary is at the end

    if proc.returncode == 0:
        summary = "Tests PASSED"
    elif proc.returncode == 5:
        summary = "No tests were found"
    else:
        summary = f"Tests FAILED (exit code {proc.returncode})"
    return f"{summary}\n{output}"


# ---------- dispatch ----------

HANDLERS = {
    "list_files": lambda root, args: list_files(root),
    "read_file": lambda root, args: read_file(root, str(args["path"])),
    "write_file": lambda root, args: write_file(root, str(args["path"]), str(args["content"])),
    "run_tests": lambda root, args: run_tests(root),
}

REQUIRED_ARGS = {
    schema["function"]["name"]: schema["function"]["parameters"]["required"]
    for schema in TOOL_SCHEMAS
}


def execute_tool(name: str, raw_args: str | None, root: Path) -> str:
    """Run one tool call from the model. Always returns a string, never raises."""
    root = root.resolve()
    if name not in HANDLERS:
        return f"Error: unknown tool '{name}'."

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        return "Error: tool arguments were not valid JSON."
    if not isinstance(args, dict):
        return "Error: tool arguments must be a JSON object."

    missing = [key for key in REQUIRED_ARGS[name] if key not in args]
    if missing:
        return f"Error: {name} is missing required argument(s): {', '.join(missing)}."

    try:
        return HANDLERS[name](root, args)
    except ToolError as e:
        return f"Error: {e}"
    except Exception as e:  # a bug in our own code; still don't crash the run
        return f"Error: {type(e).__name__}: {e}"
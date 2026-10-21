"""Tools the agent can use, and the code that runs them."""
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "runs"}
BLOCKED_FILES = {".env"}
PROTECTED_FILES = {"conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}
SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD")

MAX_READ_CHARS = 5000
MAX_WRITE_CHARS = 100_000
MAX_TEST_OUTPUT_CHARS = 4000
TEST_TIMEOUT_S = 30
PYTHON_TIMEOUT_S = 20
MAX_PYTHON_CODE_CHARS = 10_000
MAX_DIFF_CHARS = 6000


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
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Run a short Python script inside a throwaway copy of the project and return its "
                "output. Use it to check behaviour, e.g. import a module and print the result of "
                "a call with edge-case inputs. Changes made by the script are discarded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source code to run"}
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_diff",
            "description": "Show every change made to the project so far, as a unified diff.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

TOOLS_BY_NAME = {schema["function"]["name"]: schema for schema in TOOL_SCHEMAS}

# The tools the single agent has always had. Newer tools are opt-in per role.
DEFAULT_TOOLS = ["list_files", "read_file", "write_file", "run_tests"]


@dataclass
class ToolContext:
    """Where a tool runs and what it may do.

    root:     the project folder.
    allowed:  tool names this agent may call (None means DEFAULT_TOOLS).
    baseline: an untouched copy of the project, used by show_diff.
    """

    root: Path
    allowed: frozenset[str] | None = None
    baseline: Path | None = None


def schemas_for(names: list[str]) -> list[dict]:
    """Tool schemas for the given names, in order. Unknown names raise KeyError."""
    return [TOOLS_BY_NAME[name] for name in names]


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
    """Environment for subprocesses, with secrets such as API keys removed.

    Bytecode caching is turned off: Python decides whether a cached .pyc is stale from the
    source file's timestamp and size, so a same-size edit (e.g. `x + 2` -> `x * 2`) followed
    by a test run within the same second could otherwise run the OLD code.
    """
    env = {
        k: v for k, v in os.environ.items()
        if not any(word in k.upper() for word in SECRET_ENV_WORDS)
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


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
        proc = subprocess.run(  # non-zero exit codes are expected and handled below
            command,
            check=False,
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


def copy_project(root: Path, dest: Path) -> Path:
    """Copy the visible project (no secrets, caches, or virtualenvs) to dest."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        return {
            n for n in names
            if n in SKIP_DIRS or n in BLOCKED_FILES or n.endswith(".egg-info")
        }

    shutil.copytree(root, dest, ignore=ignore, dirs_exist_ok=True)
    return dest


def run_python(root: Path, code: str) -> str:
    """Run code in a throwaway copy of the project, so it cannot change the real files."""
    if len(code) > MAX_PYTHON_CODE_CHARS:
        raise ToolError(f"code is too long ({len(code)} characters).")
    with tempfile.TemporaryDirectory(prefix="run_python_", ignore_cleanup_errors=True) as tmp:
        sandbox = copy_project(root, Path(tmp) / "project")
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                check=False,
                cwd=sandbox,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PYTHON_TIMEOUT_S,
                env={**_clean_env(), "PYTHONPATH": str(sandbox)},
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"the script ran longer than {PYTHON_TIMEOUT_S}s and was stopped.")
    output = (proc.stdout + proc.stderr).strip()
    if len(output) > MAX_TEST_OUTPUT_CHARS:
        output = output[:MAX_TEST_OUTPUT_CHARS] + "\n... (truncated)"
    return f"exit code {proc.returncode}\n{output or '(no output)'}"


def _visible_text_files(root: Path) -> dict[str, list[str]]:
    files = {}
    for rel in list_files(root).splitlines():
        if rel == "(no files)":
            continue
        try:
            files[rel] = (root / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        except (UnicodeDecodeError, OSError):
            continue
    return files


def show_diff(root: Path, baseline: Path | None) -> str:
    if baseline is None:
        raise ToolError("no baseline is available, so changes cannot be shown.")
    before, after = _visible_text_files(baseline), _visible_text_files(root)
    chunks = []
    for rel in sorted(before.keys() | after.keys()):
        old, new = before.get(rel, []), after.get(rel, [])
        if old != new:
            chunks.extend(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}"))
    diff = "".join(chunks)
    if not diff:
        return "(no changes)"
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... (diff truncated)"
    return diff


# ---------- dispatch ----------

HANDLERS = {
    "list_files": lambda ctx, args: list_files(ctx.root),
    "read_file": lambda ctx, args: read_file(ctx.root, str(args["path"])),
    "write_file": lambda ctx, args: write_file(ctx.root, str(args["path"]), str(args["content"])),
    "run_tests": lambda ctx, args: run_tests(ctx.root),
    "run_python": lambda ctx, args: run_python(ctx.root, str(args["code"])),
    "show_diff": lambda ctx, args: show_diff(ctx.root, ctx.baseline),
}

REQUIRED_ARGS = {
    schema["function"]["name"]: schema["function"]["parameters"]["required"]
    for schema in TOOL_SCHEMAS
}


def execute_tool(
    name: str, raw_args: str | None, root: Path, context: ToolContext | None = None
) -> str:
    """Run one tool call from the model. Always returns a string, never raises.

    The context decides which tools this agent may use. The model only sees the schemas of
    its allowed tools, but it can still name any tool, so the check is enforced here too.
    """
    context = context or ToolContext(root=root)
    context.root = context.root.resolve()
    allowed = context.allowed if context.allowed is not None else frozenset(DEFAULT_TOOLS)
    if name not in HANDLERS:
        return f"Error: unknown tool '{name}'."
    if name not in allowed:
        return f"Error: the {name} tool is not available to this agent."

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
        return HANDLERS[name](context, args)
    except ToolError as e:
        return f"Error: {e}"
    except Exception as e:  # noqa: BLE001 - a bug in our own code must not crash the run
        return f"Error: {type(e).__name__}: {e}"
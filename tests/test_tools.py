"""Unit tests for the agent's tools. These never call the LLM API."""
import pytest

from orchestrator import tools
from orchestrator.tools import execute_tool


@pytest.fixture
def project(tmp_path):
    """A small fake project in a temporary folder."""
    (tmp_path / "main.py").write_text("print('hi')\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.py").write_text("x = 1\n")
    (tmp_path / "empty.txt").write_text("")
    (tmp_path / ".env").write_text("SECRET=123\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("")
    return tmp_path


def make_repo(tmp_path, source: str, tests: str):
    """A tiny project with one module and one test file."""
    (tmp_path / "mathlib.py").write_text(source)
    (tmp_path / "test_mathlib.py").write_text(tests)
    return tmp_path


DOUBLE_TEST = "from mathlib import double\n\ndef test_double():\n    assert double(3) == 6\n"


# ---------- list_files ----------

def test_list_files_shows_project_files(project):
    out = execute_tool("list_files", "{}", project)
    assert "main.py" in out
    assert "src/util.py" in out


def test_list_files_hides_env_and_venv(project):
    out = execute_tool("list_files", "{}", project)
    assert ".env" not in out
    assert ".venv" not in out


def test_list_files_ignores_unexpected_arguments(project):
    assert "main.py" in execute_tool("list_files", '{"path": ""}', project)


# ---------- read_file ----------

def test_read_file_returns_contents(project):
    assert "print('hi')" in execute_tool("read_file", '{"path": "main.py"}', project)


def test_read_file_blocks_env(project):
    out = execute_tool("read_file", '{"path": ".env"}', project)
    assert "not allowed" in out
    assert "SECRET" not in out


def test_read_file_blocks_paths_outside_project(project):
    assert "outside the project" in execute_tool("read_file", '{"path": "../x.txt"}', project)


def test_read_file_missing_file(project):
    assert "does not exist" in execute_tool("read_file", '{"path": "nope.py"}', project)


def test_read_file_empty_file(project):
    assert execute_tool("read_file", '{"path": "empty.txt"}', project) == "(file is empty)"


# ---------- write_file ----------

def test_write_file_creates_file(project):
    out = execute_tool("write_file", '{"path": "new.py", "content": "y = 2\\n"}', project)
    assert "Wrote" in out
    assert (project / "new.py").read_text() == "y = 2\n"


def test_write_file_creates_parent_folders(project):
    execute_tool("write_file", '{"path": "pkg/sub/mod.py", "content": "z = 3"}', project)
    assert (project / "pkg" / "sub" / "mod.py").exists()


def test_write_file_refuses_test_files(project):
    out = execute_tool("write_file", '{"path": "test_main.py", "content": "x"}', project)
    assert "protected" in out
    assert not (project / "test_main.py").exists()


def test_write_file_refuses_env(project):
    out = execute_tool("write_file", '{"path": ".env", "content": "HACKED"}', project)
    assert "not allowed" in out
    assert "SECRET" in (project / ".env").read_text()


def test_write_file_refuses_paths_outside_project(project):
    out = execute_tool("write_file", '{"path": "../evil.py", "content": "x"}', project)
    assert "outside the project" in out


# ---------- run_tests ----------

def test_run_tests_passing(tmp_path):
    repo = make_repo(tmp_path, "def double(x):\n    return x * 2\n", DOUBLE_TEST)
    assert execute_tool("run_tests", "{}", repo).startswith("Tests PASSED")


def test_run_tests_failing(tmp_path):
    repo = make_repo(tmp_path, "def double(x):\n    return x + 2\n", DOUBLE_TEST)
    assert execute_tool("run_tests", "{}", repo).startswith("Tests FAILED")


def test_run_tests_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "TEST_TIMEOUT_S", 2)
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    repo = make_repo(tmp_path, "", slow)
    assert "stopped" in execute_tool("run_tests", "{}", repo)


def test_run_tests_hides_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake-for-test")
    leak_check = "import os\n\ndef test_no_key():\n    assert 'GROQ_API_KEY' not in os.environ\n"
    repo = make_repo(tmp_path, "", leak_check)
    assert execute_tool("run_tests", "{}", repo).startswith("Tests PASSED")


# ---------- argument handling ----------

def test_missing_argument(project):
    assert "missing required argument" in execute_tool("read_file", "{}", project)


def test_invalid_json_arguments(project):
    assert "not valid JSON" in execute_tool("read_file", "{not json", project)


def test_unknown_tool(project):
    assert "unknown tool" in execute_tool("delete_everything", "{}", project)

def test_same_size_edit_is_picked_up_immediately(tmp_path):
    """Regression test: a stale .pyc must never make run_tests check old code."""
    repo = make_repo(tmp_path, "def double(x):\n    return x + 2\n", DOUBLE_TEST)
    for _ in range(5):  # fail, fix with a same-size edit, re-test; repeat quickly
        (repo / "mathlib.py").write_text("def double(x):\n    return x + 2\n")
        assert execute_tool("run_tests", "{}", repo).startswith("Tests FAILED")
        (repo / "mathlib.py").write_text("def double(x):\n    return x * 2\n")
        assert execute_tool("run_tests", "{}", repo).startswith("Tests PASSED")


def test_run_python_cannot_change_the_project(tmp_path):
    (tmp_path / "data.txt").write_text("original")
    ctx = tools.ToolContext(root=tmp_path, allowed=frozenset({"run_python"}))
    code = '{"code": "open(\'data.txt\', \'w\').write(\'changed\'); print(open(\'data.txt\').read())"}'
    out = execute_tool("run_python", code, tmp_path, ctx)
    assert out == "exit code 0\nchanged"  # the script saw its own change...
    assert (tmp_path / "data.txt").read_text() == "original"  # ...but only in the copy


def test_run_python_cannot_see_env_file(tmp_path):
    (tmp_path / ".env").write_text("GROQ_API_KEY=secret")
    ctx = tools.ToolContext(root=tmp_path, allowed=frozenset({"run_python"}))
    out = execute_tool("run_python", '{"code": "import os; print(os.path.exists(\'.env\'))"}',
                       tmp_path, ctx)
    assert out.endswith("False")


def test_show_diff_against_baseline(tmp_path):
    baseline, root = tmp_path / "before", tmp_path / "after"
    for folder in (baseline, root):
        folder.mkdir()
        (folder / "a.py").write_text("x = 1\n")
    (root / "a.py").write_text("x = 2\n")
    ctx = tools.ToolContext(root=root, allowed=frozenset({"show_diff"}), baseline=baseline)
    out = execute_tool("show_diff", "{}", root, ctx)
    assert "-x = 1" in out and "+x = 2" in out


def test_tools_outside_the_allowed_set_are_refused(project):
    ctx = tools.ToolContext(root=project, allowed=frozenset({"read_file"}))
    out = execute_tool("write_file", '{"path": "new.py", "content": "x"}', project, ctx)
    assert "not available to this agent" in out
    assert not (project / "new.py").exists()

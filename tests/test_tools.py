"""Unit tests for the agent's tools. These never call the LLM API."""
import pytest

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


def test_list_files_shows_project_files(project):
    out = execute_tool("list_files", "{}", project)
    assert "main.py" in out
    assert "src/util.py" in out


def test_list_files_hides_env_and_venv(project):
    out = execute_tool("list_files", "{}", project)
    assert ".env" not in out
    assert ".venv" not in out


def test_list_files_ignores_unexpected_arguments(project):
    out = execute_tool("list_files", '{"path": ""}', project)
    assert "main.py" in out


def test_read_file_returns_contents(project):
    assert "print('hi')" in execute_tool("read_file", '{"path": "main.py"}', project)


def test_read_file_blocks_env(project):
    out = execute_tool("read_file", '{"path": ".env"}', project)
    assert "not allowed" in out
    assert "SECRET" not in out


def test_read_file_blocks_paths_outside_project(project):
    out = execute_tool("read_file", '{"path": "../outside.txt"}', project)
    assert "outside the project" in out


def test_read_file_missing_file(project):
    assert "does not exist" in execute_tool("read_file", '{"path": "nope.py"}', project)


def test_read_file_empty_file(project):
    assert execute_tool("read_file", '{"path": "empty.txt"}', project) == "(file is empty)"


def test_read_file_missing_argument(project):
    assert "needs a 'path'" in execute_tool("read_file", "{}", project)


def test_invalid_json_arguments(project):
    assert "not valid JSON" in execute_tool("read_file", "{not json", project)


def test_unknown_tool(project):
    assert "unknown tool" in execute_tool("delete_everything", "{}", project)
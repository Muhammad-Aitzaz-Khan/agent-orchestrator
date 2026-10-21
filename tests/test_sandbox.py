"""Tests for the Docker sandbox. Skipped automatically when Docker or the image is missing."""
import shutil
import subprocess

import pytest

from orchestrator import tools
from orchestrator.tools import DEFAULT_SANDBOX_IMAGE, ToolContext, execute_tool


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "image", "inspect", DEFAULT_SANDBOX_IMAGE],
                           capture_output=True, check=False)
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not docker_ready(), reason="Docker sandbox image not available")


@pytest.fixture(autouse=True)
def docker_mode(monkeypatch):
    monkeypatch.setenv("SANDBOX", "docker")


def make_repo(tmp_path, source):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "mathlib.py").write_text(source)
    (tmp_path / "test_mathlib.py").write_text(
        "from mathlib import double\n\ndef test_double():\n    assert double(3) == 6\n")
    tmp_path.chmod(0o777)
    return tmp_path


def python_ctx(root):
    return ToolContext(root=root, allowed=frozenset({"run_python"}))


def test_tests_pass_and_fail_inside_the_container(tmp_path):
    repo = make_repo(tmp_path, "def double(x):\n    return x * 2\n")
    assert execute_tool("run_tests", "{}", repo).startswith("Tests PASSED")
    (repo / "mathlib.py").write_text("def double(x):\n    return x + 2\n")
    assert execute_tool("run_tests", "{}", repo).startswith("Tests FAILED")


def test_run_python_can_import_project_code(tmp_path):
    repo = make_repo(tmp_path, "def double(x):\n    return x * 2\n")
    out = execute_tool("run_python", '{"code": "from mathlib import double; print(double(21))"}',
                       repo, python_ctx(repo))
    assert out == "exit code 0\n42"


def test_container_has_no_network(tmp_path):
    code = ("import socket\\ntry:\\n    socket.create_connection(('1.1.1.1', 80), timeout=3)"
            "\\n    print('open')\\nexcept OSError:\\n    print('blocked')")
    out = execute_tool("run_python", f'{{"code": "{code}"}}', tmp_path, python_ctx(tmp_path))
    assert out.endswith("blocked")


def test_container_cannot_write_outside_the_project(tmp_path):
    code = ("try:\\n    open('/etc/evil', 'w')\\n    print('wrote')\\nexcept OSError:"
            "\\n    print('denied')")
    out = execute_tool("run_python", f'{{"code": "{code}"}}', tmp_path, python_ctx(tmp_path))
    assert out.endswith("denied")


def test_container_cannot_see_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-should-not-leak")
    out = execute_tool("run_python",
                       '{"code": "import os; print(os.environ.get(\\"GROQ_API_KEY\\"))"}',
                       tmp_path, python_ctx(tmp_path))
    assert out.endswith("None")


def test_infinite_loop_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "PYTHON_TIMEOUT_S", 3)
    out = execute_tool("run_python", '{"code": "while True: pass"}', tmp_path, python_ctx(tmp_path))
    assert "stopped" in out
    running = subprocess.run(["docker", "ps", "-q", "--filter", "name=orch-sandbox-"],
                             capture_output=True, text=True, check=False).stdout.strip()
    assert running == ""  # the container was killed, not left running

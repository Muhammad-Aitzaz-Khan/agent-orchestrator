"""Tests for SANDBOX=guarded, the sandbox for servers without Docker (e.g. the public demo)."""
import json
import sys

import pytest

from orchestrator.tools import ToolContext, execute_tool, run_tests

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the guard is used on Linux servers")


@pytest.fixture(autouse=True)
def guarded(monkeypatch):
    monkeypatch.setenv("SANDBOX", "guarded")
    monkeypatch.setenv("GROQ_API_KEY", "must-not-leak")


def run_python(root, code):
    return execute_tool("run_python", json.dumps({"code": code}), root,
                        ToolContext(root, allowed={"run_python"}))


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "mathlib.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "test_mathlib.py").write_text(
        "from mathlib import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    return tmp_path


def test_ordinary_code_and_tests_still_work(repo):
    assert run_tests(repo).startswith("Tests PASSED")
    out = run_python(repo, "from mathlib import add\nopen('x.txt', 'w').write('ok')\nprint(add(2, 3))")
    assert out.startswith("exit code 0") and "5" in out


@pytest.mark.parametrize("code, blocked", [
    ("import socket; socket.create_connection(('example.com', 80))", "socket"),
    ("import urllib.request; urllib.request.urlopen('http://example.com')", "urllib"),
    ("import subprocess; subprocess.run(['echo', 'hi'])", "subprocess.Popen"),
    ("import os; os.system('echo hi')", "os.system"),
    ("print(open('/proc/self/environ').read())", "reading /proc"),
    ("open('/etc/escaped.txt', 'w').write('x')", "writing /etc/escaped.txt"),
    ("import ctypes", "import ctypes"),
])
def test_dangerous_operations_are_blocked(repo, code, blocked):
    out = run_python(repo, code)
    assert "Blocked by the sandbox" in out and blocked in out


def test_the_key_is_not_in_the_environment(repo):
    out = run_python(repo, "import os; print([k for k in os.environ if 'KEY' in k])")
    assert "[]" in out and "must-not-leak" not in out

"""Tests for the model probe used by `python -m orchestrator.doctor --try`."""
from test_agent import FakeClient, completion, server_error

from orchestrator.doctor import probe


def test_good_tool_call_is_usable():
    client = FakeClient([completion(tool_calls=[("read_file", {"path": "calculator.py"})])])
    usable, detail = probe(client, "m")
    assert usable and "OK" in detail


def test_plain_answer_is_not_usable():
    usable, detail = probe(FakeClient([completion(content="Sure!")]), "m")
    assert not usable and "without calling the tool" in detail


def test_wrong_tool_call_is_not_usable():
    client = FakeClient([completion(tool_calls=[("read_file", {"path": "other.py"})])])
    assert not probe(client, "m")[0]


def test_overloaded_model_is_not_usable():
    usable, detail = probe(FakeClient([server_error()]), "m")
    assert not usable and "InternalServerError" in detail

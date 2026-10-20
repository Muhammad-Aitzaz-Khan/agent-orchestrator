import pytest

from graph import shortest_path, topological_sort


def test_alphabetical_tie_breaking():
    assert topological_sort({"d": ["b", "a"], "a": [], "b": []}) == ["d", "a", "b"]


def test_cycle_raises():
    with pytest.raises(ValueError):
        topological_sort({"a": ["b"], "b": ["c"], "c": ["a"]})


def test_empty_graph():
    assert topological_sort({}) == []


def test_unreachable():
    assert shortest_path({"a": ["b"], "c": []}, "a", "c") is None


def test_start_is_goal():
    assert shortest_path({"a": ["b"]}, "a", "a") == ["a"]

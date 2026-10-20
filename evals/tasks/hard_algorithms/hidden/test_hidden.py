from intervals import merge_intervals
from search import binary_search


def test_search_single_element():
    assert binary_search([5], 5) == 0


def test_search_empty():
    assert binary_search([], 3) == -1


def test_merge_touching():
    assert merge_intervals([[1, 2], [2, 3]]) == [[1, 3]]


def test_merge_empty():
    assert merge_intervals([]) == []


def test_merge_does_not_modify_input():
    data = [[6, 8], [1, 4], [2, 5]]
    merge_intervals(data)
    assert data == [[6, 8], [1, 4], [2, 5]]

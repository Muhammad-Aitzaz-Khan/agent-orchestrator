from intervals import merge_intervals
from search import binary_search


def test_search_middle():
    assert binary_search([1, 3, 5, 7, 9], 5) == 2


def test_search_last():
    assert binary_search([1, 3, 5, 7], 7) == 3


def test_search_missing():
    assert binary_search([1, 3, 5], 4) == -1


def test_merge_sorted():
    assert merge_intervals([[1, 3], [2, 6], [8, 10]]) == [[1, 6], [8, 10]]


def test_merge_unsorted():
    assert merge_intervals([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]

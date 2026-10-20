from dp import edit_distance, lcs_length, min_coins


def test_lcs_empty():
    assert lcs_length("", "abc") == 0
    assert lcs_length("abc", "") == 0


def test_edit_distance_from_empty():
    assert edit_distance("", "abc") == 3
    assert edit_distance("abc", "abc") == 0


def test_min_coins_impossible():
    assert min_coins([2], 3) == -1
    assert min_coins([], 4) == -1


def test_min_coins_zero():
    assert min_coins([3, 7], 0) == 0

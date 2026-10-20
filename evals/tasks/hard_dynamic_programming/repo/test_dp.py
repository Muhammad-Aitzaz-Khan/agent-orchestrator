from dp import edit_distance, lcs_length, min_coins


def test_lcs():
    assert lcs_length("abcde", "ace") == 3


def test_lcs_identical():
    assert lcs_length("dynamic", "dynamic") == 7


def test_edit_distance():
    assert edit_distance("kitten", "sitting") == 3


def test_edit_distance_insertions():
    assert edit_distance("ab", "xaxb") == 2


def test_min_coins():
    assert min_coins([1, 2, 5], 11) == 3

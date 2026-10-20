from words import top_words, word_counts


def test_empty_text():
    assert word_counts("") == {}
    assert top_words("", 3) == []


def test_n_larger_than_vocabulary():
    assert top_words("b a", 10) == [("a", 1), ("b", 1)]


def test_mixed_case_ties():
    assert top_words("Zebra apple zebra Apple mango", 3) == [("apple", 2), ("zebra", 2), ("mango", 1)]

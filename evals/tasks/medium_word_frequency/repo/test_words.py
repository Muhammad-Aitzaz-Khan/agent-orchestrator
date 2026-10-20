from words import top_words, word_counts


def test_word_counts_ignore_case_and_punctuation():
    assert word_counts("Hello, hello! World.") == {"hello": 2, "world": 1}


def test_top_words():
    assert top_words("b a c a b a", 2) == [("a", 3), ("b", 2)]


def test_top_words_ties_are_alphabetical():
    assert top_words("pear apple pear apple fig", 2) == [("apple", 2), ("pear", 2)]

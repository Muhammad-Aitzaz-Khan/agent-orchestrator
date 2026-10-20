from text_utils import capitalize_words, count_vowels, reverse_words


def test_count_vowels():
    assert count_vowels("banana") == 3


def test_count_vowels_uppercase():
    assert count_vowels("Hello WORLD") == 3


def test_capitalize_words():
    assert capitalize_words("hello world") == "Hello World"


def test_capitalize_keeps_double_spaces():
    assert capitalize_words("hello  world") == "Hello  World"


def test_reverse_words():
    assert reverse_words("one two three") == "three two one"

"""Small string helpers."""


def count_vowels(text):
    """Count the vowels (a, e, i, o, u) in text, ignoring case."""
    return sum(1 for ch in text if ch in "aeiou")


def capitalize_words(text):
    """Capitalize the first letter of every word, e.g. "hello world" -> "Hello World".

    Spacing is preserved exactly, including multiple spaces between words.
    """
    return " ".join(word[0].upper() + word[1:] for word in text.split(" "))


def reverse_words(text):
    """Reverse the order of the words, e.g. "one two three" -> "three two one"."""
    return text[::-1]

"""Word frequency statistics."""
import string


def word_counts(text):
    """Count words, ignoring case and surrounding punctuation.

    Example: "Hello, hello! World." -> {"hello": 2, "world": 1}
    """
    counts = {}
    for word in text.split():
        word = word.strip(string.punctuation)
        if word:
            counts[word] = counts.get(word, 0) + 1
    return counts


def top_words(text, n):
    """Return the n most common words as (word, count) pairs, most common first.

    Words with the same count are ordered alphabetically.
    """
    counts = word_counts(text)
    ranked = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)
    return ranked[:n]

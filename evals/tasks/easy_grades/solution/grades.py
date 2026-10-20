"""Grading helpers."""


def letter_grade(score):
    """Return the letter grade for a score from 0 to 100.

    90 and above: A, 80-89: B, 70-79: C, 60-69: D, below 60: F.
    Raises ValueError for scores outside 0-100.
    """
    if score < 0 or score > 100:
        raise ValueError("score must be between 0 and 100")
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def class_average(scores):
    """Return the average score rounded to 1 decimal place, or 0.0 for an empty list."""
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)

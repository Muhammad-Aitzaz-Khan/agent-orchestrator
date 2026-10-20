import pytest

from grades import class_average, letter_grade


def test_clear_grades():
    assert letter_grade(95) == "A"
    assert letter_grade(85) == "B"
    assert letter_grade(72) == "C"
    assert letter_grade(65) == "D"
    assert letter_grade(30) == "F"


def test_boundaries():
    assert letter_grade(90) == "A"
    assert letter_grade(80) == "B"
    assert letter_grade(70) == "C"
    assert letter_grade(60) == "D"
    assert letter_grade(59) == "F"


def test_invalid_score():
    with pytest.raises(ValueError):
        letter_grade(101)


def test_class_average():
    assert class_average([90, 80, 75]) == 81.7


def test_class_average_empty():
    assert class_average([]) == 0.0

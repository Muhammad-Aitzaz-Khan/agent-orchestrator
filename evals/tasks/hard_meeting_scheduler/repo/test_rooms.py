import pytest

from rooms import Room
from timeutils import to_hhmm, to_minutes


def test_time_conversion():
    assert to_minutes("09:30") == 570
    assert to_hhmm(570) == "09:30"


def test_invalid_time():
    with pytest.raises(ValueError):
        to_minutes("25:00")


def test_book():
    room = Room("A")
    assert room.book("09:00", "10:00", "standup")


def test_overlap_rejected():
    room = Room("A")
    room.book("09:00", "10:00", "standup")
    with pytest.raises(ValueError):
        room.book("09:30", "10:30", "review")


def test_end_before_start_rejected():
    with pytest.raises(ValueError):
        Room("A").book("11:00", "10:00", "oops")


def test_free_slots():
    room = Room("A")
    room.book("10:00", "11:00", "planning")
    assert room.free_slots() == [("08:00", "10:00"), ("11:00", "18:00")]

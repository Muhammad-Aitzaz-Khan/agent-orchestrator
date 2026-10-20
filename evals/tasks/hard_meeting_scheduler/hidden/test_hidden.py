from rooms import Room
from timeutils import overlaps


def test_touching_intervals_do_not_overlap():
    assert not overlaps(540, 600, 600, 660)
    assert overlaps(540, 601, 600, 660)


def test_back_to_back_booking():
    room = Room("A")
    room.book("09:00", "10:00", "first")
    assert room.book("10:00", "11:00", "second")


def test_free_slots_with_unordered_bookings():
    room = Room("A")
    room.book("14:00", "15:00", "late")
    room.book("09:00", "10:00", "early")
    assert room.free_slots() == [("08:00", "09:00"), ("10:00", "14:00"), ("15:00", "18:00")]


def test_free_slot_of_exactly_min_length_at_end_of_day():
    room = Room("A")
    room.book("08:00", "17:30", "workshop")
    assert room.free_slots(min_length=30) == [("17:30", "18:00")]

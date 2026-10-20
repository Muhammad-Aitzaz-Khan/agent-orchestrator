"""Meeting room bookings."""
from timeutils import overlaps, to_hhmm, to_minutes

DAY_START = "08:00"
DAY_END = "18:00"


class Room:
    def __init__(self, name):
        self.name = name
        self.bookings = []  # (start_minutes, end_minutes, title)

    def book(self, start, end, title):
        """Book the room from start to end ("HH:MM" strings).

        Raises ValueError if end is not after start, or if the booking overlaps an
        existing one. Back-to-back meetings are allowed. Returns True on success.
        """
        s, e = to_minutes(start), to_minutes(end)
        if e <= s:
            raise ValueError("end must be after start")
        for bs, be, existing in self.bookings:
            if overlaps(s, e, bs, be):
                raise ValueError(f"conflicts with {existing!r}")
        self.bookings.append((s, e, title))
        return True

    def free_slots(self, min_length=30):
        """Free periods between DAY_START and DAY_END as ("HH:MM", "HH:MM") pairs.

        Slots are in time order, and only gaps of at least min_length minutes are included.
        """
        slots = []
        cursor = to_minutes(DAY_START)
        for s, e, _ in self.bookings:
            if s - cursor >= min_length:
                slots.append((to_hhmm(cursor), to_hhmm(s)))
            cursor = max(cursor, e)
        day_end = to_minutes(DAY_END)
        if day_end - cursor > min_length:
            slots.append((to_hhmm(cursor), to_hhmm(day_end)))
        return slots

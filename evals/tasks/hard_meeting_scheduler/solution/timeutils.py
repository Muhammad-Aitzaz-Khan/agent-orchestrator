"""Time helpers. Times are minutes since midnight."""


def to_minutes(hhmm):
    """Convert "09:30" to 570. Raises ValueError for invalid times."""
    hours, minutes = hhmm.split(":")
    h, m = int(hours), int(minutes)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"invalid time {hhmm!r}")
    return h * 60 + m


def to_hhmm(minutes):
    """Convert 570 to "09:30"."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def overlaps(start1, end1, start2, end2):
    """True if the half-open intervals [start1, end1) and [start2, end2) overlap.

    Intervals that only touch (one ends exactly when the other starts) do NOT overlap.
    """
    return start1 < end2 and start2 < end1

"""Interval utilities."""


def merge_intervals(intervals):
    """Merge overlapping or touching intervals, e.g. [1, 2] and [2, 3] become [1, 3].

    Returns a new list sorted by start. The input list must not be modified.
    """
    merged = []
    for start, end in intervals:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged

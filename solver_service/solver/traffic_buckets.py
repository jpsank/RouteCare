"""Traffic bucket definitions shared conceptually with the Ruby side
(app/services/scheduling/traffic_buckets.rb) — keep boundaries in sync.

Each bucket approximates a coarse time-of-day driving-conditions regime.
`input.travel_matrix` is keyed by bucket name (see models.SolverInput):
{ bucket_name: { from_id: { to_id: minutes } } }. Travel-time lookups pick
the bucket whose [start_minute, end_minute) window covers the
estimated/actual time-of-day of a leg, falling back to OFF_PEAK when the
time isn't known (e.g. before an instance has been assigned a slot) or
doesn't fall in a rush/lunch window.
"""

from __future__ import annotations

MORNING_RUSH = "morning_rush"
LUNCH = "lunch"
AFTER_WORK_RUSH = "after_work_rush"
OFF_PEAK = "off_peak"

ALL_BUCKETS = [MORNING_RUSH, LUNCH, AFTER_WORK_RUSH, OFF_PEAK]

# (start_minute, end_minute, bucket_name) — end exclusive, ranges don't overlap.
_RUSH_RANGES = [
    (420, 570, MORNING_RUSH),
    (690, 810, LUNCH),
    (960, 1110, AFTER_WORK_RUSH),
]


def bucket_for_minute(minute_of_day: int | None) -> str:
    """Map a minute-of-day (0-1439, or None) to a bucket name.

    Callers may pass absolute (multi-day) minutes — they're normalized mod
    1440 here, so day offset doesn't matter, only time of day.
    """
    if minute_of_day is None:
        return OFF_PEAK
    m = minute_of_day % 1440
    for start, end, name in _RUSH_RANGES:
        if start <= m < end:
            return name
    return OFF_PEAK

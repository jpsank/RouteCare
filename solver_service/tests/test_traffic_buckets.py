"""Tests for traffic-bucket selection: bucket boundaries, and that
SolverContext.travel picks the right bucket's matrix (falling back to
off-peak when the time isn't known, and normalizing a flat/legacy matrix
into a single off-peak bucket)."""

from __future__ import annotations

from solver.context import build_context
from solver.traffic_buckets import (
    AFTER_WORK_RUSH,
    ALL_BUCKETS,
    LUNCH,
    MORNING_RUSH,
    OFF_PEAK,
    bucket_for_minute,
)
from tests.helpers import make_input, mk_instance, mk_patient


def test_bucket_for_minute_boundaries():
    assert bucket_for_minute(None) == OFF_PEAK

    # Morning rush: [420, 570)
    assert bucket_for_minute(419) == OFF_PEAK
    assert bucket_for_minute(420) == MORNING_RUSH
    assert bucket_for_minute(480) == MORNING_RUSH
    assert bucket_for_minute(569) == MORNING_RUSH
    assert bucket_for_minute(570) == OFF_PEAK

    # Lunch: [690, 810)
    assert bucket_for_minute(689) == OFF_PEAK
    assert bucket_for_minute(690) == LUNCH
    assert bucket_for_minute(720) == LUNCH
    assert bucket_for_minute(809) == LUNCH
    assert bucket_for_minute(810) == OFF_PEAK

    # After-work rush: [960, 1110)
    assert bucket_for_minute(959) == OFF_PEAK
    assert bucket_for_minute(960) == AFTER_WORK_RUSH
    assert bucket_for_minute(1020) == AFTER_WORK_RUSH
    assert bucket_for_minute(1109) == AFTER_WORK_RUSH
    assert bucket_for_minute(1110) == OFF_PEAK

    # A representative off-peak time
    assert bucket_for_minute(600) == OFF_PEAK


def test_bucket_for_minute_normalizes_absolute_multi_day_minutes():
    # Day 1 (minutes 1440-2879) at 8am should behave the same as day 0's 8am.
    day0_8am = 480
    day1_8am = 1440 + 480
    assert bucket_for_minute(day1_8am) == bucket_for_minute(day0_8am) == MORNING_RUSH


def _bucketed_matrix() -> dict:
    """One home<->patient leg, a different cost per bucket, so tests can
    tell which bucket's matrix a given `ctx.travel(..., minute)` call used."""
    return {
        MORNING_RUSH: {"home_0": {"1": 10}, "1": {"home_0": 10, "1": 0}},
        LUNCH: {"home_0": {"1": 20}, "1": {"home_0": 20, "1": 0}},
        AFTER_WORK_RUSH: {"home_0": {"1": 40}, "1": {"home_0": 40, "1": 0}},
        OFF_PEAK: {"home_0": {"1": 30}, "1": {"home_0": 30, "1": 0}},
    }


def test_context_travel_picks_the_bucket_matching_the_given_minute():
    p = [mk_patient(1)]
    i = [mk_instance("i1", 1)]
    inp = make_input(p, i, matrix=_bucketed_matrix())
    ctx = build_context(inp)

    assert ctx.travel("home_0", "1", 480) == 10  # morning rush
    assert ctx.travel("home_0", "1", 720) == 20  # lunch
    assert ctx.travel("home_0", "1", 1020) == 40  # after-work rush
    assert ctx.travel("home_0", "1", 600) == 30  # off-peak

    # Omitting minute_of_day falls back to off-peak, same as passing None.
    assert ctx.travel("home_0", "1") == 30
    assert ctx.travel("home_0", "1", None) == 30


def test_context_travel_accepts_a_flat_legacy_matrix_as_a_single_off_peak_bucket():
    p = [mk_patient(1)]
    i = [mk_instance("i1", 1)]
    flat_matrix = {"home_0": {"1": 15}, "1": {"home_0": 15, "1": 0}}
    inp = make_input(p, i, matrix=flat_matrix)
    ctx = build_context(inp)

    # No matter what minute is requested, a flat matrix only has one bucket
    # to offer — behavior is unchanged from before bucketing existed.
    assert ctx.travel("home_0", "1") == 15
    assert ctx.travel("home_0", "1", 480) == 15
    assert ctx.travel("home_0", "1", 1020) == 15


def test_context_travel_legacy_home_remap_works_per_bucket():
    # "home" (no clinician suffix) is the pre-multi-clinician key format;
    # context.travel should still map "home_0" -> "home" independently
    # within whichever bucket is selected.
    matrix = {
        MORNING_RUSH: {"home": {"1": 5}, "1": {"home": 5, "1": 0}},
        OFF_PEAK: {"home": {"1": 25}, "1": {"home": 25, "1": 0}},
    }
    p = [mk_patient(1)]
    i = [mk_instance("i1", 1)]
    inp = make_input(p, i, matrix=matrix)
    ctx = build_context(inp)

    assert ctx.travel("home_0", "1", 480) == 5
    assert ctx.travel("home_0", "1", 600) == 25


def test_all_buckets_present_in_helper_matrix():
    # Sanity check the fixture actually covers every bucket the real
    # pipeline produces, so the picks-the-right-bucket test above is
    # exercising all of them.
    assert set(_bucketed_matrix().keys()) == set(ALL_BUCKETS)

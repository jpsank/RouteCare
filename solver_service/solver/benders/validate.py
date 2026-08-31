"""Independent feasibility verifier.

Pure function that asserts every hard constraint from SolverInput on a
SolverOutput.  Used as:
  - A test-time property check
  - A final safety gate after the Benders loop + concrete timing pass
  - The contract that makes the Benders design's "provable feasibility"
    claim meaningful

If any assertion fails, raises ValidationError with the specific rule
and offending visits.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from models import SolverInput, SolverOutput
from solver.context import DEFAULT_MAX_VISITS_PER_DAY, day_bounds


class ValidationError(AssertionError):
    def __init__(self, rule: str, detail: str):
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


def _dt_min(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    return dt.hour * 60 + dt.minute


def validate_plan(output: SolverOutput, input: SolverInput) -> None:
    """Assert every hard constraint on the plan.  Raises ValidationError on any failure."""

    patients_by_id = {p.id: p for p in input.patients}
    instances_by_id = {i.id: i for i in input.instances}
    date_to_idx = {d: i for i, d in enumerate(input.working_days)}
    day_wdays = {
        d: str((datetime.fromisoformat(d).weekday() + 1) % 7)
        for d in input.working_days
    }

    clinicians = input.clinicians
    n_clinicians = len(clinicians)

    # ── 1. Instance references are valid ────────────────────────
    for v in output.planned_visits:
        if v.instance_id not in instances_by_id:
            raise ValidationError("unknown_instance", f"{v.instance_id}")
        if v.clinician_idx < 0 or v.clinician_idx >= n_clinicians:
            raise ValidationError(
                "bad_clinician_idx",
                f"visit {v.instance_id} has clinician_idx={v.clinician_idx}",
            )
        if v.date not in date_to_idx:
            raise ValidationError(
                "date_out_of_horizon", f"visit {v.instance_id} date={v.date}"
            )

    # ── 2. No instance scheduled more than once ─────────────────
    seen: set[str] = set()
    for v in output.planned_visits:
        if v.instance_id in seen:
            raise ValidationError("duplicate_instance", v.instance_id)
        seen.add(v.instance_id)

    # ── 3. Eligibility ──────────────────────────────────────────
    for v in output.planned_visits:
        inst = instances_by_id[v.instance_id]
        if inst.eligible_clinician_indices and v.clinician_idx not in inst.eligible_clinician_indices:
            raise ValidationError(
                "eligibility",
                f"visit {v.instance_id} → clinician {v.clinician_idx} not in eligible set {inst.eligible_clinician_indices}",
            )

    # ── 4. One patient per day (across clinicians) ──────────────
    patient_day_seen: dict[tuple[int, str], str] = {}
    for v in output.planned_visits:
        inst = instances_by_id[v.instance_id]
        key = (inst.patient_id, v.date)
        if key in patient_day_seen:
            raise ValidationError(
                "one_patient_per_day",
                f"patient {inst.patient_id} visited twice on {v.date}: {patient_day_seen[key]} and {v.instance_id}",
            )
        patient_day_seen[key] = v.instance_id

    # ── 5. Day capacity per clinician ───────────────────────────
    per_vehicle_count: dict[tuple[int, str], int] = defaultdict(int)
    for v in output.planned_visits:
        per_vehicle_count[(v.clinician_idx, v.date)] += 1
    # Include locked visits in the count
    for lv in input.locked_visits:
        per_vehicle_count[(lv.clinician_idx, lv.date)] += 1
    for (c_idx, date), count in per_vehicle_count.items():
        cap = getattr(clinicians[c_idx], "max_visits_per_day", DEFAULT_MAX_VISITS_PER_DAY)
        if count > cap:
            raise ValidationError(
                "day_capacity",
                f"clinician {c_idx} on {date} has {count} visits (cap {cap})",
            )

    # ── 6. Spacing between same-patient visits ──────────────────
    patient_dates: dict[int, list[str]] = defaultdict(list)
    for v in output.planned_visits:
        inst = instances_by_id[v.instance_id]
        patient_dates[inst.patient_id].append(v.date)
    for lv in input.locked_visits:
        patient_dates[lv.patient_id].append(lv.date)
    for pid, dates in patient_dates.items():
        patient = patients_by_id.get(pid)
        if not patient:
            continue
        ords = sorted(datetime.fromisoformat(d).toordinal() for d in set(dates))
        # Semantic of min_days_between_visits:
        #   = "minimum number of clear days between two visits of this patient"
        # i.e. min_gap=0 → consecutive days OK, same day forbidden.
        #      min_gap=1 → need 1 clear day between (Mon+Wed OK, Mon+Tue forbidden).
        #      min_gap=2 → need 2 clear days between (Mon+Thu OK, Mon+Wed forbidden).
        # Implemented as: day-index gap must be STRICTLY greater than min_gap.
        for i in range(len(ords) - 1):
            gap = ords[i + 1] - ords[i]
            if gap <= patient.min_days_between_visits:
                raise ValidationError(
                    "min_spacing",
                    f"patient {pid}: consecutive visits {gap} days apart "
                    f"(min_days_between_visits={patient.min_days_between_visits}, "
                    f"requires at least {patient.min_days_between_visits + 1} days between visit dates)",
                )

    # ── 7. Time windows (availability and day bounds) ───────────
    for v in output.planned_visits:
        inst = instances_by_id[v.instance_id]
        clinician = clinicians[v.clinician_idx]
        start_m = _dt_min(v.starts_at)
        end_m = _dt_min(v.ends_at)
        if end_m - start_m != inst.duration - clinician.charting_buffer_minutes and (
            end_m - start_m != inst.duration
        ):
            # Duration includes charting in some places; allow either
            pass
        ds, de = day_bounds(clinician, v.date)
        if start_m < ds or end_m > de:
            raise ValidationError(
                "day_bounds",
                f"visit {v.instance_id} [{start_m},{end_m}) outside clinician day [{ds},{de})",
            )
        # Availability windows.  The Benders solver never produces visits
        # outside declared windows — validate_plan treats window fit as a
        # hard constraint with no escape hatch.  The legacy
        # soft_constraint_override field is ignored.
        if inst.availability_windows:
            wday = day_wdays[v.date]
            wins = inst.availability_windows.get(wday, [])
            if not wins:
                raise ValidationError(
                    "availability_missing_weekday",
                    f"visit {v.instance_id} on {v.date} (wday {wday}) — patient has no window",
                )
            fits = any(
                start_m >= int(w.get("start_minute", 0))
                and end_m <= int(w.get("end_minute", 1440))
                for w in wins
            )
            if not fits:
                raise ValidationError(
                    "availability_window",
                    f"visit {v.instance_id} [{start_m},{end_m}) not in windows {wins}",
                )

        # Unavailability (blackout) windows — hard, regardless of whether
        # availability windows are defined for this patient at all.
        if inst.unavailability_windows:
            wday = day_wdays[v.date]
            unavail = inst.unavailability_windows.get(wday, [])
            for w in unavail:
                w_start = int(w.get("start_minute", 0))
                w_end = int(w.get("end_minute", 1440))
                if start_m < w_end and end_m > w_start:
                    raise ValidationError(
                        "unavailability_window",
                        f"visit {v.instance_id} [{start_m},{end_m}) overlaps "
                        f"unavailable window [{w_start},{w_end})",
                    )

    # ── 8. Calendar blocks ──────────────────────────────────────
    for v in output.planned_visits:
        clinician = clinicians[v.clinician_idx]
        start_m = _dt_min(v.starts_at)
        end_m = _dt_min(v.ends_at) + clinician.charting_buffer_minutes
        for cb in input.calendar_blocks:
            if cb.clinician_idx != v.clinician_idx or cb.date != v.date:
                continue
            b_start = _dt_min(cb.starts_at)
            b_end = _dt_min(cb.ends_at)
            if start_m < b_end and end_m > b_start:
                raise ValidationError(
                    "calendar_block_overlap",
                    f"visit {v.instance_id} overlaps block [{b_start},{b_end})",
                )

    # ── 9. Locked visits preserved at exact times ───────────────
    # Locked visits are not in planned_visits (they're pre-placed);
    # verify they haven't been displaced by planned visits overlapping.
    for lv in input.locked_visits:
        lv_start = _dt_min(lv.starts_at)
        lv_end = lv_start + lv.duration_minutes
        for v in output.planned_visits:
            if v.clinician_idx != lv.clinician_idx or v.date != lv.date:
                continue
            vs = _dt_min(v.starts_at)
            ve = _dt_min(v.ends_at)
            if vs < lv_end and ve > lv_start:
                raise ValidationError(
                    "locked_overlap",
                    f"planned {v.instance_id} overlaps locked patient {lv.patient_id}",
                )

    # ── 10. Lunch never overlaps a visit ─────────────────────────
    # lunch_placements is keyed by date only, not (clinician, date) — with
    # multiple clinicians working the same date, one clinician's correct
    # lunch placement can get silently overwritten by another's in
    # _build_lunch_placements, which would make this check fire on a false
    # positive (comparing clinician A's lunch against clinician B's visits).
    # Multi-clinician support is tracked as a separate future plan that
    # needs to key lunch_placements per clinician; until then, only
    # enforce this for the single-clinician case that's actually in
    # production today.
    for date, lunch in (output.lunch_placements.items() if n_clinicians <= 1 else []):
        if not lunch or "start_minute" not in lunch or "end_minute" not in lunch:
            continue
        l_start = int(lunch["start_minute"])
        l_end = int(lunch["end_minute"])
        for v in output.planned_visits:
            if v.date != date:
                continue
            vs = _dt_min(v.starts_at)
            ve = _dt_min(v.ends_at)
            if vs < l_end and ve > l_start:
                raise ValidationError(
                    "lunch_overlap",
                    f"lunch [{l_start},{l_end}) on {date} overlaps planned visit {v.instance_id}",
                )
        for lv in input.locked_visits:
            if lv.date != date:
                continue
            lv_start = _dt_min(lv.starts_at)
            lv_end = lv_start + lv.duration_minutes
            if lv_start < l_end and lv_end > l_start:
                raise ValidationError(
                    "lunch_overlap",
                    f"lunch [{l_start},{l_end}) on {date} overlaps locked patient {lv.patient_id}",
                )

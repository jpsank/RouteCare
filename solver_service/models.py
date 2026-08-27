"""Pydantic models matching Ruby SolverInputData / SolverOutputData."""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class Location(BaseModel):
    lat: float
    lng: float


class PatientData(BaseModel):
    id: int
    name: str
    location: Location | None = None
    visit_duration_minutes: int
    required_visits: int
    min_days_between_visits: int = 1
    max_days_between_visits: int = 7
    priority: int = 0
    availability_windows: dict[
        str, list[dict]
    ] = {}  # "day_of_week" → [{start_minute, end_minute}]
    unavailability_windows: dict[
        str, list[dict]
    ] = {}  # "day_of_week" → [{start_minute, end_minute}] — blackout ranges, never overlap


class ClinicianData(BaseModel):
    home_location: Location | None = None
    workday_start_minute: int = 480
    workday_end_minute: int = 1080
    working_days: list[int] = [1, 2, 3, 4, 5]
    lunch_start_minute: int = 720
    lunch_duration_minutes: int = 30
    lunch_window_minutes: int = 90
    max_continuous_work_minutes: int = 480
    required_break_minutes: int = 15
    max_drive_minutes_per_day: int | None = None
    max_visits_per_day: int = 5
    schedule_density: float = 0.5
    charting_buffer_minutes: int = 0
    per_day_hours: dict[str, dict[str, int]] = {}  # "wday" → {"start": min, "end": min}


class VisitInstanceData(BaseModel):
    id: str  # "patient_{id}_visit_{index}"
    patient_id: int
    location: Location | None = None
    duration: int  # visit_duration_minutes + charting_buffer
    priority: int = 0
    availability_windows: dict[str, list[dict]] = {}
    unavailability_windows: dict[str, list[dict]] = {}  # blackout ranges, never overlap
    eligible_clinician_indices: list[int] = []  # empty = any clinician


class LockedVisitData(BaseModel):
    patient_id: int
    clinician_idx: int = 0
    date: str  # ISO date
    starts_at: str  # ISO datetime
    ends_at: str  # ISO datetime
    duration_minutes: int


class CalendarBlockData(BaseModel):
    clinician_idx: int = 0
    date: str  # ISO date
    starts_at: str  # ISO datetime
    ends_at: str  # ISO datetime


class SolverInput(BaseModel):
    patients: list[PatientData]
    clinician: ClinicianData | None = None
    clinicians: list[ClinicianData] = []
    instances: list[VisitInstanceData]
    locked_visits: list[LockedVisitData] = []
    calendar_blocks: list[CalendarBlockData] = []
    # bucket_name -> from_id -> to_id -> minutes. JSON keys are always strings.
    # bucket_name is one of solver.traffic_buckets.ALL_BUCKETS (e.g.
    # "morning_rush", "lunch", "after_work_rush", "off_peak"). A plain flat
    # matrix (from_id -> to_id -> minutes) is also accepted for backward
    # compatibility / hand-built test fixtures — see context.build_context,
    # which normalizes it into a single "off_peak" bucket.
    travel_matrix: dict[str, dict]
    start_date: str  # ISO date — first day of the scheduling horizon
    working_days: list[str]  # ISO dates

    @model_validator(mode="before")
    @classmethod
    def normalize_clinicians(cls, data):
        if isinstance(data, dict):
            clinician = data.get("clinician")
            clinicians = data.get("clinicians")
            if clinician and not clinicians:
                data["clinicians"] = [clinician]
            elif clinicians and not clinician:
                data["clinician"] = clinicians[0]
            elif not clinician and not clinicians:
                data["clinician"] = {}
                data["clinicians"] = [{}]
        return data


class PlannedVisit(BaseModel):
    instance_id: str
    patient_id: int
    clinician_idx: int = 0
    date: str  # ISO date
    starts_at: str  # ISO datetime
    ends_at: str  # ISO datetime
    soft_constraint_override: bool = False


class SolverOutput(BaseModel):
    planned_visits: list[PlannedVisit]
    lunch_placements: dict[str, dict]  # date_string → {start_minute, end_minute}
    fitness: float = 0.0
    metadata: dict = {}


class SolveRequest(SolverInput):
    """POST /solve body: full SolverInput fields plus optional prior solution for warm-start."""

    upper_bound: SolverOutput | None = None

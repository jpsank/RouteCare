"""Pydantic models matching Ruby SolverInputData / SolverOutputData."""

from __future__ import annotations
from pydantic import BaseModel


class Location(BaseModel):
    lat: float
    lng: float


class PatientData(BaseModel):
    id: int
    name: str
    location: Location | None = None
    visit_duration_minutes: int
    required_visits_per_week: int
    min_days_between_visits: int = 1
    max_days_between_visits: int = 7
    priority: int = 0
    availability_windows: dict[str, list[dict]] = {}  # "day_of_week" → [{start_minute, end_minute}]


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
    schedule_density: float = 0.5
    charting_buffer_minutes: int = 0


class VisitInstanceData(BaseModel):
    id: str  # "patient_{id}_visit_{index}"
    patient_id: int
    location: Location | None = None
    duration: int  # visit_duration_minutes + charting_buffer
    priority: int = 0
    availability_windows: dict[str, list[dict]] = {}


class LockedVisitData(BaseModel):
    patient_id: int
    date: str  # ISO date
    starts_at: str  # ISO datetime
    ends_at: str  # ISO datetime
    duration_minutes: int


class CalendarBlockData(BaseModel):
    date: str  # ISO date
    starts_at: str  # ISO datetime
    ends_at: str  # ISO datetime


class SolverInput(BaseModel):
    patients: list[PatientData]
    clinician: ClinicianData
    instances: list[VisitInstanceData]
    locked_visits: list[LockedVisitData] = []
    calendar_blocks: list[CalendarBlockData] = []
    travel_matrix: dict[str, dict[str, int]]  # JSON keys are always strings
    week_start_on: str  # ISO date
    working_days: list[str]  # ISO dates


class PlannedVisit(BaseModel):
    instance_id: str
    patient_id: int
    date: str  # ISO date
    starts_at: str  # ISO datetime
    ends_at: str  # ISO datetime
    soft_constraint_override: bool = False


class SolverOutput(BaseModel):
    planned_visits: list[PlannedVisit]
    lunch_placements: dict[str, dict]  # date_string → {start_minute, end_minute}
    fitness: float = 0.0
    metadata: dict = {}

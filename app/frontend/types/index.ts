export type AvailabilityWindow = {
  id?: number;
  day_of_week: number;
  start_minute: number;
  end_minute: number;
};

export type CreatePatientPayload = {
  full_name: string;
  phone: string;
  email?: string;
  address_line1: string;
  city: string;
  state: string;
  postal_code: string;
  required_visits_per_week: number;
  visit_duration_minutes: number;
  notes?: string;
};

export type Patient = {
  id: number;
  full_name: string;
  phone: string;
  email?: string | null;
  address_line1?: string;
  address_line2?: string | null;
  city?: string;
  state?: string;
  postal_code?: string;
  address: string;
  required_visits_per_week: number;
  visit_duration_minutes: number;
  active: boolean;
  notes?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  availability_windows: AvailabilityWindow[];
};

export type Visit = {
  id: number;
  patient_id: number;
  patient_name: string;
  patient_address?: string;
  patient_latitude?: number | null;
  patient_longitude?: number | null;
  starts_at: string;
  ends_at: string;
  duration_minutes: number;
  status: "confirmed" | "pending_patient_confirmation" | "declined" | "unscheduled";
  position_in_day: number;
  drive_from_previous_minutes: number;
  clinician_override: boolean;
  soft_constraint_override: boolean;
  source: "optimizer" | "manual" | "reschedule";
  external_calendar_event_id?: string | null;
};

export type WeeklySchedule = {
  id: number;
  week_start_on: string;
  status: "draft" | "optimized" | "approved" | "archived";
  total_drive_minutes: number;
  baseline_drive_minutes: number;
  drive_minutes_saved: number;
  optimization_summary: Record<string, unknown>;
  visits: Visit[];
};

export type Message = {
  id: number;
  visit_id: number | null;
  patient_id: number;
  direction: "outbound" | "inbound";
  channel: "sms" | "email";
  status: "draft" | "pending_approval" | "queued" | "sent" | "received" | "failed";
  body: string;
  requires_approval: boolean;
  approved_at: string | null;
  proposed_starts_at: string | null;
  proposed_ends_at: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type CalendarBlock = {
  id: number;
  source: "external_calendar" | "manual_block" | "generated_visit";
  title?: string | null;
  starts_at: string;
  ends_at: string;
};

export type CalendarConnection = {
  id: number;
  provider: "google" | "outlook" | "apple";
  external_calendar_id: string;
  status: "active" | "disconnected" | "expired";
  token_expires_at?: string | null;
  metadata: Record<string, unknown>;
};

export type CalendarOption = {
  id: string;
  summary: string;
  primary: boolean;
  access_role?: string;
};

export type Alert = {
  id: number;
  category: string;
  severity: "low" | "medium" | "high";
  status: "open" | "acknowledged" | "resolved";
  message: string;
  due_at?: string | null;
  read_at?: string | null;
  metadata: Record<string, unknown>;
};

export type DashboardApiState = {
  schedule: WeeklySchedule | null;
  patients: ReadonlyArray<Patient>;
  messages: ReadonlyArray<Message>;
  alerts: ReadonlyArray<Alert>;
};

export type ClinicianProfile = {
  id: number;
  timezone: string;
  discipline: string;
  workday_start_minute: number;
  workday_end_minute: number;
  home_latitude?: number | null;
  home_longitude?: number | null;
  working_days_mask?: number;
  working_days?: number[];
  lunch_start_minute: number;
  lunch_duration_minutes: number;
  lunch_window_minutes: number;
};

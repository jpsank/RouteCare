export type AvailabilityWindow = {
  id?: number;
  day_of_week: number;
  start_minute: number;
  end_minute: number;
};

export type CreatePatientPayload = {
  first_name: string;
  last_name: string;
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
  first_name: string;
  last_name: string;
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
  min_days_between_visits: number;
  max_days_between_visits: number;
  priority: number;
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
  status: "confirmed" | "pending_patient_confirmation" | "declined" | "unscheduled" | "completed";
  position_in_day: number;
  drive_from_previous_minutes: number;
  clinician_override: boolean;
  soft_constraint_override: boolean;
  source: "optimizer" | "manual" | "reschedule";
  external_calendar_event_id?: string | null;
};

export type OptimizationSummary = {
  unschedulable?: Array<{ patient_name: string; patient_id: number; reasons?: string[] }>;
  drive_violations?: Array<{ date: string; drive_minutes: number; max_drive: number }>;
  lunch_breaks?: Record<string, { start_minute: number; end_minute: number }>;
  return_home_by_day?: Record<string, number>;
  route_winners?: Record<string, string>;
  scheduler_fallback?: string;
  optimizer_type?: string;
  iterations?: number;
  status?: string;
  soft_constraint_overrides?: number;
};

export type WeeklySchedule = {
  id: number;
  week_start_on: string;
  status: "draft" | "optimized" | "approved" | "archived";
  total_drive_minutes: number;
  baseline_drive_minutes: number;
  drive_minutes_saved: number;
  optimization_summary: OptimizationSummary;
  visits: Visit[];
};

export type MessageMetadata = {
  suggested_visit_times?: Array<{ starts_at: string; ends_at: string }>;
  parsed_intent?: string;
  proposed_windows?: Array<{ start_minute?: number; end_minute?: number }>;
  transport?: string;
  queued_at?: string;
  sent_at?: string;
  provider_message_id?: string;
  delivery_error?: string;
};

export type Message = {
  id: number;
  visit_id: number | null;
  patient_id: number;
  patient_name?: string | null;
  direction: "outbound" | "inbound";
  channel: "sms" | "email";
  status: "draft" | "pending_approval" | "queued" | "sent" | "received" | "failed";
  body: string;
  requires_approval: boolean;
  approved_at: string | null;
  proposed_starts_at: string | null;
  proposed_ends_at: string | null;
  metadata: MessageMetadata;
  created_at: string;
};

export type CalendarBlock = {
  id: number;
  source: "external_calendar" | "manual_block" | "generated_visit";
  title?: string | null;
  starts_at: string;
  ends_at: string;
};

export type CalendarConnectionMetadata = {
  calendar_name?: string;
  ics_url?: string;
};

export type CalendarConnection = {
  id: number;
  provider: "google" | "outlook" | "apple";
  external_calendar_id: string;
  status: "active" | "disconnected" | "expired";
  token_expires_at?: string | null;
  metadata: CalendarConnectionMetadata;
};

export type CalendarOption = {
  id: string;
  summary: string;
  primary: boolean;
  access_role?: string;
};

export type AlertMetadata = {
  visit_id?: number;
  weekly_schedule_id?: number;
};

export type Alert = {
  id: number;
  category: string;
  severity: "low" | "medium" | "high";
  status: "open" | "acknowledged" | "resolved";
  message: string;
  due_at?: string | null;
  read_at?: string | null;
  metadata: AlertMetadata;
  created_at: string;
};

export type DashboardApiState = {
  schedule: WeeklySchedule | null;
  patients: ReadonlyArray<Patient>;
  messages: ReadonlyArray<Message>;
  alerts: ReadonlyArray<Alert>;
};

export type ClinicianProfile = {
  id: number;
  display_name?: string | null;
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
  setup_completed_at?: string | null;
  max_continuous_work_minutes: number;
  required_break_minutes: number;
  max_drive_minutes_per_day?: number | null;
  schedule_density: number;
  charting_buffer_minutes: number;
  per_day_hours?: Record<string, { start: number; end: number }>;
};

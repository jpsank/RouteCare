export type AvailabilityWindow = {
  id?: number;
  day_of_week: number;
  start_minute: number;
  end_minute: number;
};

export type Patient = {
  id: number;
  full_name: string;
  phone: string;
  email?: string | null;
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

export type Schedule = {
  id: number;
  week_start_on: string;
  status: "draft" | "clinician_approved" | "partially_confirmed" | "finalized";
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
  created_at: string;
};

export type CalendarBlock = {
  id: number;
  source: "external_calendar" | "manual_block" | "generated_visit";
  title?: string | null;
  starts_at: string;
  ends_at: string;
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
  schedule: Schedule | null;
  patients: ReadonlyArray<Patient>;
  messages: ReadonlyArray<Message>;
  alerts: ReadonlyArray<Alert>;
};

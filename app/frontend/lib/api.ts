import type {
  Alert,
  CalendarBlock,
  CalendarConnection,
  CalendarOption,
  Message,
  Patient,
  ClinicianProfile,
  Visit,
  Schedule as WeeklySchedule,
} from "../types";

type JsonObject = Record<string, unknown>;

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as JsonObject;
    const error = (payload.error as string) || JSON.stringify(payload) || response.statusText;
    throw new Error(error);
  }

  return (await response.json()) as T;
}

function browserTimeZone(): string | undefined {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
}

export async function fetchPatients(): Promise<Patient[]> {
  const data = await request<{ patients: Patient[] }>("/api/v1/patients");
  return data.patients;
}

export async function fetchClinicianProfile(): Promise<ClinicianProfile> {
  const data = await request<{ clinician_profile: ClinicianProfile }>("/api/v1/clinician_profile");
  return data.clinician_profile;
}

export async function fetchSchedule(weekStartOn?: string): Promise<WeeklySchedule | null> {
  try {
    const query = weekStartOn ? `?week_start_on=${encodeURIComponent(weekStartOn)}` : "";
    const data = await request<{ schedule: WeeklySchedule }>(`/api/v1/schedule${query}`);
    return data.schedule;
  } catch {
    return null;
  }
}

export async function optimizeSchedule(weekStartOn?: string): Promise<WeeklySchedule> {
  const data = await request<{ schedule: WeeklySchedule; generated: boolean }>("/api/v1/schedule/optimize", {
    method: "POST",
    body: JSON.stringify({
      week_start_on: weekStartOn,
      client_timezone: browserTimeZone(),
    }),
  });

  return data.schedule;
}

export async function optimizeScheduleWithStart(
  weekStartOn: string | undefined,
  startLatitude?: number,
  startLongitude?: number,
): Promise<WeeklySchedule> {
  const data = await request<{ schedule: WeeklySchedule; generated: boolean }>("/api/v1/schedule/optimize", {
    method: "POST",
    body: JSON.stringify({
      week_start_on: weekStartOn,
      start_latitude: startLatitude,
      start_longitude: startLongitude,
      client_timezone: browserTimeZone(),
    }),
  });
  return data.schedule;
}

export async function approveSchedule(weekStartOn?: string): Promise<WeeklySchedule> {
  const data = await request<{ schedule: WeeklySchedule }>("/api/v1/schedule/approve", {
    method: "POST",
    body: JSON.stringify({ week_start_on: weekStartOn }),
  });
  return data.schedule;
}

export async function fetchVisits(weekStartOn?: string): Promise<Visit[]> {
  const query = weekStartOn ? `?week_start_on=${encodeURIComponent(weekStartOn)}` : "";
  const data = await request<{ visits: Visit[] }>(`/api/v1/visits${query}`);
  return data.visits;
}

export async function rescheduleVisit(id: number, requestedStartsAt: string): Promise<Visit> {
  const data = await request<{ visit: Visit }>(`/api/v1/visits/${id}/reschedule`, {
    method: "POST",
    body: JSON.stringify({ requested_starts_at: requestedStartsAt }),
  });
  return data.visit;
}

export async function updateVisit(id: number, visit: Partial<Pick<Visit, "status" | "starts_at" | "ends_at" | "position_in_day">>): Promise<Visit> {
  const data = await request<{ visit: Visit }>(`/api/v1/visits/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ visit }),
  });
  return data.visit;
}

export async function fetchMessages(): Promise<Message[]> {
  const data = await request<{ messages: Message[] }>("/api/v1/messages");
  return data.messages;
}

export async function sendVisitMessage(
  visitId: number,
  channel: "sms" | "email",
  body: string,
  sendImmediately = false,
): Promise<Message> {
  const data = await request<{ message: Message }>("/api/v1/messages", {
    method: "POST",
    body: JSON.stringify({
      message: {
        visit_id: visitId,
        channel,
        body,
        send_immediately: sendImmediately,
      },
    }),
  });
  return data.message;
}

export async function fetchCalendarBlocks(
  weekStartOn?: string,
  weekEndOn?: string,
): Promise<CalendarBlock[]> {
  const query = new URLSearchParams();
  if (weekStartOn) query.set("week_start_on", weekStartOn);
  if (weekEndOn) query.set("week_end_on", weekEndOn);
  const data = await request<{ calendar_blocks: CalendarBlock[] }>(
    `/api/v1/calendar_blocks${query.toString() ? `?${query.toString()}` : ""}`,
  );
  return data.calendar_blocks;
}

export async function fetchAlerts(): Promise<Alert[]> {
  const data = await request<{ alerts: Alert[] }>("/api/v1/alerts");
  return data.alerts;
}

type CreatePatientPayload = {
  full_name: string;
  phone: string;
  email?: string;
  address_line1: string;
  address_line2?: string;
  city: string;
  state: string;
  postal_code: string;
  required_visits_per_week: number;
  visit_duration_minutes: number;
  notes?: string;
  latitude?: number;
  longitude?: number;
};

export async function createPatient(patient: CreatePatientPayload): Promise<{ patient: Patient }> {
  return request<{ patient: Patient }>("/api/v1/patients", {
    method: "POST",
    body: JSON.stringify({ patient }),
  });
}

export async function updatePatient(patientId: number, patient: Partial<CreatePatientPayload>): Promise<{ patient: Patient }> {
  return request<{ patient: Patient }>(`/api/v1/patients/${patientId}`, {
    method: "PATCH",
    body: JSON.stringify({ patient }),
  });
}

export const api = {
  getSchedule: async (weekStartOn?: string) => {
    try {
      return await request<{ schedule: WeeklySchedule }>(
        `/api/v1/schedule${weekStartOn ? `?week_start_on=${encodeURIComponent(weekStartOn)}` : ""}`,
      );
    } catch (error) {
      if (error instanceof Error && error.message === "Schedule not found") {
        return { schedule: null };
      }
      throw error;
    }
  },
  optimizeSchedule: (weekStartOn?: string, startLatitude?: number, startLongitude?: number) =>
    request<{ schedule: WeeklySchedule; generated: boolean }>("/api/v1/schedule/optimize", {
      method: "POST",
      body: JSON.stringify({
        week_start_on: weekStartOn,
        start_latitude: startLatitude,
        start_longitude: startLongitude,
        client_timezone: browserTimeZone(),
      }),
    }),
  approveSchedule: (weekStartOn?: string) =>
    request<{ schedule: WeeklySchedule }>("/api/v1/schedule/approve", {
      method: "POST",
      body: JSON.stringify({ week_start_on: weekStartOn }),
    }),
  listPatients: () => request<{ patients: Patient[] }>("/api/v1/patients"),
  createPatient: (patient: Partial<CreatePatientPayload>) =>
    request<{ patient: Patient }>("/api/v1/patients", {
      method: "POST",
      body: JSON.stringify({ patient }),
    }),
  updatePatient: (id: number, patient: Partial<CreatePatientPayload>) =>
    request<{ patient: Patient }>(`/api/v1/patients/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ patient }),
    }),
  seedDemoPatients: () =>
    request<{ patients: Patient[] }>("/api/v1/patients/seed_demo", {
      method: "POST",
    }),
  getClinicianProfile: () => request<{ clinician_profile: ClinicianProfile }>("/api/v1/clinician_profile"),
  updateClinicianProfile: (
    clinician_profile: Partial<
      Pick<
        ClinicianProfile,
        | "workday_start_minute"
        | "workday_end_minute"
        | "timezone"
        | "home_latitude"
        | "home_longitude"
        | "working_days"
        | "lunch_start_minute"
        | "lunch_duration_minutes"
        | "lunch_window_minutes"
      >
    >,
  ) =>
    request<{ clinician_profile: ClinicianProfile }>("/api/v1/clinician_profile", {
      method: "PATCH",
      body: JSON.stringify({ clinician_profile }),
    }),
  listMessages: () => request<{ messages: Message[] }>("/api/v1/messages"),
  createMessage: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) =>
    request<{ message: Message }>("/api/v1/messages", {
      method: "POST",
      body: JSON.stringify({
        message: {
          visit_id: visitId,
          channel,
          body,
          send_immediately: sendImmediately,
        },
      }),
    }),
  listAlerts: () => request<{ alerts: Alert[] }>("/api/v1/alerts"),
  listCalendarBlocks: (weekStartOn?: string, weekEndOn?: string) => {
    const query = new URLSearchParams();
    if (weekStartOn) query.set("week_start_on", weekStartOn);
    if (weekEndOn) query.set("week_end_on", weekEndOn);
    return request<{ calendar_blocks: CalendarBlock[] }>(
      `/api/v1/calendar_blocks${query.toString() ? `?${query.toString()}` : ""}`,
    );
  },
  listCalendarConnections: () => request<{ calendar_connections: CalendarConnection[] }>("/api/v1/calendar_connections"),
  connectCalendar: (calendar_connection: {
    provider: "google" | "outlook" | "apple";
    external_calendar_id: string;
    access_token?: string;
    refresh_token?: string;
    token_expires_at?: string;
    metadata?: Record<string, unknown>;
  }) =>
    request<{ calendar_connection: CalendarConnection }>("/api/v1/calendar_connections", {
      method: "POST",
      body: JSON.stringify({
        calendar_connection: {
          ...calendar_connection,
          access_token: calendar_connection.access_token ?? "n/a",
          refresh_token: calendar_connection.refresh_token ?? "n/a",
          metadata: calendar_connection.metadata ?? {},
        },
      }),
    }),
  syncCalendarConnection: (connectionId: number, weekStartOn: string, weekEndOn: string) =>
    request<{ imported_events: number; total_events: number }>(`/api/v1/calendar_connections/${connectionId}/sync`, {
      method: "POST",
      body: JSON.stringify({
        week_start_on: weekStartOn,
        week_end_on: weekEndOn,
      }),
    }),
  pushVisitsToCalendar: (connectionId: number, weekStartOn?: string) =>
    request<{ pushed_visits: number }>(`/api/v1/calendar_connections/${connectionId}/push_visits`, {
      method: "POST",
      body: JSON.stringify({ week_start_on: weekStartOn }),
    }),
  listConnectionCalendars: (connectionId: number) =>
    request<{ calendars: CalendarOption[] }>(`/api/v1/calendar_connections/${connectionId}/available_calendars`),
  selectConnectionCalendar: (connectionId: number, externalCalendarId: string) =>
    request<{ calendar_connection: CalendarConnection }>(`/api/v1/calendar_connections/${connectionId}/select_calendar`, {
      method: "POST",
      body: JSON.stringify({ external_calendar_id: externalCalendarId }),
    }),
  calendarFeedUrl: (weekStartOn?: string) =>
    `/api/v1/calendar_feed.ics${weekStartOn ? `?week_start_on=${encodeURIComponent(weekStartOn)}` : ""}`,
  listVisits: () => request<{ visits: Visit[] }>("/api/v1/visits"),
  rescheduleVisit: (id: number, requestedStartsAt: string) =>
    request<{ visit: Visit }>(`/api/v1/visits/${id}/reschedule`, {
      method: "POST",
      body: JSON.stringify({ requested_starts_at: requestedStartsAt }),
    }),
  updateVisit: (id: number, visit: Partial<Pick<Visit, "status" | "starts_at" | "ends_at" | "position_in_day">>) =>
    request<{ visit: Visit }>(`/api/v1/visits/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ visit }),
    }),
  createVisit: (visit: { patient_id: number; starts_at: string; duration_minutes?: number; status?: Visit["status"] }) =>
    request<{ visit: Visit }>("/api/v1/visits", {
      method: "POST",
      body: JSON.stringify({ visit }),
    }),
};

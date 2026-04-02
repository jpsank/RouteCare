import type { Alert, CalendarBlock, Message, Patient, Visit, Schedule as WeeklySchedule } from "../types";

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

export async function fetchPatients(): Promise<Patient[]> {
  const data = await request<{ patients: Patient[] }>("/api/v1/patients");
  return data.patients;
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
    body: JSON.stringify({ week_start_on: weekStartOn }),
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
  city: string;
  state: string;
  postal_code: string;
  required_visits_per_week: number;
  visit_duration_minutes: number;
  notes?: string;
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
  optimizeSchedule: (weekStartOn?: string) =>
    request<{ schedule: WeeklySchedule; generated: boolean }>("/api/v1/schedule/optimize", {
      method: "POST",
      body: JSON.stringify({ week_start_on: weekStartOn }),
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
  listCalendarBlocks: () => request<{ calendar_blocks: CalendarBlock[] }>("/api/v1/calendar_blocks"),
  listVisits: () => request<{ visits: Visit[] }>("/api/v1/visits"),
};

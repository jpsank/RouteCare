import type { Patient, Visit } from "../../types";

export type Point = { latitude: number; longitude: number };

export type RouteStep = {
  instruction: string;
  distanceMeters: number;
  durationSeconds: number;
};

export type RouteSnapshot = {
  geometry: GeoJSON.LineString;
  durationSeconds: number;
  distanceMeters: number;
  steps: RouteStep[];
};

export type PatientSavePayload = {
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

export type PatientForm = {
  full_name: string;
  phone: string;
  email: string;
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  postal_code: string;
  required_visits_per_week: number;
  visit_duration_minutes: number;
  notes: string;
  latitude: string;
  longitude: string;
};

export const EMPTY_PATIENT_FORM: PatientForm = {
  full_name: "",
  phone: "",
  email: "",
  address_line1: "",
  address_line2: "",
  city: "",
  state: "",
  postal_code: "",
  required_visits_per_week: 2,
  visit_duration_minutes: 60,
  notes: "",
  latitude: "",
  longitude: "",
};

export function asDateKey(value: string): string {
  return new Date(value).toISOString().slice(0, 10);
}

export function fmt(date: string): string {
  return new Date(date).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function toLocalInputValue(dateIso: string): string {
  const date = new Date(dateIso);
  const offset = date.getTimezoneOffset();
  const adjusted = new Date(date.getTime() - offset * 60_000);
  return adjusted.toISOString().slice(0, 16);
}

export function localInputToIso(value: string): string {
  return new Date(value).toISOString();
}

export function patientFormFromPatient(patient: Patient): PatientForm {
  return {
    full_name: patient.full_name || "",
    phone: patient.phone || "",
    email: patient.email || "",
    address_line1: patient.address_line1 || "",
    address_line2: patient.address_line2 || "",
    city: patient.city || "",
    state: patient.state || "",
    postal_code: patient.postal_code || "",
    required_visits_per_week: patient.required_visits_per_week || 1,
    visit_duration_minutes: patient.visit_duration_minutes || 60,
    notes: patient.notes || "",
    latitude: patient.latitude != null ? String(patient.latitude) : "",
    longitude: patient.longitude != null ? String(patient.longitude) : "",
  };
}

export function patientPayloadFromForm(form: PatientForm): PatientSavePayload {
  return {
    full_name: form.full_name.trim(),
    phone: form.phone.trim(),
    email: form.email.trim() || undefined,
    address_line1: form.address_line1.trim(),
    address_line2: form.address_line2.trim() || undefined,
    city: form.city.trim(),
    state: form.state.trim(),
    postal_code: form.postal_code.trim(),
    required_visits_per_week: Number(form.required_visits_per_week),
    visit_duration_minutes: Number(form.visit_duration_minutes),
    notes: form.notes.trim() || undefined,
    latitude: form.latitude.trim() ? Number(form.latitude) : undefined,
    longitude: form.longitude.trim() ? Number(form.longitude) : undefined,
  };
}

export function formatDistance(meters: number): string {
  return `${(meters / 1609.34).toFixed(1)} mi`;
}

export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function buildGoogleMapsUrl(stops: Point[], origin?: Point): string {
  if (stops.length === 0) return "https://www.google.com/maps";
  const destination = stops[stops.length - 1];
  const waypoints = stops.slice(0, -1).map((stop) => `${stop.latitude},${stop.longitude}`).join("|");
  const query = new URLSearchParams({
    api: "1",
    destination: `${destination.latitude},${destination.longitude}`,
    travelmode: "driving",
  });
  if (origin) query.set("origin", `${origin.latitude},${origin.longitude}`);
  if (waypoints) query.set("waypoints", waypoints);
  return `https://www.google.com/maps/dir/?${query.toString()}`;
}

export function buildAppleMapsUrl(stops: Point[], origin?: Point): string {
  if (stops.length === 0) return "https://maps.apple.com/";
  const saddr = origin ? `${origin.latitude},${origin.longitude}` : "Current Location";
  const daddr = stops.map((stop) => `${stop.latitude},${stop.longitude}`).join("+to:");
  return `https://maps.apple.com/?saddr=${encodeURIComponent(saddr)}&daddr=${encodeURIComponent(daddr)}&dirflg=d`;
}

export async function fetchRouteSnapshot(token: string, origin: Point, stops: Point[]): Promise<RouteSnapshot> {
  const coords = [origin, ...stops].map((point) => `${point.longitude},${point.latitude}`).join(";");
  const response = await fetch(
    `https://api.mapbox.com/directions/v5/mapbox/driving/${coords}?geometries=geojson&overview=full&steps=true&access_token=${encodeURIComponent(token)}`,
  );
  if (!response.ok) throw new Error("Unable to load route from Mapbox.");
  const payload = (await response.json()) as {
    routes?: Array<{
      geometry?: GeoJSON.LineString;
      duration?: number;
      distance?: number;
      legs?: Array<{ steps?: Array<{ distance?: number; duration?: number; maneuver?: { instruction?: string } }> }>;
    }>;
  };
  const route = payload.routes?.[0];
  if (!route?.geometry) throw new Error("Mapbox did not return a route.");

  const steps: RouteStep[] = [];
  (route.legs || []).forEach((leg) => {
    (leg.steps || []).forEach((step) => {
      if (step.maneuver?.instruction) {
        steps.push({
          instruction: step.maneuver.instruction,
          distanceMeters: step.distance || 0,
          durationSeconds: step.duration || 0,
        });
      }
    });
  });

  return {
    geometry: route.geometry,
    durationSeconds: route.duration || 0,
    distanceMeters: route.distance || 0,
    steps,
  };
}

const PATIENT_PALETTE = [
  { bg: "#dbeafe", text: "#1e40af", accent: "#3b82f6" },
  { bg: "#dcfce7", text: "#166534", accent: "#22c55e" },
  { bg: "#fce7f3", text: "#9d174d", accent: "#ec4899" },
  { bg: "#e0e7ff", text: "#3730a3", accent: "#6366f1" },
  { bg: "#fef3c7", text: "#92400e", accent: "#f59e0b" },
  { bg: "#ccfbf1", text: "#115e59", accent: "#14b8a6" },
  { bg: "#fee2e2", text: "#991b1b", accent: "#ef4444" },
  { bg: "#f3e8ff", text: "#6b21a8", accent: "#a855f7" },
  { bg: "#ffedd5", text: "#9a3412", accent: "#f97316" },
  { bg: "#e0f2fe", text: "#075985", accent: "#0ea5e9" },
  { bg: "#fef9c3", text: "#854d0e", accent: "#eab308" },
  { bg: "#ede9fe", text: "#5b21b6", accent: "#8b5cf6" },
];

export function patientColor(patientId: number): { bg: string; text: string; accent: string } {
  return PATIENT_PALETTE[patientId % PATIENT_PALETTE.length];
}

const STATUS_BORDER: Record<string, string> = {
  confirmed: "#16a34a",
  pending_patient_confirmation: "#d97706",
  declined: "#dc2626",
  unscheduled: "#9ca3af",
};

export function statusBorderColor(status: string): string {
  return STATUS_BORDER[status] ?? "#9ca3af";
}

export function statusOptions(): Array<{ label: string; value: Visit["status"] }> {
  return [
    { label: "Confirmed", value: "confirmed" },
    { label: "Pending Confirmation", value: "pending_patient_confirmation" },
    { label: "Declined", value: "declined" },
    { label: "Unscheduled", value: "unscheduled" },
  ];
}

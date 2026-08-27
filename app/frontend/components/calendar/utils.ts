import { format as formatDate } from "date-fns";
import type { AvailabilityWindow, Patient, Visit } from "../../types";
import { googleMapsKey, mapboxToken } from "./mapProviders";

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
  first_name: string;
  last_name: string;
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
  min_days_between_visits: number;
  max_days_between_visits: number;
  priority: number;
  // Omitted entirely => leave existing windows untouched. Present (including
  // an empty array) => replace the patient's windows with this set.
  availability_windows?: AvailabilityWindow[];
};

export type PatientForm = {
  first_name: string;
  last_name: string;
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
  min_days_between_visits: number;
  max_days_between_visits: number;
  priority: number;
};

export const EMPTY_PATIENT_FORM: PatientForm = {
  first_name: "",
  last_name: "",
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
  min_days_between_visits: 1,
  max_days_between_visits: 7,
  priority: 0,
};

export function asDateKey(value: string): string {
  return formatDate(new Date(value), "yyyy-MM-dd");
}

export function fmt(date: string): string {
  return formatDate(new Date(date), "h:mm a");
}

export function toLocalInputValue(dateIso: string): string {
  return formatDate(new Date(dateIso), "yyyy-MM-dd'T'HH:mm");
}

export function localInputToIso(value: string): string {
  return new Date(value).toISOString();
}

export function patientFormFromPatient(patient: Patient): PatientForm {
  return {
    first_name: patient.first_name || "",
    last_name: patient.last_name || "",
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
    min_days_between_visits: patient.min_days_between_visits ?? 1,
    max_days_between_visits: patient.max_days_between_visits ?? 7,
    priority: patient.priority ?? 0,
  };
}

export function patientPayloadFromForm(form: PatientForm): PatientSavePayload {
  return {
    first_name: form.first_name.trim(),
    last_name: form.last_name.trim(),
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
    min_days_between_visits: Number(form.min_days_between_visits),
    max_days_between_visits: Number(form.max_days_between_visits),
    priority: Number(form.priority),
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

// Cascades Mapbox Directions -> Google Routes -> OSRM (free, keyless) -> a
// straight-line estimate, so a suspended/missing Mapbox token degrades
// gracefully instead of breaking the map.
export async function fetchRouteSnapshot(origin: Point, stops: Point[]): Promise<RouteSnapshot> {
  const mapbox = mapboxToken();
  if (mapbox) {
    try {
      return await fetchMapboxRoute(mapbox, origin, stops);
    } catch (error) {
      console.warn("Mapbox directions failed, falling back to Google:", error);
    }
  }

  const google = googleMapsKey();
  if (google) {
    try {
      return await fetchGoogleRoute(google, origin, stops);
    } catch (error) {
      console.warn("Google directions failed, falling back to OSRM:", error);
    }
  }

  try {
    return await fetchOsrmRoute(origin, stops);
  } catch (error) {
    console.warn("OSRM directions failed, falling back to a straight-line estimate:", error);
  }

  return straightLineRoute(origin, stops);
}

async function fetchOsrmRoute(origin: Point, stops: Point[]): Promise<RouteSnapshot> {
  const coords = [ origin, ...stops ].map((point) => `${point.longitude},${point.latitude}`).join(";");
  const response = await fetch(
    `https://router.project-osrm.org/route/v1/driving/${coords}?overview=full&geometries=geojson`,
  );
  if (!response.ok) throw new Error("Unable to load route from OSRM.");

  const payload = (await response.json()) as {
    routes?: Array<{ geometry?: GeoJSON.LineString; duration?: number; distance?: number }>;
  };
  const route = payload.routes?.[0];
  if (!route?.geometry) throw new Error("OSRM did not return a route.");

  return {
    geometry: route.geometry,
    durationSeconds: route.duration || 0,
    distanceMeters: route.distance || 0,
    steps: [],
  };
}

async function fetchMapboxRoute(token: string, origin: Point, stops: Point[]): Promise<RouteSnapshot> {
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

async function fetchGoogleRoute(apiKey: string, origin: Point, stops: Point[]): Promise<RouteSnapshot> {
  const destination = stops[stops.length - 1];
  const intermediates = stops.slice(0, -1);

  const response = await fetch("https://routes.googleapis.com/directions/v2:computeRoutes", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Goog-Api-Key": apiKey,
      "X-Goog-FieldMask":
        "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,routes.legs.steps.navigationInstruction,routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration",
    },
    body: JSON.stringify({
      origin: { location: { latLng: { latitude: origin.latitude, longitude: origin.longitude } } },
      destination: { location: { latLng: { latitude: destination.latitude, longitude: destination.longitude } } },
      intermediates: intermediates.map((p) => ({ location: { latLng: { latitude: p.latitude, longitude: p.longitude } } })),
      travelMode: "DRIVE",
      polylineQuality: "HIGH_QUALITY",
    }),
  });
  if (!response.ok) throw new Error("Unable to load route from Google Maps.");

  const payload = (await response.json()) as {
    routes?: Array<{
      duration?: string;
      distanceMeters?: number;
      polyline?: { encodedPolyline?: string };
      legs?: Array<{
        steps?: Array<{
          navigationInstruction?: { instructions?: string };
          distanceMeters?: number;
          staticDuration?: string;
        }>;
      }>;
    }>;
  };
  const route = payload.routes?.[0];
  const encoded = route?.polyline?.encodedPolyline;
  if (!encoded) throw new Error("Google Maps did not return a route.");

  const steps: RouteStep[] = [];
  (route.legs || []).forEach((leg) => {
    (leg.steps || []).forEach((step) => {
      if (step.navigationInstruction?.instructions) {
        steps.push({
          instruction: step.navigationInstruction.instructions,
          distanceMeters: step.distanceMeters || 0,
          durationSeconds: parseGoogleDuration(step.staticDuration),
        });
      }
    });
  });

  return {
    geometry: { type: "LineString", coordinates: decodePolyline(encoded) },
    durationSeconds: parseGoogleDuration(route.duration),
    distanceMeters: route.distanceMeters || 0,
    steps,
  };
}

function parseGoogleDuration(duration?: string): number {
  if (!duration) return 0;
  return parseInt(duration.replace("s", ""), 10) || 0;
}

// Decodes a Google encoded polyline into [lng, lat] pairs (GeoJSON coordinate order).
function decodePolyline(encoded: string): Array<[number, number]> {
  let index = 0;
  let lat = 0;
  let lng = 0;
  const coordinates: Array<[number, number]> = [];

  while (index < encoded.length) {
    let shift = 0;
    let result = 0;
    let byte: number;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lat += result & 1 ? ~(result >> 1) : result >> 1;

    shift = 0;
    result = 0;
    do {
      byte = encoded.charCodeAt(index++) - 63;
      result |= (byte & 0x1f) << shift;
      shift += 5;
    } while (byte >= 0x20);
    lng += result & 1 ? ~(result >> 1) : result >> 1;

    coordinates.push([ lng / 1e5, lat / 1e5 ]);
  }

  return coordinates;
}

function haversineMeters(a: Point, b: Point): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const radiusMeters = 6_371_000;
  const dLat = toRad(b.latitude - a.latitude);
  const dLng = toRad(b.longitude - a.longitude);
  const sinDLat = Math.sin(dLat / 2);
  const sinDLng = Math.sin(dLng / 2);
  const h =
    sinDLat * sinDLat + Math.cos(toRad(a.latitude)) * Math.cos(toRad(b.latitude)) * sinDLng * sinDLng;
  return radiusMeters * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

// Last-resort fallback when neither mapping provider is available: draws a
// straight line between stops and estimates duration from great-circle distance.
function straightLineRoute(origin: Point, stops: Point[]): RouteSnapshot {
  const points = [ origin, ...stops ];
  const coordinates: Array<[number, number]> = points.map((p) => [ p.longitude, p.latitude ]);

  let distanceMeters = 0;
  for (let i = 1; i < points.length; i++) {
    distanceMeters += haversineMeters(points[i - 1], points[i]);
  }
  const averageSpeedKmh = 38;
  const durationSeconds = (distanceMeters / 1000 / averageSpeedKmh) * 3600 + stops.length * 240;

  return {
    geometry: { type: "LineString", coordinates },
    durationSeconds,
    distanceMeters,
    steps: [],
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
  completed: "#059669",
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
    { label: "Completed", value: "completed" },
  ];
}

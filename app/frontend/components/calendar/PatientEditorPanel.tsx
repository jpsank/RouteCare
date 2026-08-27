import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import type { AvailabilityWindow, Patient } from "../../types";
import { useSwipeDown } from "./hooks/useSwipeDown";
import type { PatientSavePayload } from "./utils";

const DAY_OPTIONS: Array<[number, string]> = [
  [1, "Monday"],
  [2, "Tuesday"],
  [3, "Wednesday"],
  [4, "Thursday"],
  [5, "Friday"],
  [6, "Saturday"],
  [0, "Sunday"],
];

function minutesToTimeInput(minute: number): string {
  const h = Math.floor(minute / 60).toString().padStart(2, "0");
  const m = (minute % 60).toString().padStart(2, "0");
  return `${h}:${m}`;
}

function timeInputToMinutes(value: string): number {
  const [h, m] = value.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

// Local editing row: keeps a stable client-side key independent of server id
// so newly-added (unsaved) windows can be reordered/removed reliably.
type WindowRow = AvailabilityWindow & { _key: string };

let nextRowKey = 0;
function toRow(window: AvailabilityWindow): WindowRow {
  return { ...window, _key: `w${nextRowKey++}` };
}

type Props = {
  patient: Patient | null;
  onSave: (patientId: number, payload: PatientSavePayload) => Promise<boolean>;
  onClose: () => void;
};

const patientSchema = z
  .object({
    first_name: z.string().trim().min(1, "First name is required"),
    last_name: z.string().trim().min(1, "Last name is required"),
    phone: z
      .string()
      .trim()
      .min(7, "Phone number looks too short")
      .regex(/^[0-9+()\-\s.]+$/, "Phone contains invalid characters"),
    email: z.string().trim().email("Invalid email").or(z.literal("")),
    address_line1: z.string().trim().min(1, "Address is required"),
    address_line2: z.string().trim(),
    city: z.string().trim().min(1, "City is required"),
    state: z.string().trim().min(2, "State is required").max(20),
    postal_code: z.string().trim().min(3, "Postal code is required"),
    required_visits_per_week: z
      .number()
      .int()
      .min(1, "Must be at least 1")
      .max(7, "Cannot exceed 7"),
    visit_duration_minutes: z
      .number()
      .int()
      .min(15, "Must be at least 15 minutes")
      .max(480, "Cannot exceed 8 hours"),
    notes: z.string(),
    min_days_between_visits: z
      .number()
      .int()
      .min(0, "Cannot be negative")
      .max(6, "Cannot exceed 6"),
    max_days_between_visits: z
      .number()
      .int()
      .min(1, "Must be at least 1")
      .max(7, "Cannot exceed 7"),
    priority: z.number().int().min(0).max(10),
  })
  .refine((data) => data.max_days_between_visits >= data.min_days_between_visits, {
    message: "Max days must be ≥ min days",
    path: ["max_days_between_visits"],
  });

type PatientFormData = z.infer<typeof patientSchema>;

function patientDefaults(patient: Patient): PatientFormData {
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
    min_days_between_visits: patient.min_days_between_visits ?? 1,
    max_days_between_visits: patient.max_days_between_visits ?? 7,
    priority: patient.priority ?? 0,
  };
}

function toPayload(
  data: PatientFormData,
  patient: Patient,
  availabilityWindows: AvailabilityWindow[],
): PatientSavePayload {
  return {
    first_name: data.first_name.trim(),
    last_name: data.last_name.trim(),
    phone: data.phone.trim(),
    email: data.email.trim() || undefined,
    address_line1: data.address_line1.trim(),
    address_line2: data.address_line2.trim() || undefined,
    city: data.city.trim(),
    state: data.state.trim(),
    postal_code: data.postal_code.trim(),
    required_visits_per_week: data.required_visits_per_week,
    visit_duration_minutes: data.visit_duration_minutes,
    notes: data.notes.trim() || undefined,
    latitude: patient.latitude ?? undefined,
    longitude: patient.longitude ?? undefined,
    min_days_between_visits: data.min_days_between_visits,
    max_days_between_visits: data.max_days_between_visits,
    priority: data.priority,
    availability_windows: availabilityWindows,
  };
}

export function PatientEditorPanel({ patient, onSave, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
  const { handleRef, sheetStyle } = useSwipeDown(onClose);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<PatientFormData>({
    resolver: zodResolver(patientSchema),
    mode: "onBlur",
  });

  const [windowRows, setWindowRows] = useState<WindowRow[]>([]);

  useEffect(() => {
    if (patient) {
      reset(patientDefaults(patient));
      setWindowRows((patient.availability_windows ?? []).map(toRow));
    }
  }, [patient, reset]);

  useEffect(() => {
    if (!patient) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [patient, onClose]);

  if (!patient) return null;

  const windowErrors = windowRows.map((row) =>
    row.end_minute <= row.start_minute ? "End time must be after start time" : null,
  );
  const hasWindowErrors = windowErrors.some(Boolean);

  const addWindow = () => {
    setWindowRows((rows) => [
      ...rows,
      toRow({ day_of_week: 1, start_minute: 9 * 60, end_minute: 17 * 60, available: true }),
    ]);
  };

  const updateWindow = (key: string, patch: Partial<AvailabilityWindow>) => {
    setWindowRows((rows) => rows.map((row) => (row._key === key ? { ...row, ...patch } : row)));
  };

  const removeWindow = (key: string) => {
    setWindowRows((rows) => rows.filter((row) => row._key !== key));
  };

  const onSubmit = handleSubmit(async (data) => {
    if (hasWindowErrors) return;
    const windows = windowRows.map(({ _key, ...window }) => window);
    const ok = await onSave(patient.id, toPayload(data, patient, windows));
    if (ok) onClose();
  });

  const errorText = (msg?: string) =>
    msg ? <p className="text-[11px] text-red-600">{msg}</p> : null;

  return (
    <div
      ref={ref}
      className={isMobile
        ? "fixed inset-0 z-[1100] flex items-end animate-[fadeIn_0.15s_ease-out] bg-black/25"
        : "fixed inset-0 z-[1100] flex items-start justify-center px-4 pt-[10vh] animate-[fadeIn_0.15s_ease-out] bg-black/25 backdrop-blur-[2px]"}
      data-modal-overlay
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <form
        onSubmit={onSubmit}
        className={isMobile
          ? "w-full max-h-[85vh] overflow-y-auto rounded-t-2xl bg-white p-4 shadow-2xl animate-[slideUp_0.2s_ease-out]"
          : "w-full max-w-md max-h-[80vh] overflow-y-auto rounded-2xl bg-white p-5 shadow-2xl ring-1 ring-black/5 animate-[scaleIn_0.15s_ease-out]"}
        style={isMobile ? sheetStyle : undefined}
      >
        {isMobile && <div ref={handleRef} className="flex h-8 w-full cursor-grab items-center justify-center"><div className="h-1 w-10 rounded-full bg-gray-300" /></div>}

        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900">Edit Patient</h3>
          <button type="button" className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600" onClick={onClose}>
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>

        <div className="rc-form-grid">
          <div className="rc-field">
            <span className="rc-label">First Name</span>
            <input {...register("first_name")} aria-invalid={errors.first_name ? "true" : "false"} />
            {errorText(errors.first_name?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Last Name</span>
            <input {...register("last_name")} aria-invalid={errors.last_name ? "true" : "false"} />
            {errorText(errors.last_name?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Phone</span>
            <input {...register("phone")} aria-invalid={errors.phone ? "true" : "false"} />
            {errorText(errors.phone?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Email</span>
            <input type="email" {...register("email")} aria-invalid={errors.email ? "true" : "false"} />
            {errorText(errors.email?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Duration (min)</span>
            <input type="number" min={15} step={5} {...register("visit_duration_minutes", { valueAsNumber: true })} />
            {errorText(errors.visit_duration_minutes?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Visits / week</span>
            <input type="number" min={1} max={7} {...register("required_visits_per_week", { valueAsNumber: true })} />
            {errorText(errors.required_visits_per_week?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Address</span>
            <input {...register("address_line1")} aria-invalid={errors.address_line1 ? "true" : "false"} />
            {errorText(errors.address_line1?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Apt / Suite / Unit</span>
            <input {...register("address_line2")} placeholder="Apt 4B" />
          </div>
          <div className="rc-field">
            <span className="rc-label">City</span>
            <input {...register("city")} aria-invalid={errors.city ? "true" : "false"} />
            {errorText(errors.city?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">State</span>
            <input {...register("state")} aria-invalid={errors.state ? "true" : "false"} />
            {errorText(errors.state?.message)}
          </div>
          <div className="rc-field sm:col-span-2">
            <span className="rc-label">Postal Code</span>
            <input {...register("postal_code")} aria-invalid={errors.postal_code ? "true" : "false"} />
            {errorText(errors.postal_code?.message)}
          </div>

          <div className="sm:col-span-2 border-t border-gray-100 pt-2 mt-1">
            <span className="text-[11px] font-medium uppercase tracking-wide text-gray-400">Scheduling</span>
          </div>
          <div className="rc-field">
            <span className="rc-label">Priority</span>
            <select {...register("priority", { valueAsNumber: true })}>
              <option value={0}>Normal</option>
              <option value={5}>High</option>
              <option value={10}>Urgent</option>
            </select>
          </div>
          <div className="rc-field">
            <span className="rc-label">Min days between</span>
            <input type="number" min={0} max={6} {...register("min_days_between_visits", { valueAsNumber: true })} />
            {errorText(errors.min_days_between_visits?.message)}
          </div>
          <div className="rc-field">
            <span className="rc-label">Max days between</span>
            <input type="number" min={1} max={7} {...register("max_days_between_visits", { valueAsNumber: true })} />
            {errorText(errors.max_days_between_visits?.message)}
          </div>

          <div className="sm:col-span-2 border-t border-gray-100 pt-2 mt-1 flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wide text-gray-400">Availability Windows</span>
            <button type="button" className="btn-ghost btn-sm" onClick={addWindow}>
              + Add window
            </button>
          </div>
          <div className="sm:col-span-2 flex flex-col gap-2">
            {windowRows.length === 0 && (
              <p className="text-[12px] text-gray-400">
                No windows set — patient is available any time during working hours.
              </p>
            )}
            {windowRows.map((row, idx) => (
              <div key={row._key} className="flex flex-wrap items-center gap-2 rounded-md border border-gray-200 p-2">
                <select
                  className="text-sm"
                  value={row.day_of_week}
                  onChange={(e) => updateWindow(row._key, { day_of_week: Number(e.target.value) })}
                >
                  {DAY_OPTIONS.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <input
                  type="time"
                  className="text-sm"
                  value={minutesToTimeInput(row.start_minute)}
                  onChange={(e) => updateWindow(row._key, { start_minute: timeInputToMinutes(e.target.value) })}
                />
                <span className="text-xs text-gray-400">to</span>
                <input
                  type="time"
                  className="text-sm"
                  value={minutesToTimeInput(row.end_minute)}
                  onChange={(e) => updateWindow(row._key, { end_minute: timeInputToMinutes(e.target.value) })}
                />
                <button
                  type="button"
                  className={`rc-badge ${row.available ? "rc-badge-success" : "rc-badge-danger"} shadow-none border-0 cursor-pointer`}
                  title="Toggle between available and unavailable (blackout)"
                  onClick={() => updateWindow(row._key, { available: !row.available })}
                >
                  {row.available ? "Available" : "Unavailable"}
                </button>
                <button
                  type="button"
                  className="btn-ghost btn-sm ml-auto text-red-600"
                  onClick={() => removeWindow(row._key)}
                  aria-label="Remove window"
                >
                  Remove
                </button>
                {windowErrors[idx] && (
                  <p className="w-full text-[11px] text-red-600">{windowErrors[idx]}</p>
                )}
              </div>
            ))}
            <p className="text-[11px] text-gray-400">
              Available windows restrict visits to those times. Unavailable (blackout) windows are always
              excluded, even without an available window defined.
            </p>
          </div>

          <div className="sm:col-span-2 border-t border-gray-100 pt-2 mt-1">
            <span className="text-[11px] font-medium uppercase tracking-wide text-gray-400">Notes</span>
          </div>
          <div className="rc-field sm:col-span-2">
            <textarea
              className="w-full resize-y text-sm"
              rows={3}
              {...register("notes")}
              placeholder="Free-form notes about this patient..."
            />
          </div>
        </div>

        <button type="submit" className="btn-primary mt-3 w-full" disabled={isSubmitting || hasWindowErrors}>
          {isSubmitting ? "Saving..." : "Save Patient"}
        </button>
      </form>
    </div>
  );
}

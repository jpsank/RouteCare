import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { api } from "../../lib/api";
import type { Patient, Visit } from "../../types";
import { useSwipeDown } from "./hooks/useSwipeDown";
import { localInputToIso } from "./utils";

type Props = {
  patients: ReadonlyArray<Patient>;
  position: { x: number; y: number };
  startInput: string;
  onStartInputChange: (value: string) => void;
  onClose: () => void;
  onCalendarRefresh: () => Promise<void>;
};

const POPOVER_WIDTH = 380;
const POPOVER_HEIGHT = 500;

const STATUSES: Visit["status"][] = ["pending_patient_confirmation", "confirmed", "declined", "unscheduled"];

const schema = z
  .object({
    status: z.enum(STATUSES as [Visit["status"], ...Visit["status"][]]),
    selectedPatient: z.string(),
    full_name: z.string().trim(),
    phone: z.string().trim(),
    visit_duration_minutes: z.number().int().min(5).max(480),
    required_visits_per_week: z.number().int().min(1).max(7),
    address_line1: z.string().trim(),
    city: z.string().trim(),
    state: z.string().trim(),
    postal_code: z.string().trim(),
  })
  .superRefine((data, ctx) => {
    if (!data.selectedPatient) {
      ctx.addIssue({ code: "custom", path: ["selectedPatient"], message: "Choose or create a patient" });
      return;
    }
    if (data.selectedPatient === "new") {
      for (const field of ["full_name", "phone", "address_line1", "city", "state", "postal_code"] as const) {
        if (!data[field]) {
          ctx.addIssue({ code: "custom", path: [field], message: "Required" });
        }
      }
    }
  });
type FormData = z.infer<typeof schema>;

function clampPosition(x: number, y: number) {
  return {
    x: Math.max(12, Math.min(x, window.innerWidth - POPOVER_WIDTH)),
    y: Math.max(12, Math.min(y, window.innerHeight - POPOVER_HEIGHT)),
  };
}

export function AddEventPopover({ patients, position, startInput, onStartInputChange, onClose, onCalendarRefresh }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const [pos, setPos] = useState(position);
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
  const { handleRef: swipeRef, sheetStyle } = useSwipeDown(onClose);

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      status: "pending_patient_confirmation",
      selectedPatient: "",
      full_name: "",
      phone: "",
      visit_duration_minutes: 60,
      required_visits_per_week: 2,
      address_line1: "",
      city: "",
      state: "",
      postal_code: "",
    },
  });

  const selectedPatient = watch("selectedPatient");

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!ref.current || ref.current.contains(event.target as Node)) return;
      onClose();
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [onClose]);

  const onSubmit = handleSubmit(async (data) => {
    if (!startInput) return;
    let patientId: number | null = null;
    if (data.selectedPatient === "new") {
      const created = await api.createPatient({
        full_name: data.full_name,
        phone: data.phone,
        address_line1: data.address_line1,
        city: data.city,
        state: data.state,
        postal_code: data.postal_code,
        required_visits_per_week: data.required_visits_per_week,
        visit_duration_minutes: data.visit_duration_minutes,
      });
      patientId = created.patient.id;
    } else if (data.selectedPatient) {
      patientId = Number(data.selectedPatient);
    }
    if (!patientId) return;

    await api.createVisit({
      patient_id: patientId,
      starts_at: localInputToIso(startInput),
      status: data.status,
    });
    await onCalendarRefresh();
    onClose();
  });

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    dragRef.current = { offsetX: event.clientX - pos.x, offsetY: event.clientY - pos.y };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  };
  const moveDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    setPos(clampPosition(event.clientX - dragRef.current.offsetX, event.clientY - dragRef.current.offsetY));
  };
  const endDrag = () => { dragRef.current = null; };

  const errorText = (msg?: string) =>
    msg ? <p className="text-[11px] text-red-600">{msg}</p> : null;

  return (
    <div
      ref={ref}
      className={isMobile ? "fixed inset-x-0 bottom-0 z-[1000] p-3" : "rc-popover"}
      style={isMobile ? undefined : { left: `${pos.x}px`, top: `${pos.y}px` }}
      role="dialog"
      aria-modal="false"
    >
      <form onSubmit={onSubmit} className={`${isMobile ? "rounded-t-2xl" : ""} rc-popover-card space-y-3`} style={isMobile ? sheetStyle : undefined}>
        {isMobile && <div ref={swipeRef} className="flex h-8 w-full cursor-grab items-center justify-center"><div className="h-1 w-10 rounded-full bg-gray-300" /></div>}
        <div
          className={`flex items-center justify-between select-none ${isMobile ? "" : "cursor-move"}`}
          onPointerDown={isMobile ? undefined : startDrag}
          onPointerMove={isMobile ? undefined : moveDrag}
          onPointerUp={isMobile ? undefined : endDrag}
          onPointerCancel={isMobile ? undefined : endDrag}
        >
          <h3 className="text-sm font-semibold text-gray-900">Add Event</h3>
          <button type="button" className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600" onPointerDown={(e) => e.stopPropagation()} onClick={onClose} aria-label="Close">
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>

        <div className="rc-field">
          <span className="rc-label">Start time</span>
          <input type="datetime-local" value={startInput} onChange={(e) => onStartInputChange(e.target.value)} />
        </div>

        <div className="rc-field">
          <span className="rc-label">Status</span>
          <select {...register("status")}>
            <option value="pending_patient_confirmation">Pending Confirmation</option>
            <option value="confirmed">Confirmed</option>
            <option value="declined">Declined</option>
            <option value="unscheduled">Unscheduled</option>
          </select>
        </div>

        <div className="rc-field">
          <span className="rc-label">Patient</span>
          <select {...register("selectedPatient")}>
            <option value="">Select patient...</option>
            {patients.map((p) => (
              <option key={p.id} value={String(p.id)}>{p.full_name}</option>
            ))}
            <option value="new">+ New patient</option>
          </select>
          {errorText(errors.selectedPatient?.message)}
        </div>

        {selectedPatient === "new" && (
          <div className="rc-form-grid rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="rc-field">
              <span className="rc-label">Full Name</span>
              <input {...register("full_name")} />
              {errorText(errors.full_name?.message)}
            </div>
            <div className="rc-field">
              <span className="rc-label">Phone</span>
              <input {...register("phone")} />
              {errorText(errors.phone?.message)}
            </div>
            <div className="rc-field">
              <span className="rc-label">Duration (min)</span>
              <input type="number" min={5} step={5} {...register("visit_duration_minutes", { valueAsNumber: true })} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Visits / week</span>
              <input type="number" min={1} max={7} {...register("required_visits_per_week", { valueAsNumber: true })} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Address</span>
              <input {...register("address_line1")} />
              {errorText(errors.address_line1?.message)}
            </div>
            <div className="rc-field">
              <span className="rc-label">City</span>
              <input {...register("city")} />
              {errorText(errors.city?.message)}
            </div>
            <div className="rc-field">
              <span className="rc-label">State</span>
              <input {...register("state")} />
              {errorText(errors.state?.message)}
            </div>
            <div className="rc-field sm:col-span-2">
              <span className="rc-label">Postal Code</span>
              <input {...register("postal_code")} />
              {errorText(errors.postal_code?.message)}
            </div>
          </div>
        )}

        <button type="submit" className="btn-primary w-full" disabled={isSubmitting}>
          {isSubmitting ? "Saving..." : "Create Event"}
        </button>
      </form>
    </div>
  );
}

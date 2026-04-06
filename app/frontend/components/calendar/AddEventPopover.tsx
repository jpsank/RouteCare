import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api } from "../../lib/api";
import type { Patient, Visit } from "../../types";
import { useSwipeDown } from "./hooks/useSwipeDown";
import { EMPTY_PATIENT_FORM, localInputToIso, patientPayloadFromForm, type PatientForm } from "./utils";

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
  const { handleProps: swipeProps, sheetStyle } = useSwipeDown(onClose);
  const [statusInput, setStatusInput] = useState<Visit["status"]>("pending_patient_confirmation");
  const [selectedPatient, setSelectedPatient] = useState("");
  const [newPatientForm, setNewPatientForm] = useState<PatientForm>(EMPTY_PATIENT_FORM);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!ref.current || ref.current.contains(event.target as Node)) return;
      onClose();
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [onClose]);

  const createEvent = async () => {
    if (!startInput) return;
    setSaving(true);
    try {
      let patientId: number | null = null;
      if (selectedPatient === "new") {
        const payload = patientPayloadFromForm(newPatientForm);
        const created = await api.createPatient(payload);
        patientId = created.patient.id;
      } else if (selectedPatient) {
        patientId = Number(selectedPatient);
      }
      if (!patientId) return;

      await api.createVisit({
        patient_id: patientId,
        starts_at: localInputToIso(startInput),
        status: statusInput,
      });
      await onCalendarRefresh();
      onClose();
    } finally {
      setSaving(false);
    }
  };

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

  return (
    <div
      ref={ref}
      className={isMobile ? "fixed inset-x-0 bottom-0 z-[1000] p-3" : "rc-popover"}
      style={isMobile ? undefined : { left: `${pos.x}px`, top: `${pos.y}px` }}
      role="dialog"
      aria-modal="false"
    >
      <div className={`${isMobile ? "rounded-t-2xl" : ""} rc-popover-card space-y-3`} style={isMobile ? sheetStyle : undefined}>
        <div
          className={`flex items-center justify-between select-none ${isMobile ? "" : "cursor-move"}`}
          onPointerDown={isMobile ? undefined : startDrag}
          onPointerMove={isMobile ? undefined : moveDrag}
          onPointerUp={isMobile ? undefined : endDrag}
          onPointerCancel={isMobile ? undefined : endDrag}
        >
          <div>
            {isMobile && <div className="mx-auto mb-2 flex h-5 w-12 cursor-grab items-center justify-center" {...swipeProps}><div className="h-1 w-8 rounded-full bg-gray-300" /></div>}
            <h3 className="text-sm font-semibold text-gray-900">Add Event</h3>
          </div>
          <button className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600" onPointerDown={(e) => e.stopPropagation()} onClick={onClose} aria-label="Close">
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>

        <div className="rc-field">
          <span className="rc-label">Start time</span>
          <input type="datetime-local" value={startInput} onChange={(e) => onStartInputChange(e.target.value)} />
        </div>

        <div className="rc-field">
          <span className="rc-label">Status</span>
          <select value={statusInput} onChange={(e) => setStatusInput(e.target.value as Visit["status"])}>
            <option value="pending_patient_confirmation">Pending Confirmation</option>
            <option value="confirmed">Confirmed</option>
            <option value="declined">Declined</option>
            <option value="unscheduled">Unscheduled</option>
          </select>
        </div>

        <div className="rc-field">
          <span className="rc-label">Patient</span>
          <select value={selectedPatient} onChange={(e) => setSelectedPatient(e.target.value)}>
            <option value="">Select patient...</option>
            {patients.map((p) => (
              <option key={p.id} value={String(p.id)}>{p.full_name}</option>
            ))}
            <option value="new">+ New patient</option>
          </select>
        </div>

        {selectedPatient === "new" && (
          <div className="rc-form-grid rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="rc-field">
              <span className="rc-label">Full Name</span>
              <input value={newPatientForm.full_name} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, full_name: e.target.value }))} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Phone</span>
              <input value={newPatientForm.phone} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, phone: e.target.value }))} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Duration (min)</span>
              <input type="number" min={15} step={15} value={newPatientForm.visit_duration_minutes} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, visit_duration_minutes: Number(e.target.value) || 60 }))} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Visits / week</span>
              <input type="number" min={1} max={7} value={newPatientForm.required_visits_per_week} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, required_visits_per_week: Number(e.target.value) || 1 }))} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Address</span>
              <input value={newPatientForm.address_line1} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, address_line1: e.target.value }))} />
            </div>
            <div className="rc-field">
              <span className="rc-label">City</span>
              <input value={newPatientForm.city} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, city: e.target.value }))} />
            </div>
            <div className="rc-field">
              <span className="rc-label">State</span>
              <input value={newPatientForm.state} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, state: e.target.value }))} />
            </div>
            <div className="rc-field sm:col-span-2">
              <span className="rc-label">Postal Code</span>
              <input value={newPatientForm.postal_code} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, postal_code: e.target.value }))} />
            </div>
          </div>
        )}

        <button className="btn-primary w-full" onClick={createEvent} disabled={saving}>
          {saving ? "Saving..." : "Create Event"}
        </button>
      </div>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { Visit } from "../../types";
import { patientColor, statusOptions, type PatientForm } from "./utils";

type Props = {
  editorMode: "none" | "add" | "edit";
  selectedVisit: Visit | null;
  visitStartInput: string;
  setVisitStartInput: (value: string) => void;
  visitStatusInput: Visit["status"];
  setVisitStatusInput: (value: Visit["status"]) => void;
  savingVisit: boolean;
  saveVisit: () => Promise<void>;
  patientForm: PatientForm;
  setPatientForm: Dispatch<SetStateAction<PatientForm>>;
  savingPatient: boolean;
  savePatient: () => Promise<void>;
  closeEditor: () => void;
  position?: { x: number; y: number } | null;
};

export function EventEditorPanel({
  editorMode,
  selectedVisit,
  visitStartInput,
  setVisitStartInput,
  visitStatusInput,
  setVisitStatusInput,
  savingVisit,
  saveVisit,
  patientForm,
  setPatientForm,
  savingPatient,
  savePatient,
  closeEditor,
  position,
}: Props) {
  const [showPatientFields, setShowPatientFields] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const isOpen = editorMode !== "none" || Boolean(selectedVisit);

  useEffect(() => {
    setShowPatientFields(false);
  }, [selectedVisit?.id, editorMode]);

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!panelRef.current) return;
      if (panelRef.current.contains(event.target as Node)) return;
      closeEditor();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [isOpen, closeEditor]);

  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  const panelStyle = useMemo(() => {
    if (isMobile) return {};
    const x = Math.max(12, Math.min((position?.x ?? window.innerWidth / 2) + 8, window.innerWidth - 380));
    const y = Math.max(12, Math.min((position?.y ?? 120) + 8, window.innerHeight - 500));
    return { left: `${x}px`, top: `${y}px` };
  }, [position, isMobile]);

  if (!isOpen) return null;

  return (
    <div ref={panelRef} className={isMobile ? "fixed inset-x-0 bottom-0 z-[1000] p-3" : "rc-popover"} style={panelStyle} role="dialog" aria-modal="false">
      <div className={`${isMobile ? "rounded-t-2xl" : ""} rc-popover-card space-y-3`}>
        {isMobile && <div className="mx-auto mb-2 h-1 w-8 rounded-full bg-gray-300" />}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {selectedVisit && (
              <span
                className="inline-block h-3 w-3 rounded-full flex-none"
                style={{ backgroundColor: patientColor(selectedVisit.patient_id).accent }}
              />
            )}
            <h3 className="text-sm font-semibold text-gray-900">
              {editorMode === "add" ? "Add Patient" : selectedVisit?.patient_name ?? "Edit Event"}
            </h3>
          </div>
          <button className="btn-ghost btn-xs" onClick={closeEditor} aria-label="Close">&times;</button>
        </div>

        {selectedVisit && (
          <>
            {(selectedVisit.drive_from_previous_minutes || 0) > 0 && (
              <div>
                <span className="rc-transit">
                  🚗 {selectedVisit.position_in_day === 0 ? "From home" : "Transit"}:{" "}
                  {selectedVisit.drive_from_previous_minutes || 0} min
                </span>
              </div>
            )}
            <div className="rc-field">
              <span className="rc-label">Visit Start</span>
              <input type="datetime-local" value={visitStartInput} onChange={(e) => setVisitStartInput(e.target.value)} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Visit Status</span>
              <select value={visitStatusInput} onChange={(e) => setVisitStatusInput(e.target.value as Visit["status"])}>
                {statusOptions().map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <button className="btn-primary w-full" onClick={saveVisit} disabled={savingVisit}>
              {savingVisit ? "Saving..." : "Save Visit"}
            </button>
          </>
        )}

        <button className="btn-link text-xs" onClick={() => setShowPatientFields((prev) => !prev)}>
          {showPatientFields ? "Hide patient details" : "Edit patient details"}
        </button>

        {showPatientFields && (
          <>
            <div className="rc-form-grid rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="rc-field">
                <span className="rc-label">Full Name</span>
                <input value={patientForm.full_name} onChange={(e) => setPatientForm((prev) => ({ ...prev, full_name: e.target.value }))} />
              </div>
              <div className="rc-field">
                <span className="rc-label">Phone</span>
                <input value={patientForm.phone} onChange={(e) => setPatientForm((prev) => ({ ...prev, phone: e.target.value }))} />
              </div>
              <div className="rc-field">
                <span className="rc-label">Duration (min)</span>
                <input type="number" min={15} step={15} value={patientForm.visit_duration_minutes} onChange={(e) => setPatientForm((prev) => ({ ...prev, visit_duration_minutes: Number(e.target.value) || 60 }))} />
              </div>
              <div className="rc-field">
                <span className="rc-label">Visits / week</span>
                <input type="number" min={1} max={7} value={patientForm.required_visits_per_week} onChange={(e) => setPatientForm((prev) => ({ ...prev, required_visits_per_week: Number(e.target.value) || 1 }))} />
              </div>
              <div className="rc-field">
                <span className="rc-label">Email</span>
                <input type="email" value={patientForm.email} onChange={(e) => setPatientForm((prev) => ({ ...prev, email: e.target.value }))} />
              </div>
              <div className="rc-field sm:col-span-2">
                <span className="rc-label">Address</span>
                <input value={patientForm.address_line1} onChange={(e) => setPatientForm((prev) => ({ ...prev, address_line1: e.target.value }))} />
              </div>
              <div className="rc-field">
                <span className="rc-label">City</span>
                <input value={patientForm.city} onChange={(e) => setPatientForm((prev) => ({ ...prev, city: e.target.value }))} />
              </div>
              <div className="rc-field">
                <span className="rc-label">State</span>
                <input value={patientForm.state} onChange={(e) => setPatientForm((prev) => ({ ...prev, state: e.target.value }))} />
              </div>
              <div className="rc-field sm:col-span-2">
                <span className="rc-label">Postal Code</span>
                <input value={patientForm.postal_code} onChange={(e) => setPatientForm((prev) => ({ ...prev, postal_code: e.target.value }))} />
              </div>
            </div>
            <button className="btn-primary w-full" onClick={savePatient} disabled={savingPatient}>
              {savingPatient ? "Saving..." : editorMode === "add" ? "Add Patient" : "Save Patient"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

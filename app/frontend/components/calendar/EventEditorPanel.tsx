import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { Visit } from "../../types";
import { statusOptions, type PatientForm } from "./utils";

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

  const panelStyle = useMemo(() => {
    const x = Math.max(16, Math.min((position?.x ?? window.innerWidth / 2) + 8, window.innerWidth - 420));
    const y = Math.max(16, Math.min((position?.y ?? 120) + 8, window.innerHeight - 580));
    return { left: `${x}px`, top: `${y}px` };
  }, [position]);

  if (!isOpen) return null;

  return (
    <div ref={panelRef} className="floating-popover" style={panelStyle} role="dialog" aria-modal="false">
        <div className="floating-popover-card border-slate-300">
          <div className="section-head">
            <h3 className="text-base font-bold text-slate-900">{editorMode === "add" ? "Add Patient" : "Edit Event"}</h3>
            <button className="btn-quiet" onClick={closeEditor}>Close</button>
          </div>
          {selectedVisit && (
            <>
              <div className="section-meta">
                <span className="transit-chip">
                  <span className="transit-icon" aria-hidden="true">
                    🚗
                  </span>
                  <span>
                    {selectedVisit.position_in_day === 0 ? "Drive from home" : "Drive from previous visit"}:{" "}
                    {selectedVisit.drive_from_previous_minutes || 0} min
                  </span>
                </span>
              </div>
              <label className="field">
                <span className="field-label">Visit Start</span>
                <input type="datetime-local" value={visitStartInput} onChange={(e) => setVisitStartInput(e.target.value)} />
              </label>
              <label className="field">
                <span className="field-label">Visit Status</span>
                <select value={visitStatusInput} onChange={(e) => setVisitStatusInput(e.target.value as Visit["status"])}>
                  {statusOptions().map((status) => (
                    <option key={status.value} value={status.value}>
                      {status.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="controls">
                <button className="btn-primary" onClick={saveVisit} disabled={savingVisit}>
                  {savingVisit ? "Saving Visit..." : "Save Visit"}
                </button>
              </div>
            </>
          )}

          <button className="link-btn mt-2" onClick={() => setShowPatientFields((prev) => !prev)}>
            {showPatientFields ? "Hide patient details" : "Edit patient details"}
          </button>

          {showPatientFields && (
            <>
              <div className="message-form mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
                <label className="field">
                  <span className="field-label">Full Name</span>
                  <input value={patientForm.full_name} onChange={(e) => setPatientForm((prev) => ({ ...prev, full_name: e.target.value }))} required />
                </label>
                <label className="field">
                  <span className="field-label">Phone</span>
                  <input value={patientForm.phone} onChange={(e) => setPatientForm((prev) => ({ ...prev, phone: e.target.value }))} required />
                </label>
              <label className="field">
                <span className="field-label">Visit Duration (min)</span>
                <input
                  type="number"
                  min={15}
                  step={15}
                  value={patientForm.visit_duration_minutes}
                  onChange={(e) => setPatientForm((prev) => ({ ...prev, visit_duration_minutes: Number(e.target.value) || 60 }))}
                  required
                />
              </label>
                <label className="field">
                  <span className="field-label">Email</span>
                  <input type="email" value={patientForm.email} onChange={(e) => setPatientForm((prev) => ({ ...prev, email: e.target.value }))} />
                </label>
                <label className="field">
                  <span className="field-label">Address 1</span>
                  <input
                    value={patientForm.address_line1}
                    onChange={(e) => setPatientForm((prev) => ({ ...prev, address_line1: e.target.value }))}
                    required
                  />
                </label>
                <label className="field">
                  <span className="field-label">City</span>
                  <input value={patientForm.city} onChange={(e) => setPatientForm((prev) => ({ ...prev, city: e.target.value }))} required />
                </label>
                <label className="field">
                  <span className="field-label">State</span>
                  <input value={patientForm.state} onChange={(e) => setPatientForm((prev) => ({ ...prev, state: e.target.value }))} required />
                </label>
                <label className="field">
                  <span className="field-label">Postal Code</span>
                  <input
                    value={patientForm.postal_code}
                    onChange={(e) => setPatientForm((prev) => ({ ...prev, postal_code: e.target.value }))}
                    required
                  />
                </label>
              </div>
              <div className="controls">
                <button className="btn-primary" onClick={savePatient} disabled={savingPatient}>
                  {savingPatient ? "Saving Patient..." : editorMode === "add" ? "Add Patient" : "Save Patient"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
  );
}

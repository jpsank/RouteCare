import { useEffect, useRef, useState } from "react";
import type { Patient } from "../../types";
import type { PatientForm } from "./utils";

type Props = {
  patient: Patient | null;
  onSave: (patientId: number, form: PatientForm) => Promise<void>;
  onClose: () => void;
};

function formFromPatient(patient: Patient): PatientForm {
  return {
    full_name: patient.full_name,
    phone: patient.phone,
    email: patient.email ?? "",
    address_line1: patient.address_line1 ?? "",
    city: patient.city ?? "",
    state: patient.state ?? "",
    postal_code: patient.postal_code ?? "",
    required_visits_per_week: patient.required_visits_per_week,
    visit_duration_minutes: patient.visit_duration_minutes,
  };
}

export function PatientEditorPanel({ patient, onSave, onClose }: Props) {
  const [form, setForm] = useState<PatientForm | null>(null);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;

  useEffect(() => {
    if (patient) setForm(formFromPatient(patient));
  }, [patient?.id]);

  useEffect(() => {
    if (!patient) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [patient, onClose]);

  if (!patient || !form) return null;

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(patient.id, form);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  const set = (key: keyof PatientForm, value: string | number) =>
    setForm((prev) => prev ? { ...prev, [key]: value } : prev);

  return (
    <div
      ref={ref}
      className={isMobile
        ? "fixed inset-0 z-[1100] h-screen w-screen flex items-end animate-[fadeIn_0.15s_ease-out] bg-black/25"
        : "fixed inset-0 z-[1100] h-screen w-screen flex items-start justify-center px-4 pt-[10vh] animate-[fadeIn_0.15s_ease-out] bg-black/25 backdrop-blur-[2px]"}
      data-modal-overlay
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className={isMobile
        ? "w-full max-h-[85vh] overflow-y-auto rounded-t-2xl bg-white p-4 shadow-2xl animate-[slideUp_0.2s_ease-out]"
        : "w-full max-w-md max-h-[80vh] overflow-y-auto rounded-2xl bg-white p-5 shadow-2xl ring-1 ring-black/5 animate-[scaleIn_0.15s_ease-out]"}>
        {isMobile && <div className="mx-auto mb-3 h-1 w-8 rounded-full bg-gray-300" />}

        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900">Edit Patient</h3>
          <button className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600" onClick={onClose}>
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>

        <div className="rc-form-grid">
          <div className="rc-field">
            <span className="rc-label">Full Name</span>
            <input value={form.full_name} onChange={(e) => set("full_name", e.target.value)} />
          </div>
          <div className="rc-field">
            <span className="rc-label">Phone</span>
            <input value={form.phone} onChange={(e) => set("phone", e.target.value)} />
          </div>
          <div className="rc-field">
            <span className="rc-label">Email</span>
            <input type="email" value={form.email} onChange={(e) => set("email", e.target.value)} />
          </div>
          <div className="rc-field">
            <span className="rc-label">Duration (min)</span>
            <input type="number" min={15} step={15} value={form.visit_duration_minutes} onChange={(e) => set("visit_duration_minutes", Number(e.target.value) || 60)} />
          </div>
          <div className="rc-field">
            <span className="rc-label">Visits / week</span>
            <input type="number" min={1} max={7} value={form.required_visits_per_week} onChange={(e) => set("required_visits_per_week", Number(e.target.value) || 1)} />
          </div>
          <div className="rc-field">
            <span className="rc-label">Address</span>
            <input value={form.address_line1} onChange={(e) => set("address_line1", e.target.value)} />
          </div>
          <div className="rc-field">
            <span className="rc-label">City</span>
            <input value={form.city} onChange={(e) => set("city", e.target.value)} />
          </div>
          <div className="rc-field">
            <span className="rc-label">State</span>
            <input value={form.state} onChange={(e) => set("state", e.target.value)} />
          </div>
          <div className="rc-field sm:col-span-2">
            <span className="rc-label">Postal Code</span>
            <input value={form.postal_code} onChange={(e) => set("postal_code", e.target.value)} />
          </div>
        </div>

        <button className="btn-primary mt-3 w-full" onClick={handleSave} disabled={saving}>
          {saving ? "Saving..." : "Save Patient"}
        </button>
      </div>
    </div>
  );
}

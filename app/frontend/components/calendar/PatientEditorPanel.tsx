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
      className={isMobile ? "fixed inset-x-0 bottom-0 z-[1100] p-3" : "fixed inset-0 z-[1100] flex items-start justify-center bg-black/20 pt-16"}
      onClick={isMobile ? undefined : (e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className={`${isMobile ? "rounded-t-2xl" : "w-full max-w-md rounded-xl"} border border-gray-200 bg-white p-4 shadow-xl`}>
        {isMobile && <div className="mx-auto mb-3 h-1 w-8 rounded-full bg-gray-300" />}

        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Edit Patient</h3>
          <button className="rounded-md border-0 bg-transparent p-1 text-gray-400 shadow-none hover:text-gray-600" onClick={onClose}>
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
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

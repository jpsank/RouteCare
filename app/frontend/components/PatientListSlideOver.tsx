import { useEffect, useMemo, useRef, useState } from "react";
import type { Patient, Visit } from "../types";
import { fmt, patientColor } from "./calendar/utils";

type Props = {
  patients: ReadonlyArray<Patient>;
  visits: ReadonlyArray<Visit>;
  isOpen: boolean;
  onClose: () => void;
  onEditPatient: (patientId: number) => void;
};

function nextVisitLabel(visit: Visit | undefined): string {
  if (!visit) return "No upcoming visit";
  const date = new Date(visit.starts_at);
  const dateLabel = date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return `${dateLabel} · ${fmt(visit.starts_at)}`;
}

export function PatientListSlideOver({ patients, visits, isOpen, onClose, onEditPatient }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) setSearch("");
  }, [isOpen]);

  const nextVisitByPatient = useMemo(() => {
    const now = Date.now();
    const map = new Map<number, Visit>();
    const upcoming = visits
      .filter((v) => v.status !== "declined" && new Date(v.starts_at).getTime() >= now)
      .sort((a, b) => new Date(a.starts_at).getTime() - new Date(b.starts_at).getTime());
    for (const v of upcoming) {
      if (!map.has(v.patient_id)) map.set(v.patient_id, v);
    }
    return map;
  }, [visits]);

  const filteredPatients = useMemo(() => {
    const activePatients = patients.filter((p) => p.active);
    const term = search.trim().toLowerCase();
    const matched = term
      ? activePatients.filter((p) => p.full_name.toLowerCase().includes(term))
      : activePatients;
    return [...matched].sort((a, b) => a.full_name.localeCompare(b.full_name));
  }, [patients, search]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[900] flex justify-end animate-[fadeIn_0.15s_ease-out] bg-black/25 backdrop-blur-[2px]"
      data-modal-overlay
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div ref={ref} className="flex h-full w-full max-w-sm flex-col bg-white shadow-2xl ring-1 ring-black/5 animate-[slideInRight_0.2s_ease-out]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3.5">
          <h3 className="text-base font-semibold text-gray-900">Patients</h3>
          <button
            className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600"
            onClick={onClose}
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="border-b border-gray-100 px-4 py-2.5">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search patients..."
            autoFocus
          />
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <h4 className="mb-2 text-[10px] font-bold uppercase tracking-wider text-gray-400">
            {filteredPatients.length} patient{filteredPatients.length !== 1 ? "s" : ""}
          </h4>
          {filteredPatients.length === 0 && (
            <p className="py-4 text-center text-xs text-gray-400">
              {patients.length === 0 ? "No patients yet" : "No patients match your search"}
            </p>
          )}
          <div className="space-y-1">
            {filteredPatients.map((p) => {
              const pc = patientColor(p.id);
              const nextVisit = nextVisitByPatient.get(p.id);
              return (
                <div
                  key={p.id}
                  className="flex items-center gap-2.5 rounded-lg border border-gray-100 px-2.5 py-2 cursor-pointer hover:bg-gray-50"
                  onClick={() => onEditPatient(p.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === "Enter") onEditPatient(p.id); }}
                >
                  <span className="inline-block h-2.5 w-2.5 flex-none rounded-full" style={{ backgroundColor: pc.accent }} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-gray-900">{p.full_name}</div>
                    <div className="truncate text-[11px] text-gray-400">
                      {nextVisitLabel(nextVisit)}
                      <span className="mx-1">&middot;</span>
                      {p.required_visits_per_week}x/wk
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

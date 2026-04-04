import { useEffect, useMemo, useState } from "react";
import { api } from "../../../lib/api";
import type { Patient, Visit } from "../../../types";
import { EMPTY_PATIENT_FORM, localInputToIso, patientFormFromPatient, patientPayloadFromForm, toLocalInputValue, type PatientForm, type PatientSavePayload } from "../utils";

type Args = {
  visits: Visit[];
  patients: ReadonlyArray<Patient>;
  onCreatePatient: (patient: PatientSavePayload) => Promise<void>;
  onUpdatePatient: (patientId: number, patient: PatientSavePayload) => Promise<void>;
  onCalendarRefresh: () => Promise<void>;
};

export function useEventEditor({ visits, patients, onCreatePatient, onUpdatePatient, onCalendarRefresh }: Args) {
  const [selectedVisitId, setSelectedVisitId] = useState<number | null>(null);
  const [visitStartInput, setVisitStartInput] = useState("");
  const [visitStatusInput, setVisitStatusInput] = useState<Visit["status"]>("pending_patient_confirmation");
  const [savingVisit, setSavingVisit] = useState(false);

  const [editorMode, setEditorMode] = useState<"none" | "add" | "edit">("none");
  const [patientForm, setPatientForm] = useState<PatientForm>(EMPTY_PATIENT_FORM);
  const [savingPatient, setSavingPatient] = useState(false);

  const selectedVisit = useMemo(() => visits.find((visit) => visit.id === selectedVisitId) || null, [visits, selectedVisitId]);
  const selectedPatient = useMemo(
    () => (selectedVisit ? patients.find((patient) => patient.id === selectedVisit.patient_id) || null : null),
    [patients, selectedVisit],
  );

  useEffect(() => {
    if (!selectedVisit) return;
    setVisitStartInput(toLocalInputValue(selectedVisit.starts_at));
    setVisitStatusInput(selectedVisit.status);
    if (selectedPatient) {
      setPatientForm(patientFormFromPatient(selectedPatient));
    }
  }, [selectedVisit, selectedPatient]);

  const openCreatePatient = () => {
    setEditorMode("add");
    setSelectedVisitId(null);
    setPatientForm(EMPTY_PATIENT_FORM);
  };

  const savePatient = async () => {
    setSavingPatient(true);
    try {
      const payload = patientPayloadFromForm(patientForm);
      if (editorMode === "add") await onCreatePatient(payload);
      else if (editorMode === "edit" && selectedPatient) await onUpdatePatient(selectedPatient.id, payload);
      await onCalendarRefresh();
      setEditorMode("none");
    } finally {
      setSavingPatient(false);
    }
  };

  const saveVisit = async () => {
    if (!selectedVisit) return;
    setSavingVisit(true);
    try {
      if (visitStartInput && localInputToIso(visitStartInput) !== selectedVisit.starts_at) {
        await api.rescheduleVisit(selectedVisit.id, localInputToIso(visitStartInput));
      }
      if (visitStatusInput !== selectedVisit.status) {
        await api.updateVisit(selectedVisit.id, { status: visitStatusInput });
      }
      await onCalendarRefresh();
    } finally {
      setSavingVisit(false);
    }
  };

  return {
    selectedVisitId,
    setSelectedVisitId,
    selectedVisit,
    editorMode,
    setEditorMode,
    patientForm,
    setPatientForm,
    visitStartInput,
    setVisitStartInput,
    visitStatusInput,
    setVisitStatusInput,
    savingVisit,
    savingPatient,
    openCreatePatient,
    saveVisit,
    savePatient,
  };
}

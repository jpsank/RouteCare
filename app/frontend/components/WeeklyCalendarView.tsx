import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import type { CalendarBlock, CalendarConnection, ClinicianProfile, Patient, Schedule, Visit } from "../types";
import { CalendarConnectionsPanel } from "./calendar/CalendarConnectionsPanel";
import { EventEditorPanel } from "./calendar/EventEditorPanel";
import { RoutePanel } from "./calendar/RoutePanel";
import { ScheduleCalendar } from "./calendar/ScheduleCalendar";
import { useCalendarConnections } from "./calendar/hooks/useCalendarConnections";
import { useEventEditor } from "./calendar/hooks/useEventEditor";
import { useRouteMap } from "./calendar/hooks/useRouteMap";
import {
  asDateKey,
  EMPTY_PATIENT_FORM,
  localInputToIso,
  patientPayloadFromForm,
  toLocalInputValue,
  type PatientForm,
  type PatientSavePayload,
} from "./calendar/utils";

type Props = {
  schedule: Schedule | null;
  patients: ReadonlyArray<Patient>;
  calendarBlocks: CalendarBlock[];
  calendarConnections: CalendarConnection[];
  loading: boolean;
  onOptimize: (start?: { latitude: number; longitude: number }) => Promise<void>;
  onCreatePatient: (patient: PatientSavePayload) => Promise<void>;
  onUpdatePatient: (patientId: number, patient: PatientSavePayload) => Promise<void>;
  onSeedDemoPatients: () => Promise<void>;
  clinicianProfile: ClinicianProfile | null;
  onUpdateWorkingHours: (workdayStartMinute: number, workdayEndMinute: number) => Promise<void>;
  onUpdateWorkingDays: (workingDays: number[]) => Promise<void>;
  onSetHomeFromCurrentLocation: (latitude: number, longitude: number) => Promise<void>;
  onUpdateHomeLocation: (latitude: number, longitude: number) => Promise<void>;
  onCalendarRefresh: () => Promise<void>;
};

export function WeeklyCalendarView({
  schedule,
  patients,
  calendarBlocks,
  calendarConnections,
  loading,
  onOptimize,
  onCreatePatient,
  onUpdatePatient,
  onSeedDemoPatients,
  clinicianProfile,
  onUpdateWorkingHours,
  onUpdateWorkingDays,
  onSetHomeFromCurrentLocation,
  onUpdateHomeLocation,
  onCalendarRefresh,
}: Props) {
  const visits = schedule?.visits ?? [];

  const {
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
    saveVisit,
    savePatient,
  } = useEventEditor({
    visits,
    patients,
    onCreatePatient,
    onUpdatePatient,
    onCalendarRefresh,
  });

  const [selectedDate, setSelectedDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [savingHours, setSavingHours] = useState(false);
  const [savingWorkingDays, setSavingWorkingDays] = useState(false);
  const [savingHomeLocation, setSavingHomeLocation] = useState(false);
  const [homeLatitudeInput, setHomeLatitudeInput] = useState("");
  const [homeLongitudeInput, setHomeLongitudeInput] = useState("");
  const [savingHomeInput, setSavingHomeInput] = useState(false);
  const [addEventOpen, setAddEventOpen] = useState(false);
  const [addEventStartInput, setAddEventStartInput] = useState("");
  const [addEventStatusInput, setAddEventStatusInput] = useState<Visit["status"]>("pending_patient_confirmation");
  const [selectedPatientOption, setSelectedPatientOption] = useState<string>("");
  const [newPatientForm, setNewPatientForm] = useState<PatientForm>(EMPTY_PATIENT_FORM);
  const [savingNewEvent, setSavingNewEvent] = useState(false);
  const [addEventPosition, setAddEventPosition] = useState<{ x: number; y: number } | null>(null);
  const [editorPosition, setEditorPosition] = useState<{ x: number; y: number } | null>(null);
  const [pendingSlot, setPendingSlot] = useState<{ key: string; startIso: string } | null>(null);
  const addEventRef = useRef<HTMLDivElement | null>(null);
  const homeOrigin = useMemo(() => {
    if (clinicianProfile?.home_latitude == null || clinicianProfile?.home_longitude == null) return null;
    return {
      latitude: Number(clinicianProfile.home_latitude),
      longitude: Number(clinicianProfile.home_longitude),
    };
  }, [clinicianProfile?.home_latitude, clinicianProfile?.home_longitude]);

  useEffect(() => {
    setHomeLatitudeInput(clinicianProfile?.home_latitude != null ? String(clinicianProfile.home_latitude) : "");
    setHomeLongitudeInput(clinicianProfile?.home_longitude != null ? String(clinicianProfile.home_longitude) : "");
  }, [clinicianProfile?.home_latitude, clinicianProfile?.home_longitude]);

  useEffect(() => {
    if (!addEventOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!addEventRef.current) return;
      if (addEventRef.current.contains(event.target as Node)) return;
      setAddEventOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [addEventOpen]);


  const hourOptions = useMemo(
    () =>
      Array.from({ length: 25 }).map((_, index) => {
        const minuteValue = index * 60;
        if (index === 24) {
          return {
            minuteValue,
            label: "12:00 AM (next day)",
          };
        }
        const meridiem = index >= 12 ? "PM" : "AM";
        const hour12 = ((index + 11) % 12) + 1;
        return {
          minuteValue,
          label: `${hour12}:00 ${meridiem}`,
        };
      }),
    [],
  );

  const workdayStartMinute = clinicianProfile?.workday_start_minute ?? 8 * 60;
  const workdayEndMinute = clinicianProfile?.workday_end_minute ?? 18 * 60;
  const workingDays = clinicianProfile?.working_days?.length ? clinicianProfile.working_days : [1, 2, 3, 4, 5];

  const updateWorkdayRange = async (nextStart: number, nextEnd: number) => {
    if (nextEnd <= nextStart) return;
    setSavingHours(true);
    try {
      await onUpdateWorkingHours(nextStart, nextEnd);
    } finally {
      setSavingHours(false);
    }
  };

  const dayVisits = useMemo(
    () =>
      visits
        .filter((visit) => asDateKey(visit.starts_at) === selectedDate)
        .sort((a, b) => a.position_in_day - b.position_in_day),
    [selectedDate, visits],
  );

  const dayBlocks = useMemo(
    () => calendarBlocks.filter((block) => asDateKey(block.starts_at) === selectedDate),
    [calendarBlocks, selectedDate],
  );

  const {
    mapContainerRef,
    routeLoading,
    routeSnapshot,
    routeError,
    googleMapsUrl,
    appleMapsUrl,
  } = useRouteMap({ dayVisits, selectedDate, homeOrigin });

  const {
    connectingProvider,
    setConnectingProvider,
    externalCalendarId,
    setExternalCalendarId,
    appleIcsUrl,
    setAppleIcsUrl,
    googleCalendars,
    selectedGoogleCalendarId,
    setSelectedGoogleCalendarId,
    calendarConfigMessage,
    googleConnection,
    connectCalendar,
    syncConnection,
    pushToConnection,
    loadGoogleCalendars,
    saveGoogleCalendarSelection,
  } = useCalendarConnections({ calendarConnections, selectedDate, onCalendarRefresh });

  const calendarEvents = useMemo(
    () => [
      ...visits.map((visit) => ({
        id: `visit-${visit.id}`,
        title: visit.patient_name,
        start: visit.starts_at,
        end: visit.ends_at,
        className: `calendar-event visit-${visit.status}${selectedVisitId === visit.id ? " visit-selected" : ""}`,
      })),
      ...calendarBlocks.map((block) => ({
        id: `block-${block.id}`,
        title: block.title || "Blocked",
        start: block.starts_at,
        end: block.ends_at,
        display: "background" as const,
        className: "calendar-event-blocked",
      })),
      ...(pendingSlot
        ? [
            {
              id: "pending-slot",
              title: "Selected slot",
              start: pendingSlot.startIso,
              end: new Date(new Date(pendingSlot.startIso).getTime() + 30 * 60 * 1000).toISOString(),
              display: "background" as const,
              className: "calendar-slot-selected",
            },
          ]
        : []),
    ],
    [calendarBlocks, pendingSlot, selectedVisitId, visits],
  );

  const saveHomeFromCurrentLocation = async () => {
    const currentPosition = await new Promise<GeolocationPosition>((resolve, reject) =>
      navigator.geolocation.getCurrentPosition(resolve, reject),
    );
    setSavingHomeLocation(true);
    try {
      setHomeLatitudeInput(String(currentPosition.coords.latitude));
      setHomeLongitudeInput(String(currentPosition.coords.longitude));
      await onSetHomeFromCurrentLocation(currentPosition.coords.latitude, currentPosition.coords.longitude);
    } finally {
      setSavingHomeLocation(false);
    }
  };

  const saveHomeFromInputs = async () => {
    const latitude = Number(homeLatitudeInput);
    const longitude = Number(homeLongitudeInput);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
    setSavingHomeInput(true);
    try {
      await onUpdateHomeLocation(latitude, longitude);
    } finally {
      setSavingHomeInput(false);
    }
  };

  const toggleWorkingDay = async (wday: number) => {
    const next = workingDays.includes(wday) ? workingDays.filter((day) => day !== wday) : [...workingDays, wday].sort((a, b) => a - b);
    if (next.length === 0) return;
    setSavingWorkingDays(true);
    try {
      await onUpdateWorkingDays(next);
    } finally {
      setSavingWorkingDays(false);
    }
  };

  const showEditorPanel = editorMode !== "none";

  const openAddEventModal = (dateKey: string, startStr: string, pointer: { x: number; y: number }) => {
    setSelectedDate(dateKey);
    const hasTime = startStr.includes("T");
    const fallback = `${dateKey}T${String(Math.floor(workdayStartMinute / 60)).padStart(2, "0")}:${String(workdayStartMinute % 60).padStart(2, "0")}`;
    setAddEventStartInput(hasTime ? toLocalInputValue(startStr) : fallback);
    setAddEventStatusInput("pending_patient_confirmation");
    setSelectedPatientOption("");
    setNewPatientForm(EMPTY_PATIENT_FORM);
    setAddEventPosition(pointer);
    setAddEventOpen(true);
    setPendingSlot(null);
  };

  const createEventFromModal = async () => {
    if (!addEventStartInput) return;
    setSavingNewEvent(true);
    try {
      let patientId: number | null = null;
      if (selectedPatientOption === "new") {
        const payload = patientPayloadFromForm(newPatientForm);
        const created = await api.createPatient(payload);
        patientId = created.patient.id;
      } else if (selectedPatientOption) {
        patientId = Number(selectedPatientOption);
      }
      if (!patientId) return;

      await api.createVisit({
        patient_id: patientId,
        starts_at: localInputToIso(addEventStartInput),
        status: addEventStatusInput,
      });
      await onCalendarRefresh();
      setAddEventOpen(false);
      setPendingSlot(null);
    } finally {
      setSavingNewEvent(false);
    }
  };

  if (!schedule) {
    return (
      <section className="surface-card">
        <h2 className="text-xl font-bold tracking-tight text-slate-900">Calendar Planner</h2>
        <p className="section-subtitle">Generate your week first.</p>
        <div className="controls planner-toolbar">
          <button className="btn-primary" onClick={() => onOptimize()} disabled={loading}>
            {loading ? "Optimizing..." : "Generate Week"}
          </button>
          <button onClick={saveHomeFromCurrentLocation} disabled={loading || savingHomeLocation}>
            {savingHomeLocation ? "Saving Home..." : "Set Home to Current Location"}
          </button>
          <button className="btn-quiet" onClick={onSeedDemoPatients} disabled={loading}>
            Replace with Demo Patients
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="surface-card">
      <div className="planner-header">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-slate-900">Day Planner</h2>
          <p className="mt-1 text-sm text-slate-600">Manage clinician availability, routing, and visit edits in one place.</p>
        </div>
        <div className="controls planner-toolbar">
          <button className="btn-primary" onClick={() => onOptimize()} disabled={loading}>
            {loading ? "Optimizing..." : "Re-optimize"}
          </button>
          <button onClick={saveHomeFromCurrentLocation} disabled={loading || savingHomeLocation}>
            {savingHomeLocation ? "Saving Home..." : "Set Home to Current Location"}
          </button>
          <label className="field-label home-coordinate-group">
            Home lat
            <input
              className="home-coordinate-input"
              value={homeLatitudeInput}
              onChange={(event) => setHomeLatitudeInput(event.target.value)}
              placeholder="36.18"
            />
          </label>
          <label className="field-label home-coordinate-group">
            Home lng
            <input
              className="home-coordinate-input"
              value={homeLongitudeInput}
              onChange={(event) => setHomeLongitudeInput(event.target.value)}
              placeholder="-94.13"
            />
          </label>
          <button className="btn-quiet" onClick={saveHomeFromInputs} disabled={loading || savingHomeInput}>
            {savingHomeInput ? "Saving Home..." : "Save Home"}
          </button>
          <button className="btn-quiet" onClick={onSeedDemoPatients} disabled={loading}>
            Replace with Demo Patients
          </button>
        </div>
      </div>

      <div className="day-toggle-group">
        {[
          [1, "Mon"],
          [2, "Tue"],
          [3, "Wed"],
          [4, "Thu"],
          [5, "Fri"],
          [6, "Sat"],
          [0, "Sun"],
        ].map(([wday, label]) => (
          <button
            key={`working-day-${wday}`}
            type="button"
            className={`day-toggle-btn ${workingDays.includes(Number(wday)) ? "active" : ""}`}
            onClick={() => toggleWorkingDay(Number(wday))}
            disabled={loading || savingWorkingDays}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="controls rounded-xl border border-slate-200 bg-slate-50 p-3">
        <label className="field-label planner-workday-setting">
          Day starts
          <select
            value={workdayStartMinute}
            onChange={(event) => updateWorkdayRange(Number(event.target.value), workdayEndMinute)}
            disabled={loading || savingHours}
          >
            {hourOptions
              .filter((option) => option.minuteValue < workdayEndMinute)
              .map((option) => (
              <option key={`start-${option.minuteValue}`} value={option.minuteValue}>
                {option.label}
              </option>
              ))}
          </select>
        </label>
        <label className="field-label planner-workday-setting">
          Day ends
          <select
            value={workdayEndMinute}
            onChange={(event) => updateWorkdayRange(workdayStartMinute, Number(event.target.value))}
            disabled={loading || savingHours}
          >
            {hourOptions
              .filter((option) => option.minuteValue > workdayStartMinute)
              .map((option) => (
              <option key={`end-${option.minuteValue}`} value={option.minuteValue}>
                {option.label}
              </option>
              ))}
          </select>
        </label>
      </div>

      <CalendarConnectionsPanel
        connectingProvider={connectingProvider}
        setConnectingProvider={setConnectingProvider}
        externalCalendarId={externalCalendarId}
        setExternalCalendarId={setExternalCalendarId}
        appleIcsUrl={appleIcsUrl}
        setAppleIcsUrl={setAppleIcsUrl}
        googleConnection={googleConnection}
        googleCalendars={googleCalendars}
        selectedGoogleCalendarId={selectedGoogleCalendarId}
        setSelectedGoogleCalendarId={setSelectedGoogleCalendarId}
        calendarConnections={calendarConnections}
        calendarConfigMessage={calendarConfigMessage}
        connectCalendar={connectCalendar}
        loadGoogleCalendars={loadGoogleCalendars}
        saveGoogleCalendarSelection={saveGoogleCalendarSelection}
        syncConnection={syncConnection}
        pushToConnection={pushToConnection}
        calendarFeedUrl={api.calendarFeedUrl(schedule.week_start_on)}
      />

      <div className="calendar-layout">
        <div className="rounded-2xl border border-slate-200 bg-white p-3 shadow-soft">
          <ScheduleCalendar
            events={calendarEvents}
            workdayStartMinute={workdayStartMinute}
            workdayEndMinute={workdayEndMinute}
            onDateClick={(dateKey, startStr, pointer) => {
              const slotIso = new Date(startStr).toISOString();
              if (pendingSlot?.key === slotIso) {
                openAddEventModal(dateKey, startStr, pointer);
                return;
              }
              setPendingSlot({ key: slotIso, startIso: slotIso });
              setSelectedVisitId(null);
              setEditorMode("none");
              setEditorPosition(null);
            }}
            onEventClick={(eventId, startStr, pointer) => {
              setSelectedDate(asDateKey(startStr));
              setAddEventOpen(false);
              setPendingSlot(null);
              const match = eventId.match(/^visit-(\d+)$/);
              if (!match) return;
              const visitId = Number(match[1]);
              if (selectedVisitId === visitId && editorMode === "none") {
                setEditorPosition(pointer);
                setEditorMode("edit");
              } else {
                setSelectedVisitId(visitId);
                setEditorMode("none");
                setEditorPosition(null);
              }
            }}
          />
        </div>

        <div>
          <RoutePanel
            selectedDate={selectedDate}
            dayVisits={dayVisits}
            dayBlocks={dayBlocks}
            selectedVisitId={selectedVisitId}
            setSelectedVisitId={setSelectedVisitId}
            googleMapsUrl={googleMapsUrl}
            appleMapsUrl={appleMapsUrl}
            mapContainerRef={mapContainerRef}
            routeLoading={routeLoading}
            routeSnapshot={routeSnapshot}
            routeError={routeError}
          />

        </div>
      </div>

      {showEditorPanel && (
        <EventEditorPanel
          editorMode={editorMode}
          selectedVisit={selectedVisit}
          visitStartInput={visitStartInput}
          setVisitStartInput={setVisitStartInput}
          visitStatusInput={visitStatusInput}
          setVisitStatusInput={setVisitStatusInput}
          savingVisit={savingVisit}
          saveVisit={saveVisit}
          patientForm={patientForm}
          setPatientForm={setPatientForm}
          savingPatient={savingPatient}
          savePatient={savePatient}
          closeEditor={() => {
            setEditorMode("none");
            setSelectedVisitId(null);
            setEditorPosition(null);
          }}
          position={editorPosition}
        />
      )}

      {addEventOpen && (
        <div
          ref={addEventRef}
          className="floating-popover"
          style={{
            left: `${Math.max(16, Math.min((addEventPosition?.x ?? window.innerWidth / 2) + 8, window.innerWidth - 420))}px`,
            top: `${Math.max(16, Math.min((addEventPosition?.y ?? 120) + 8, window.innerHeight - 580))}px`,
          }}
          role="dialog"
          aria-modal="false"
        >
            <div className="floating-popover-card">
              <div className="section-head">
                <h3 className="text-base font-bold text-slate-900">Add Event</h3>
                <button
                  className="btn-quiet"
                  onClick={() => {
                    setAddEventOpen(false);
                    setPendingSlot(null);
                  }}
                >
                  Close
                </button>
              </div>
              <div className="field">
                <span className="field-label">Start time</span>
                <input type="datetime-local" value={addEventStartInput} onChange={(event) => setAddEventStartInput(event.target.value)} />
              </div>
              <div className="field">
                <span className="field-label">Status</span>
                <select value={addEventStatusInput} onChange={(event) => setAddEventStatusInput(event.target.value as Visit["status"])}>
                  <option value="pending_patient_confirmation">Pending Confirmation</option>
                  <option value="confirmed">Confirmed</option>
                  <option value="declined">Declined</option>
                  <option value="unscheduled">Unscheduled</option>
                </select>
              </div>
              <div className="field">
                <span className="field-label">Patient</span>
                <select value={selectedPatientOption} onChange={(event) => setSelectedPatientOption(event.target.value)}>
                  <option value="">Select patient...</option>
                  {patients.map((patient) => (
                    <option key={patient.id} value={String(patient.id)}>
                      {patient.full_name}
                    </option>
                  ))}
                  <option value="new">+ Add new patient</option>
                </select>
              </div>
              {selectedPatientOption === "new" && (
                <div className="message-form mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <label className="field">
                    <span className="field-label">Full Name</span>
                    <input value={newPatientForm.full_name} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, full_name: e.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="field-label">Phone</span>
                    <input value={newPatientForm.phone} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, phone: e.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="field-label">Visit Duration (min)</span>
                    <input
                      type="number"
                      min={15}
                      step={15}
                      value={newPatientForm.visit_duration_minutes}
                      onChange={(e) =>
                        setNewPatientForm((prev) => ({
                          ...prev,
                          visit_duration_minutes: Number(e.target.value) || 60,
                        }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">Address</span>
                    <input value={newPatientForm.address_line1} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, address_line1: e.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="field-label">City</span>
                    <input value={newPatientForm.city} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, city: e.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="field-label">State</span>
                    <input value={newPatientForm.state} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, state: e.target.value }))} />
                  </label>
                  <label className="field">
                    <span className="field-label">Postal Code</span>
                    <input value={newPatientForm.postal_code} onChange={(e) => setNewPatientForm((prev) => ({ ...prev, postal_code: e.target.value }))} />
                  </label>
                </div>
              )}
              <div className="controls">
                <button className="btn-primary" onClick={createEventFromModal} disabled={savingNewEvent}>
                  {savingNewEvent ? "Saving..." : "Create Event"}
                </button>
              </div>
            </div>
          </div>
      )}
    </section>
  );
}

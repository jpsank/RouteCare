import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import type { CalendarBlock, CalendarConnection, ClinicianProfile, Patient, WeeklySchedule, Visit } from "../types";
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
  schedule: WeeklySchedule | null;
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

const DAY_LABELS: Array<[number, string]> = [
  [1, "M"],
  [2, "T"],
  [3, "W"],
  [4, "T"],
  [5, "F"],
  [6, "S"],
  [0, "S"],
];

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
  const visits: Visit[] = schedule?.visits ?? [];

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
  const [showSettings, setShowSettings] = useState(false);
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
        if (index === 24) return { minuteValue, label: "12:00 AM (next day)" };
        const meridiem = index >= 12 ? "PM" : "AM";
        const hour12 = ((index + 11) % 12) + 1;
        return { minuteValue, label: `${hour12}:00 ${meridiem}` };
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
      <div className="rc-card">
        <h2 className="rc-section-title">Calendar Planner</h2>
        <p className="rc-section-subtitle">Generate your optimized weekly schedule.</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn-primary" onClick={() => onOptimize()} disabled={loading}>
            {loading ? "Optimizing..." : "Generate Week"}
          </button>
          <button onClick={saveHomeFromCurrentLocation} disabled={loading || savingHomeLocation}>
            {savingHomeLocation ? "Saving..." : "Use Current Location"}
          </button>
          <button className="btn-ghost" onClick={onSeedDemoPatients} disabled={loading}>
            Load Demo Patients
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="rc-card">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn-primary" onClick={() => onOptimize()} disabled={loading}>
              {loading ? "Optimizing..." : "Re-optimize"}
            </button>
            <button onClick={saveHomeFromCurrentLocation} disabled={loading || savingHomeLocation}>
              {savingHomeLocation ? "Saving..." : "Use Current Location"}
            </button>
            <button className="btn-ghost" onClick={onSeedDemoPatients} disabled={loading}>
              Demo Patients
            </button>
            <button className="btn-ghost" onClick={() => setShowSettings(!showSettings)}>
              {showSettings ? "Hide Settings" : "Settings"}
            </button>
          </div>

          <div className="flex items-center gap-1">
            {DAY_LABELS.map(([wday, label]) => (
              <button
                key={`wd-${wday}`}
                type="button"
                className={`rc-day-btn ${workingDays.includes(wday) ? "active" : ""}`}
                onClick={() => toggleWorkingDay(wday)}
                disabled={loading || savingWorkingDays}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {showSettings && (
          <div className="mt-3 space-y-3 border-t border-gray-200 pt-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="rc-field">
                <span className="rc-label">Day starts</span>
                <select
                  className="w-36"
                  value={workdayStartMinute}
                  onChange={(event) => updateWorkdayRange(Number(event.target.value), workdayEndMinute)}
                  disabled={loading || savingHours}
                >
                  {hourOptions
                    .filter((o) => o.minuteValue < workdayEndMinute)
                    .map((o) => (
                      <option key={`s-${o.minuteValue}`} value={o.minuteValue}>{o.label}</option>
                    ))}
                </select>
              </div>
              <div className="rc-field">
                <span className="rc-label">Day ends</span>
                <select
                  className="w-36"
                  value={workdayEndMinute}
                  onChange={(event) => updateWorkdayRange(workdayStartMinute, Number(event.target.value))}
                  disabled={loading || savingHours}
                >
                  {hourOptions
                    .filter((o) => o.minuteValue > workdayStartMinute)
                    .map((o) => (
                      <option key={`e-${o.minuteValue}`} value={o.minuteValue}>{o.label}</option>
                    ))}
                </select>
              </div>
              <div className="rc-field">
                <span className="rc-label">Home lat</span>
                <input
                  className="w-24"
                  value={homeLatitudeInput}
                  onChange={(e) => setHomeLatitudeInput(e.target.value)}
                  placeholder="36.18"
                />
              </div>
              <div className="rc-field">
                <span className="rc-label">Home lng</span>
                <input
                  className="w-24"
                  value={homeLongitudeInput}
                  onChange={(e) => setHomeLongitudeInput(e.target.value)}
                  placeholder="-94.13"
                />
              </div>
              <button className="btn-sm" onClick={saveHomeFromInputs} disabled={loading || savingHomeInput}>
                {savingHomeInput ? "Saving..." : "Save Home"}
              </button>
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
          </div>
        )}
      </div>

      {/* Calendar + Route */}
      <div className="rc-calendar-layout">
        <div className="rc-calendar-wrapper">
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
          className="rc-popover"
          style={{
            left: `${Math.max(16, Math.min((addEventPosition?.x ?? window.innerWidth / 2) + 8, window.innerWidth - 420))}px`,
            top: `${Math.max(16, Math.min((addEventPosition?.y ?? 120) + 8, window.innerHeight - 540))}px`,
          }}
          role="dialog"
          aria-modal="false"
        >
          <div className="rc-popover-card space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-900">Add Event</h3>
              <button
                className="btn-ghost btn-xs"
                onClick={() => { setAddEventOpen(false); setPendingSlot(null); }}
              >
                Close
              </button>
            </div>
            <div className="rc-field">
              <span className="rc-label">Start time</span>
              <input type="datetime-local" value={addEventStartInput} onChange={(e) => setAddEventStartInput(e.target.value)} />
            </div>
            <div className="rc-field">
              <span className="rc-label">Status</span>
              <select value={addEventStatusInput} onChange={(e) => setAddEventStatusInput(e.target.value as Visit["status"])}>
                <option value="pending_patient_confirmation">Pending Confirmation</option>
                <option value="confirmed">Confirmed</option>
                <option value="declined">Declined</option>
                <option value="unscheduled">Unscheduled</option>
              </select>
            </div>
            <div className="rc-field">
              <span className="rc-label">Patient</span>
              <select value={selectedPatientOption} onChange={(e) => setSelectedPatientOption(e.target.value)}>
                <option value="">Select patient...</option>
                {patients.map((p) => (
                  <option key={p.id} value={String(p.id)}>{p.full_name}</option>
                ))}
                <option value="new">+ New patient</option>
              </select>
            </div>
            {selectedPatientOption === "new" && (
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
            <button className="btn-primary w-full" onClick={createEventFromModal} disabled={savingNewEvent}>
              {savingNewEvent ? "Saving..." : "Create Event"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

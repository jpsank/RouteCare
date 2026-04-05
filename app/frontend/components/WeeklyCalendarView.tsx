import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { CalendarBlock, CalendarConnection, ClinicianProfile, Message, Patient, WeeklySchedule, Visit } from "../types";
import { AddEventPopover } from "./calendar/AddEventPopover";
import { EventEditorPanel } from "./calendar/EventEditorPanel";
import { WeeklySettingsPanel } from "./calendar/WeeklySettingsPanel";
import { useCalendarConnections } from "./calendar/hooks/useCalendarConnections";
import { useEventEditor } from "./calendar/hooks/useEventEditor";
import {
  asDateKey,
  localInputToIso,
  patientColor,
  statusBorderColor,
  toLocalInputValue,
  type PatientSavePayload,
} from "./calendar/utils";

const ScheduleCalendar = lazy(async () => {
  const module = await import("./calendar/ScheduleCalendar");
  return { default: module.ScheduleCalendar };
});

const RoutePanel = lazy(async () => {
  const module = await import("./calendar/RoutePanel");
  return { default: module.RoutePanel };
});

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
  onUpdateLunchSettings: (lunchStartMinute: number, lunchDurationMinutes: number, lunchWindowMinutes: number) => Promise<void>;
  onUpdateDisplayName: (displayName: string) => Promise<void>;
  onSetHomeFromCurrentLocation: (latitude: number, longitude: number) => Promise<void>;
  onUpdateHomeLocation: (latitude: number, longitude: number) => Promise<void>;
  onCalendarRefresh: () => Promise<void>;
  onBulkConfirm?: () => Promise<{ sent_count: number; skipped_count: number } | undefined>;
  messages?: ReadonlyArray<Message>;
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
  onUpdateLunchSettings,
  onUpdateDisplayName,
  onSetHomeFromCurrentLocation,
  onUpdateHomeLocation,
  onCalendarRefresh,
  onBulkConfirm,
  messages = [],
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
  const [addEventPosition, setAddEventPosition] = useState<{ x: number; y: number } | null>(null);
  const [editorPosition, setEditorPosition] = useState<{ x: number; y: number } | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [savingLunch, setSavingLunch] = useState(false);
  const [displayNameInput, setDisplayNameInput] = useState("");
  const [savingDisplayName, setSavingDisplayName] = useState(false);


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
    setDisplayNameInput(clinicianProfile?.display_name ?? "");
  }, [clinicianProfile?.display_name]);

  const workdayStartMinute = clinicianProfile?.workday_start_minute ?? 8 * 60;
  const workdayEndMinute = clinicianProfile?.workday_end_minute ?? 18 * 60;
  const workingDays = clinicianProfile?.working_days?.length ? clinicianProfile.working_days : [1, 2, 3, 4, 5];
  const lunchStartMinute = clinicianProfile?.lunch_start_minute ?? 720;
  const lunchDurationMinutes = clinicianProfile?.lunch_duration_minutes ?? 30;
  const lunchWindowMinutes = clinicianProfile?.lunch_window_minutes ?? 90;

  const updateLunch = async (startMin: number, duration: number, window: number) => {
    setSavingLunch(true);
    try {
      await onUpdateLunchSettings(startMin, duration, window);
    } finally {
      setSavingLunch(false);
    }
  };

  const updateWorkdayRange = async (nextStart: number, nextEnd: number) => {
    if (nextEnd <= nextStart) return;
    setSavingHours(true);
    try {
      await onUpdateWorkingHours(nextStart, nextEnd);
    } finally {
      setSavingHours(false);
    }
  };

  const saveDisplayName = async () => {
    setSavingDisplayName(true);
    try {
      await onUpdateDisplayName(displayNameInput.trim());
    } finally {
      setSavingDisplayName(false);
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

  const lunchEvents = useMemo(() => {
    if (!schedule?.week_start_on) return [];
    const lunchBreaks = (schedule.optimization_summary?.lunch_breaks ?? {}) as Record<
      string,
      { start_minute: number; end_minute: number }
    >;
    const weekStart = new Date(schedule.week_start_on + "T00:00:00");
    const events: Array<{
      id: string;
      title: string;
      start: string;
      end: string;
      display: "background";
      className: string;
    }> = [];

    for (let d = 0; d < 7; d++) {
      const day = new Date(weekStart);
      day.setDate(day.getDate() + d);
      if (!workingDays.includes(day.getDay())) continue;
      const iso = day.toISOString().slice(0, 10);
      const lb = lunchBreaks[iso];
      const startMin = lb?.start_minute ?? lunchStartMinute;
      const endMin = lb?.end_minute ?? lunchStartMinute + lunchDurationMinutes;
      const sH = Math.floor(startMin / 60);
      const sM = startMin % 60;
      const eH = Math.floor(endMin / 60);
      const eM = endMin % 60;
      events.push({
        id: `lunch-${iso}`,
        title: "Lunch",
        start: `${iso}T${String(sH).padStart(2, "0")}:${String(sM).padStart(2, "0")}:00`,
        end: `${iso}T${String(eH).padStart(2, "0")}:${String(eM).padStart(2, "0")}:00`,
        display: "background" as const,
        className: "calendar-event-lunch",
      });
    }
    return events;
  }, [schedule?.week_start_on, schedule?.optimization_summary, lunchStartMinute, lunchDurationMinutes, workingDays]);

  const calendarEvents = useMemo(
    () => [
      ...visits.map((visit) => {
        const pc = patientColor(visit.patient_id);
        const isSelected = selectedVisitId === visit.id;
        return {
          id: `visit-${visit.id}`,
          title: visit.patient_name,
          start: visit.starts_at,
          end: visit.ends_at,
          backgroundColor: pc.bg,
          textColor: pc.text,
          borderColor: statusBorderColor(visit.status),
          className: `calendar-event visit-status-${visit.status}${isSelected ? " visit-selected" : ""}`,
        };
      }),
      ...calendarBlocks.map((block) => ({
        id: `block-${block.id}`,
        title: block.title || "Blocked",
        start: block.starts_at,
        end: block.ends_at,
        display: "background" as const,
        className: "calendar-event-blocked",
      })),
      ...lunchEvents,
      ...(addEventOpen && addEventStartInput
        ? (() => {
            try {
              const startIso = localInputToIso(addEventStartInput);
              const startMs = new Date(startIso).getTime();
              if (!Number.isFinite(startMs)) return [];
              const endIso = new Date(startMs + 30 * 60 * 1000).toISOString();
              return [
                {
                  id: "preview-slot",
                  title: "",
                  start: startIso,
                  end: endIso,
                  display: "background" as const,
                  className: "calendar-slot-selected",
                },
              ];
            } catch {
              return [];
            }
          })()
        : []),
    ],
    [
      addEventOpen,
      addEventStartInput,
      calendarBlocks,
      lunchEvents,
      selectedVisitId,
      visits,
    ],
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
    setAddEventPosition({ x: Math.max(12, pointer.x + 8), y: Math.max(12, pointer.y + 8) });
    setAddEventOpen(true);
  };

  if (!schedule) {
    return (
      <div className="rc-empty">
        <p className="mb-2 text-sm font-medium text-gray-700">No schedule yet</p>
        <div className="flex flex-wrap justify-center gap-2">
          <button className="btn-primary btn-sm" onClick={() => onOptimize()} disabled={loading}>
            {loading ? "Optimizing..." : "Generate Week"}
          </button>
          <button className="btn-sm" onClick={saveHomeFromCurrentLocation} disabled={loading || savingHomeLocation}>
            {savingHomeLocation ? "Saving..." : "Use Current Location"}
          </button>
          <button className="btn-ghost btn-sm" onClick={onSeedDemoPatients} disabled={loading}>
            Load Demo Patients
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Compact toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary btn-sm" onClick={() => onOptimize()} disabled={loading}>
          {loading ? "Optimizing..." : "Re-optimize"}
        </button>

        <div className="flex items-center gap-0.5">
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

        <button className="btn-ghost btn-xs" onClick={() => setShowSettings(!showSettings)}>
          {showSettings ? "Hide Settings" : "Settings"}
        </button>
      </div>

      {/* Bulk confirm banner */}
      {(() => {
        const outboundVisitIds = new Set(messages.filter((m) => m.direction === "outbound").map((m) => m.visit_id));
        const unconfirmedCount = visits.filter(
          (v) => v.status === "pending_patient_confirmation" && !outboundVisitIds.has(v.id),
        ).length;
        if (unconfirmedCount === 0 || !onBulkConfirm) return null;
        return (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
            <span className="text-amber-800">
              <strong>{unconfirmedCount}</strong> visit{unconfirmedCount !== 1 ? "s" : ""} need confirmation
            </span>
            <button
              className="btn-primary btn-sm ml-auto"
              onClick={() => onBulkConfirm()}
              disabled={loading}
            >
              Send All Confirmations
            </button>
          </div>
        );
      })()}

      {showSettings && (
        <WeeklySettingsPanel
          loading={loading}
          workdayStartMinute={workdayStartMinute}
          workdayEndMinute={workdayEndMinute}
          onUpdateWorkdayRange={updateWorkdayRange}
          savingHours={savingHours}
          homeLatitudeInput={homeLatitudeInput}
          homeLongitudeInput={homeLongitudeInput}
          onHomeLatitudeInputChange={setHomeLatitudeInput}
          onHomeLongitudeInputChange={setHomeLongitudeInput}
          onSaveHomeFromInputs={saveHomeFromInputs}
          onSaveHomeFromCurrentLocation={saveHomeFromCurrentLocation}
          savingHomeInput={savingHomeInput}
          savingHomeLocation={savingHomeLocation}
          onSeedDemoPatients={onSeedDemoPatients}
          lunchStartMinute={lunchStartMinute}
          lunchDurationMinutes={lunchDurationMinutes}
          lunchWindowMinutes={lunchWindowMinutes}
          onUpdateLunch={updateLunch}
          savingLunch={savingLunch}
          displayNameInput={displayNameInput}
          onDisplayNameInputChange={setDisplayNameInput}
          onSaveDisplayName={saveDisplayName}
          savingDisplayName={savingDisplayName}
          calendarConnectionsProps={{
            connectingProvider,
            setConnectingProvider,
            externalCalendarId,
            setExternalCalendarId,
            appleIcsUrl,
            setAppleIcsUrl,
            googleConnection,
            googleCalendars,
            selectedGoogleCalendarId,
            setSelectedGoogleCalendarId,
            calendarConnections,
            calendarConfigMessage,
            connectCalendar,
            loadGoogleCalendars,
            saveGoogleCalendarSelection,
            syncConnection,
            pushToConnection,
            calendarFeedUrl: api.calendarFeedUrl(schedule.week_start_on),
          }}
        />
      )}

      {/* Calendar + Route */}
      <div className="rc-calendar-layout">
        <div className="rc-calendar-wrapper">
          {/* Patient legend — inside the calendar card */}
          {visits.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-100 px-3 py-1.5 text-[11px] text-gray-500">
              {Array.from(new Map(visits.map((v) => [v.patient_id, v.patient_name]))).map(([pid, name]) => {
                const pc = patientColor(pid);
                return (
                  <span key={pid} className="inline-flex items-center gap-1">
                    <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: pc.accent }} />
                    {name}
                  </span>
                );
              })}
              <span className="ml-auto hidden items-center gap-2.5 text-gray-400 sm:inline-flex">
                <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-1.5 rounded-sm" style={{ backgroundColor: "#16a34a" }} />OK</span>
                <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-1.5 rounded-sm" style={{ backgroundColor: "#d97706" }} />Pending</span>
                <span className="inline-flex items-center gap-1"><span className="inline-block h-1.5 w-1.5 rounded-sm" style={{ backgroundColor: "#dc2626" }} />Declined</span>
              </span>
            </div>
          )}
          <Suspense fallback={<div className="p-4 text-sm text-gray-500">Loading calendar...</div>}>
            <ScheduleCalendar
              events={calendarEvents}
              workdayStartMinute={workdayStartMinute}
              workdayEndMinute={workdayEndMinute}
              onDateClick={(dateKey, startStr, pointer) => {
                setSelectedVisitId(null);
                setEditorMode("none");
                setEditorPosition(null);
                if (addEventOpen) {
                  setAddEventOpen(false);
                  return;
                }
                openAddEventModal(dateKey, startStr, pointer);
              }}
              onEventClick={(eventId, startStr, pointer) => {
                setSelectedDate(asDateKey(startStr));
                setAddEventOpen(false);
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
          </Suspense>
        </div>

        <RoutePanel
          selectedDate={selectedDate}
          dayVisits={dayVisits}
          dayBlocks={dayBlocks}
          selectedVisitId={selectedVisitId}
          setSelectedVisitId={setSelectedVisitId}
          homeOrigin={homeOrigin}
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

      {addEventOpen && addEventPosition && (
        <AddEventPopover
          patients={patients}
          position={addEventPosition}
          startInput={addEventStartInput}
          onStartInputChange={setAddEventStartInput}
          onClose={() => setAddEventOpen(false)}
          onCalendarRefresh={onCalendarRefresh}
        />
      )}
    </div>
  );
}

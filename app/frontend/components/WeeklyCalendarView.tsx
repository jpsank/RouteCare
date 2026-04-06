import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import type { Alert, CalendarBlock, CalendarConnection, ClinicianProfile, Message, Patient, WeeklySchedule, Visit } from "../types";
import { AddEventPopover } from "./calendar/AddEventPopover";
import { DailyRouteView } from "./DailyRouteView";
import { EventEditorPanel } from "./calendar/EventEditorPanel";
import { InlineAlertSummary } from "./calendar/InlineAlertSummary";
import { PatientEditorPanel } from "./calendar/PatientEditorPanel";
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
  onSendMessage?: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => Promise<import("../types").Message | undefined>;
  messages?: ReadonlyArray<Message> | null;
  alerts?: Alert[];
  onUpdateAlert?: (alertId: number, status: Alert["status"]) => Promise<void>;
  onExecuteAlertAction?: (alertId: number) => Promise<void>;
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
  onSendMessage,
  messages,
  alerts = [],
  onUpdateAlert,
  onExecuteAlertAction,
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
  const [editingPatientId, setEditingPatientId] = useState<number | null>(null);
  const [routeCollapsed, setRouteCollapsed] = useState(() => {
    try { return localStorage.getItem("rc-route-collapsed") === "true"; } catch { return false; }
  });
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
  const [mobileToday, setMobileToday] = useState(() => typeof window !== "undefined" && window.innerWidth < 640);
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
          extendedProps: { patientId: visit.patient_id },
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
    <div className="space-y-1 sm:space-y-2">
      {/* Compact toolbar */}
      <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
        <button className="btn-primary btn-sm" onClick={() => onOptimize()} disabled={loading}>
          {loading ? "Optimizing..." : "Re-optimize"}
        </button>

        {/* Mobile today/calendar toggle — inline in toolbar */}
        <div className="flex items-center gap-0.5 sm:hidden">
          <button
            className={`rounded-md px-2 py-1 text-[10px] font-medium transition-colors ${mobileToday ? "bg-indigo-100 text-indigo-700" : "bg-transparent text-gray-400"}`}
            onClick={() => setMobileToday(true)}
          >
            Today
          </button>
          <button
            className={`rounded-md px-2 py-1 text-[10px] font-medium transition-colors ${!mobileToday ? "bg-indigo-100 text-indigo-700" : "bg-transparent text-gray-400"}`}
            onClick={() => setMobileToday(false)}
          >
            Week
          </button>
        </div>

        {onUpdateAlert && (
          <InlineAlertSummary
            alerts={alerts}
            onUpdateAlert={onUpdateAlert}
            onExecuteAction={onExecuteAlertAction}
          />
        )}

        <button
          className="flex items-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100 ml-auto sm:ml-0"
          onClick={() => setShowSettings(!showSettings)}
        >
          <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
          </svg>
          <span className="hidden sm:inline">Settings</span>
        </button>
      </div>

      {/* Bulk confirm banner */}
      {(() => {
        if (!messages || !onBulkConfirm) return null;
        const contactedPatientIds = new Set(messages.filter((m) => m.direction === "outbound").map((m) => m.patient_id));
        const unconfirmedCount = visits.filter(
          (v) => v.status === "pending_patient_confirmation" && !contactedPatientIds.has(v.patient_id),
        ).length;
        if (unconfirmedCount === 0) return null;
        return (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs sm:gap-3 sm:px-3 sm:py-2 sm:text-sm">
            <span className="text-amber-800">
              <strong>{unconfirmedCount}</strong> unconfirmed
            </span>
            <button
              className="btn-primary btn-sm ml-auto"
              onClick={() => onBulkConfirm()}
              disabled={loading}
            >
              Send All
            </button>
          </div>
        );
      })()}

      {/* Settings modal (portaled to body) */}
      {showSettings && createPortal(
        <div className="fixed inset-0 z-[800] flex items-start justify-center px-4 pt-[10vh] animate-[fadeIn_0.15s_ease-out] bg-black/25 backdrop-blur-[2px]" data-modal-overlay onClick={(e) => { if (e.target === e.currentTarget) setShowSettings(false); }}>
          <div className="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-2xl bg-white p-5 shadow-2xl ring-1 ring-black/5 animate-[scaleIn_0.15s_ease-out]">
            <div className="mb-5 flex items-center justify-between">
              <h2 className="text-base font-semibold text-gray-900">Settings</h2>
              <button className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600" onClick={() => setShowSettings(false)}>
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>

            {/* Working Days */}
            <div className="mb-4">
              <span className="rc-label mb-1.5 block">Working days</span>
              <div className="flex flex-wrap gap-1.5">
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
          </div>
        </div>,
        document.body,
      )}

      {/* Mobile today view */}
      {mobileToday && (
        <div className="sm:hidden">
          <DailyRouteView
            visits={visits}
            date={new Date().toISOString().slice(0, 10)}
            onSendMessage={onSendMessage}
            homeOrigin={homeOrigin}
          />
        </div>
      )}

      {/* Calendar + Route (hidden on mobile when today view is active) */}
      <div className={`grid items-start gap-3 ${routeCollapsed ? "grid-cols-1 xl:grid-cols-[1fr_auto]" : "grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)]"} ${mobileToday ? "hidden sm:grid" : ""}`}>
        <div className="rc-calendar-wrapper">
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
              onEventClick={(eventId, startStr, pointer, target) => {
                setSelectedDate(asDateKey(startStr));
                setAddEventOpen(false);
                const match = eventId.match(/^visit-(\d+)$/);
                if (!match) return;
                const visitId = Number(match[1]);

                // Click on patient name → open patient editor directly
                const nameEl = (target as HTMLElement).closest?.("[data-patient-id]");
                if (nameEl) {
                  const patientId = Number(nameEl.getAttribute("data-patient-id"));
                  if (patientId) {
                    setEditingPatientId(patientId);
                    return;
                  }
                }

                const mobile = window.innerWidth < 640;
                if (mobile || (selectedVisitId === visitId && editorMode === "none")) {
                  setSelectedVisitId(visitId);
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

        {routeCollapsed ? (
          <>
            {/* xl: show collapsed tab to re-open; below xl: always show the route panel inline */}
            <button
              className="hidden xl:flex flex-col items-center gap-2 self-start rounded-lg border border-gray-200 bg-white px-2 py-3 text-[10px] font-medium text-gray-400 shadow-sm hover:bg-gray-50 hover:text-gray-600"
              onClick={() => { setRouteCollapsed(false); try { localStorage.setItem("rc-route-collapsed", "false"); } catch {} }}
              title="Show route panel"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 6.75V15m6-6v8.25m.503 3.498 4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 0 0-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0Z" />
              </svg>
              <span className="[writing-mode:vertical-lr]">Route</span>
            </button>
            <div className="xl:hidden">
              <RoutePanel
                selectedDate={selectedDate}
                dayVisits={dayVisits}
                dayBlocks={dayBlocks}
                selectedVisitId={selectedVisitId}
                setSelectedVisitId={setSelectedVisitId}
                homeOrigin={homeOrigin}
              />
            </div>
          </>
        ) : (
          <div className="relative">
            <button
              className="hidden xl:block absolute right-2 top-2 z-10 rounded-md border-0 bg-transparent p-0.5 text-gray-300 shadow-none hover:text-gray-500"
              onClick={() => { setRouteCollapsed(true); try { localStorage.setItem("rc-route-collapsed", "true"); } catch {} }}
              title="Hide route panel"
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
            </button>
            <RoutePanel
              selectedDate={selectedDate}
              dayVisits={dayVisits}
              dayBlocks={dayBlocks}
              selectedVisitId={selectedVisitId}
              setSelectedVisitId={setSelectedVisitId}
              homeOrigin={homeOrigin}
            />
          </div>
        )}
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
          closeEditor={() => {
            setEditorMode("none");
            setSelectedVisitId(null);
            setEditorPosition(null);
          }}
          position={editorPosition}
          onSendMessage={onSendMessage}
          onEditPatient={(patientId) => setEditingPatientId(patientId)}
        />
      )}

      {editingPatientId && createPortal(
        <PatientEditorPanel
          patient={patients.find((p) => p.id === editingPatientId) ?? null}
          onSave={async (patientId, payload) => {
            const prev = patients.find((p) => p.id === patientId);
            await onUpdatePatient(patientId, payload);
            const addressChanged =
              prev && (prev.address_line1 !== payload.address_line1 ||
                prev.city !== payload.city ||
                prev.state !== payload.state ||
                prev.postal_code !== payload.postal_code);
            if (addressChanged) {
              setEditorMode("none");
              setSelectedVisitId(null);
              setEditorPosition(null);
              await onOptimize();
            } else {
              await onCalendarRefresh();
            }
          }}
          onClose={() => setEditingPatientId(null)}
        />,
        document.body,
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

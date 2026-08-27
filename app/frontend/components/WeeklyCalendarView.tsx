import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { useClickOutside } from "./hooks/useClickOutside";
import { createPortal } from "react-dom";
import { api } from "../lib/api";
import type { Alert, CalendarBlock, CalendarConnection, ClinicianProfile, Message, Patient, WeeklySchedule, Visit } from "../types";
import { AddEventPopover } from "./calendar/AddEventPopover";
import { DailyRouteView } from "./DailyRouteView";
import { PatientListSlideOver } from "./PatientListSlideOver";
import { EventEditorPanel } from "./calendar/EventEditorPanel";
import { InlineAlertSummary } from "./calendar/InlineAlertSummary";
import { WeekStatsPopover } from "./calendar/WeekStatsPopover";
import { PatientEditorPanel } from "./calendar/PatientEditorPanel";
import { SettingsModal } from "./calendar/SettingsModal";
import { useCalendarConnections } from "./calendar/hooks/useCalendarConnections";
import { useEventEditor } from "./calendar/hooks/useEventEditor";
import { useSettingsState } from "./calendar/hooks/useSettingsState";
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
  pendingMessageCount?: number;
  onOpenMessages?: () => void;
  onSignOut?: () => void;
  onOptimize: (start?: { latitude: number; longitude: number }) => Promise<boolean>;
  onCreatePatient: (patient: PatientSavePayload) => Promise<boolean>;
  onUpdatePatient: (patientId: number, patient: PatientSavePayload) => Promise<boolean>;
  onSeedDemoPatients: () => Promise<boolean>;
  onImportPatients: (file: File) => Promise<{ imported: number; errors: string[]; header_map?: Record<string, string> } | undefined>;
  clinicianProfile: ClinicianProfile | null;
  onUpdateWorkingHours: (workdayStartMinute: number, workdayEndMinute: number) => Promise<boolean>;
  onUpdateWorkingDays: (workingDays: number[]) => Promise<boolean>;
  onUpdateLunchSettings: (lunchStartMinute: number, lunchDurationMinutes: number, lunchWindowMinutes: number) => Promise<boolean>;
  onUpdateDisplayName: (displayName: string) => Promise<boolean>;
  onUpdateSchedulingSettings: (settings: Record<string, unknown>) => Promise<boolean>;
  onSetHomeFromCurrentLocation: (latitude: number, longitude: number) => Promise<boolean>;
  onUpdateHomeLocation: (latitude: number, longitude: number) => Promise<boolean>;
  onCalendarRefresh: () => Promise<void>;
  onBulkConfirm?: () => Promise<{ sent_count: number; skipped_count: number } | undefined>;
  onSendMessage?: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => Promise<import("../types").Message | undefined>;
  messages?: ReadonlyArray<Message> | null;
  alerts?: Alert[];
  onUpdateAlert?: (alertId: number, status: Alert["status"]) => Promise<boolean>;
  onExecuteAlertAction?: (alertId: number) => Promise<boolean>;
};


export function WeeklyCalendarView({
  schedule,
  patients,
  calendarBlocks,
  calendarConnections,
  loading,
  pendingMessageCount = 0,
  onOpenMessages,
  onSignOut,
  onOptimize,
  onCreatePatient,
  onUpdatePatient,
  onSeedDemoPatients,
  onImportPatients,
  clinicianProfile,
  onUpdateWorkingHours,
  onUpdateWorkingDays,
  onUpdateLunchSettings,
  onUpdateDisplayName,
  onUpdateSchedulingSettings,
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
  const [patientListOpen, setPatientListOpen] = useState(false);
  const [routeCollapsed, setRouteCollapsed] = useState(() => {
    try { return localStorage.getItem("rc-route-collapsed") === "true"; } catch { return false; }
  });
  const settings = useSettingsState(clinicianProfile, {
    onUpdateWorkingHours,
    onUpdateWorkingDays,
    onUpdateLunchSettings,
    onUpdateDisplayName,
    onUpdateSchedulingSettings,
    onSetHomeFromCurrentLocation,
    onUpdateHomeLocation,
  });

  const [addEventOpen, setAddEventOpen] = useState(false);
  const [addEventStartInput, setAddEventStartInput] = useState("");
  const [addEventPosition, setAddEventPosition] = useState<{ x: number; y: number } | null>(null);
  const [editorPosition, setEditorPosition] = useState<{ x: number; y: number } | null>(null);
  const [mobileToday, setMobileToday] = useState(false);
  const [mobileTodayInitialized, setMobileTodayInitialized] = useState(false);
  const [warningsExpanded, setWarningsExpanded] = useState(false);
  const warningsRef = useRef<HTMLDivElement>(null);
  useClickOutside(warningsRef, () => setWarningsExpanded(false), warningsExpanded);


  const homeOrigin = useMemo(() => {
    if (clinicianProfile?.home_latitude == null || clinicianProfile?.home_longitude == null) return null;
    return {
      latitude: Number(clinicianProfile.home_latitude),
      longitude: Number(clinicianProfile.home_longitude),
    };
  }, [clinicianProfile?.home_latitude, clinicianProfile?.home_longitude]);

  // Default to Today view on mobile only if there are visits today
  useEffect(() => {
    if (mobileTodayInitialized || !schedule) return;
    const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
    const today = new Date().toISOString().slice(0, 10);
    const hasVisitsToday = visits.some((v) => asDateKey(v.starts_at) === today);
    setMobileToday(isMobile && hasVisitsToday);
    setMobileTodayInitialized(true);
  }, [schedule, visits, mobileTodayInitialized]);

  const {
    workdayStartMinute, workdayEndMinute, calendarStartMinute, calendarEndMinute, workingDays,
    lunchStartMinute, lunchDurationMinutes, lunchWindowMinutes,
    chartingBufferMinutes, scheduleDensity, maxDriveMinutesPerDay,
    maxContinuousWorkMinutes, requiredBreakMinutes,
    savingHours, savingWorkingDays, savingLunch, savingSchedulingSettings,
    showSettings, setShowSettings,
    updateWorkdayRange, toggleWorkingDay, updateLunch,
    updateSchedulingSettings,
    saveDisplayName, saveHomeLocation, detectCurrentLocation,
  } = settings;

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

  const returnHomeMinutes = useMemo(() => {
    return schedule?.optimization_summary?.return_home_by_day?.[selectedDate] ?? 0;
  }, [schedule?.optimization_summary, selectedDate]);

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
    if (!schedule?.week_start_on || lunchDurationMinutes <= 0) return [];
    const lunchBreaks = schedule.optimization_summary?.lunch_breaks ?? {};
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

  const handleEventDrop = async (eventId: string, newStartStr: string) => {
    const match = eventId.match(/^visit-(\d+)$/);
    if (!match) return;
    const visitId = Number(match[1]);
    try {
      await api.rescheduleVisit(visitId, new Date(newStartStr).toISOString());
      await onCalendarRefresh();
    } catch {
      await onCalendarRefresh(); // revert by refreshing
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
          <button className="btn-sm" onClick={() => void detectCurrentLocation()} disabled={loading}>
            Use Current Location
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
      {/* Compact nav + toolbar (single row) */}
      <div className="mb-2 flex flex-wrap items-center gap-1.5 border-b border-gray-200 pb-2 sm:gap-2">
        <div className="flex items-center">
          <svg className="mr-1 h-5 w-5 flex-none text-orange-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 6.75V15m6-6v8.25m.503 3.498 4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 0 0-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0Z" />
          </svg>
          <span className="mr-2 hidden text-sm font-bold tracking-tight text-gray-900 sm:inline sm:mr-3">RouteCare</span>
        </div>

        <button className="btn-primary btn-sm" onClick={() => onOptimize()} disabled={loading}>
          {loading ? "Optimizing..." : "Re-optimize"}
        </button>

        {/* Consolidated schedule-warning pill (inline in toolbar) */}
        {(() => {
          const contactedPatientIds = new Set(
            (messages ?? []).filter((m) => m.direction === "outbound").map((m) => m.patient_id),
          );
          const unconfirmedCount = (messages && onBulkConfirm)
            ? visits.filter(
                (v) => v.status === "pending_patient_confirmation" && !contactedPatientIds.has(v.patient_id),
              ).length
            : 0;

          const unschedulable = schedule?.optimization_summary?.unschedulable ?? [];
          const reasonLabels: Record<string, string> = {
            min_days_between_visits: "min days between visits",
            day_capacity: "not enough time in day",
            availability_windows: "no patient availability",
            routing_or_drive_limit: "drive limit or routing",
            no_working_days_available: "no working days available",
          };
          const byPatient = new Map<string, Set<string>>();
          for (const u of unschedulable) {
            const existing = byPatient.get(u.patient_name) ?? new Set();
            for (const r of u.reasons ?? []) existing.add(reasonLabels[r] ?? r);
            byPatient.set(u.patient_name, existing);
          }

          const violations = schedule?.optimization_summary?.drive_violations ?? [];

          const hasError = byPatient.size > 0;
          const hasWarn = unconfirmedCount > 0 || violations.length > 0;
          if (!hasError && !hasWarn) return null;

          const tone = hasError
            ? { pill: "border-red-200 bg-red-50 text-red-800 hover:bg-red-100", dot: "bg-red-500" }
            : { pill: "border-orange-200 bg-orange-50 text-orange-800 hover:bg-orange-100", dot: "bg-orange-500" };

          const summaryParts: string[] = [];
          if (unconfirmedCount > 0) summaryParts.push(`${unconfirmedCount} unconfirmed`);
          if (byPatient.size > 0) summaryParts.push(`${byPatient.size} unschedulable`);
          if (violations.length > 0) summaryParts.push(`drive limit ${violations.length}d`);
          const hasDetails = byPatient.size > 0 || violations.length > 0;

          return (
            <div ref={warningsRef} className="relative flex items-center gap-1">
              <button
                type="button"
                className={`flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs font-medium shadow-none ${tone.pill}`}
                onClick={() => hasDetails && setWarningsExpanded((v) => !v)}
                aria-expanded={warningsExpanded}
              >
                <span className={`h-1.5 w-1.5 flex-none rounded-full ${tone.dot}`} />
                <span>{summaryParts.join(" · ")}</span>
                {hasDetails && (
                  <svg
                    className={`h-3 w-3 flex-none transition-transform ${warningsExpanded ? "rotate-180" : ""}`}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                  </svg>
                )}
              </button>
              {unconfirmedCount > 0 && onBulkConfirm && (
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  onClick={() => onBulkConfirm()}
                  disabled={loading}
                >
                  Send All
                </button>
              )}
              {warningsExpanded && hasDetails && (
                <div className="absolute left-0 top-full z-50 mt-1 w-[min(420px,calc(100vw-24px))] space-y-1.5 rounded-2xl bg-white p-3 text-xs shadow-2xl ring-1 ring-black/5 sm:text-sm">
                  {byPatient.size > 0 && (
                    <div className="flex flex-col gap-0.5">
                      <span className="font-semibold text-red-800">Could not schedule:</span>
                      {[...byPatient.entries()].map(([name, reasons]) => (
                        <div key={name} className="ml-3 text-red-700">
                          <strong>{name}</strong>{reasons.size > 0 && ` — ${[...reasons].join(", ")}`}
                        </div>
                      ))}
                    </div>
                  )}
                  {violations.length > 0 && (
                    <div className="text-orange-800">
                      <span className="font-semibold">Drive time exceeds limit:</span>{" "}
                      {violations.map((v) => {
                        const d = new Date(v.date + "T12:00:00");
                        return `${d.toLocaleDateString([], { weekday: "short" })} (${v.drive_minutes}min / ${v.max_drive}min)`;
                      }).join(", ")}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })()}

        {/* Mobile today/calendar toggle — inline in toolbar */}
        <div className="flex items-center gap-0.5 sm:hidden">
          <button
            className={`rounded-md px-2 py-1 text-[10px] font-medium transition-colors ${mobileToday ? "bg-orange-100 text-orange-700" : "bg-transparent text-gray-400"}`}
            onClick={() => setMobileToday(true)}
          >
            Today
          </button>
          <button
            className={`rounded-md px-2 py-1 text-[10px] font-medium transition-colors ${!mobileToday ? "bg-orange-100 text-orange-700" : "bg-transparent text-gray-400"}`}
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

        <WeekStatsPopover schedule={schedule} />

        {schedule && (
          <a
            className="flex items-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100"
            href={`/api/v1/schedule.pdf?week_start_on=${encodeURIComponent(schedule.week_start_on)}`}
            target="_blank"
            rel="noopener"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
            </svg>
            <span className="hidden sm:inline">PDF</span>
          </a>
        )}

        {/* Right cluster: spinner, messages, settings, sign out */}
        <div className="ml-auto flex items-center gap-1">
          {loading && (
            <svg className="h-3.5 w-3.5 animate-spin text-orange-400" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}

          <button
            className="flex items-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100"
            onClick={() => setPatientListOpen(true)}
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
            </svg>
            <span className="hidden sm:inline">Patients</span>
          </button>

          {onOpenMessages && (
            <button
              className="relative flex items-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100"
              onClick={onOpenMessages}
            >
              <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 0 1-.825-.242m9.345-8.334a2.126 2.126 0 0 0-.476-.095 48.64 48.64 0 0 0-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0 0 11.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155" />
              </svg>
              <span className="hidden sm:inline">Messages</span>
              {pendingMessageCount > 0 && (
                <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
                  {pendingMessageCount}
                </span>
              )}
            </button>
          )}

          <button
            className="flex items-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100"
            onClick={() => setShowSettings(!showSettings)}
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
            </svg>
            <span className="hidden sm:inline">Settings</span>
          </button>

          {onSignOut && (
            <button
              className="rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-400 shadow-none hover:bg-gray-100 hover:text-gray-600"
              onClick={onSignOut}
              title="Sign out"
            >
              <svg className="h-4 w-4 sm:hidden" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3 0 3-3m0 0-3-3m3 3H9" />
              </svg>
              <span className="hidden sm:inline">Sign out</span>
            </button>
          )}
        </div>
      </div>

      {showSettings && (
        <SettingsModal
          onClose={() => setShowSettings(false)}
          loading={loading}
          clinicianProfile={clinicianProfile}
          workdayStartMinute={workdayStartMinute}
          workdayEndMinute={workdayEndMinute}
          onUpdateWorkdayRange={updateWorkdayRange}
          savingHours={savingHours}
          workingDays={workingDays}
          onToggleWorkingDay={toggleWorkingDay}
          savingWorkingDays={savingWorkingDays}
          onSaveHomeLocation={saveHomeLocation}
          onDetectCurrentLocation={detectCurrentLocation}
          lunchStartMinute={lunchStartMinute}
          lunchDurationMinutes={lunchDurationMinutes}
          lunchWindowMinutes={lunchWindowMinutes}
          onUpdateLunch={updateLunch}
          savingLunch={savingLunch}
          onSaveDisplayName={saveDisplayName}
          chartingBufferMinutes={chartingBufferMinutes}
          scheduleDensity={scheduleDensity}
          maxDriveMinutesPerDay={maxDriveMinutesPerDay}
          maxContinuousWorkMinutes={maxContinuousWorkMinutes}
          requiredBreakMinutes={requiredBreakMinutes}
          onUpdateSchedulingSettings={updateSchedulingSettings}
          savingSchedulingSettings={savingSchedulingSettings}
          onSeedDemoPatients={onSeedDemoPatients}
          onImportPatients={onImportPatients}
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
              workdayStartMinute={calendarStartMinute}
              workdayEndMinute={calendarEndMinute}
              workingDays={workingDays}
              onEventDrop={handleEventDrop}
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
                returnHomeMinutes={returnHomeMinutes}
                patients={patients}
                onEditPatient={(id) => setEditingPatientId(id)}
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
              returnHomeMinutes={returnHomeMinutes}
              patients={patients}
              onEditPatient={(id) => setEditingPatientId(id)}
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
          onToggleLock={async (visitId, locked) => {
            await api.updateVisit(visitId, { clinician_override: locked });
            await onCalendarRefresh();
          }}
        />
      )}

      {createPortal(
        <PatientListSlideOver
          patients={patients}
          visits={visits}
          isOpen={patientListOpen}
          onClose={() => setPatientListOpen(false)}
          onEditPatient={(patientId) => setEditingPatientId(patientId)}
        />,
        document.body,
      )}

      {editingPatientId && createPortal(
        <PatientEditorPanel
          patient={patients.find((p) => p.id === editingPatientId) ?? null}
          onSave={async (patientId, payload) => {
            const prev = patients.find((p) => p.id === patientId);
            const ok = await onUpdatePatient(patientId, payload);
            if (!ok) return false;
            const addressChanged =
              prev && (prev.address_line1 !== payload.address_line1 ||
                prev.city !== payload.city ||
                prev.state !== payload.state ||
                prev.postal_code !== payload.postal_code);
            const schedulingChanged =
              prev && (prev.required_visits_per_week !== payload.required_visits_per_week ||
                prev.visit_duration_minutes !== payload.visit_duration_minutes ||
                prev.min_days_between_visits !== payload.min_days_between_visits ||
                prev.max_days_between_visits !== payload.max_days_between_visits ||
                prev.priority !== payload.priority);
            if (addressChanged || schedulingChanged) {
              setEditorMode("none");
              setSelectedVisitId(null);
              setEditorPosition(null);
              await onOptimize();
            } else {
              await onCalendarRefresh();
            }
            return true;
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

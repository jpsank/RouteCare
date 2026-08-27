import { createPortal } from "react-dom";
import { useMemo, useState, useRef, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { CalendarConnectionsPanel } from "./CalendarConnectionsPanel";
import { PatientImportControl } from "./PatientImportControl";
import type { ClinicianProfile } from "../../types";
import type { ComponentProps } from "react";

const profileSchema = z.object({
  displayName: z
    .string()
    .trim()
    .min(2, "At least 2 characters")
    .max(80, "Too long"),
  latitude: z
    .string()
    .refine(
      (v) => v === "" || (!Number.isNaN(Number(v)) && Math.abs(Number(v)) <= 90),
      "Must be between -90 and 90",
    ),
  longitude: z
    .string()
    .refine(
      (v) => v === "" || (!Number.isNaN(Number(v)) && Math.abs(Number(v)) <= 180),
      "Must be between -180 and 180",
    ),
});
type ProfileForm = z.infer<typeof profileSchema>;

const DAY_LABELS: Array<[number, string]> = [
  [1, "Mon"],
  [2, "Tue"],
  [3, "Wed"],
  [4, "Thu"],
  [5, "Fri"],
  [6, "Sat"],
  [0, "Sun"],
];

type Tab = "general" | "schedule" | "calendar";

type SettingsModalProps = {
  onClose: () => void;
  loading: boolean;
  clinicianProfile: ClinicianProfile | null;
  workdayStartMinute: number;
  workdayEndMinute: number;
  onUpdateWorkdayRange: (start: number, end: number) => Promise<boolean>;
  savingHours: boolean;
  workingDays: number[];
  onToggleWorkingDay: (wday: number) => Promise<boolean>;
  savingWorkingDays: boolean;
  onSaveHomeLocation: (lat: number, lng: number) => Promise<boolean>;
  onDetectCurrentLocation: () => Promise<{ latitude: number; longitude: number } | null>;
  lunchStartMinute: number;
  lunchDurationMinutes: number;
  lunchWindowMinutes: number;
  onUpdateLunch: (start: number, dur: number, win: number) => Promise<boolean>;
  savingLunch: boolean;
  onSaveDisplayName: (name: string) => Promise<boolean>;
  chartingBufferMinutes: number;
  scheduleDensity: number;
  maxDriveMinutesPerDay: number | null;
  maxContinuousWorkMinutes: number;
  requiredBreakMinutes: number;
  onUpdateSchedulingSettings: (settings: Record<string, unknown>) => Promise<boolean>;
  savingSchedulingSettings: boolean;
  onSeedDemoPatients: () => Promise<boolean>;
  onImportPatients: (file: File) => Promise<{ imported: number; errors: string[]; header_map?: Record<string, string> } | undefined>;
  calendarConnectionsProps: ComponentProps<typeof CalendarConnectionsPanel>;
};

const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
  {
    key: "general",
    label: "General",
    icon: (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
      </svg>
    ),
  },
  {
    key: "schedule",
    label: "Schedule",
    icon: (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
      </svg>
    ),
  },
  {
    key: "calendar",
    label: "Calendars",
    icon: (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
      </svg>
    ),
  },
];

// ── Time window grid ─────────────────────────────────────────────────────────
const GRID_FIRST_HOUR = 6;
const GRID_LAST_HOUR = 22;
const GRID_COLS = GRID_LAST_HOUR - GRID_FIRST_HOUR; // 16 cells (6 AM – 10 PM)
const idxToMin = (idx: number) => (GRID_FIRST_HOUR + idx) * 60;
const minToIdx = (min: number) =>
  Math.max(0, Math.min(GRID_COLS, Math.round(min / 60) - GRID_FIRST_HOUR));
const gridHourLabel = (idx: number) => {
  const h = GRID_FIRST_HOUR + idx;
  if (h === 12) return "12p";
  if (h > 12) return `${h - 12}`;
  return `${h}`;
};
const formatGridTime = (min: number) => {
  const h = Math.floor(min / 60);
  if (h === 12) return "12p";
  return h > 12 ? `${h - 12}p` : `${h}a`;
};

function TimeWindowGrid({
  startMinute,
  endMinute,
  disabled = false,
  bare = false,
  onChange,
}: {
  startMinute: number;
  endMinute: number;
  disabled?: boolean;
  bare?: boolean;
  onChange: (start: number, end: number) => void;
}) {
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ anchor: number; tip: number } | null>(null);
  const [dragViz, setDragViz] = useState<{ anchor: number; tip: number } | null>(null);
  const isDragging = dragViz !== null;

  // Optimistic value shown between mouse-up and prop update arriving
  const [optimistic, setOptimistic] = useState<{ start: number; end: number } | null>(null);
  useEffect(() => {
    if (optimistic && optimistic.start === startMinute && optimistic.end === endMinute) {
      setOptimistic(null);
    }
  }, [startMinute, endMinute, optimistic]);

  const baseStart = optimistic?.start ?? startMinute;
  const baseEnd = optimistic?.end ?? endMinute;

  const selStart = isDragging
    ? Math.min(dragViz!.anchor, dragViz!.tip)
    : minToIdx(baseStart);
  const selEnd = isDragging
    ? Math.max(dragViz!.anchor, dragViz!.tip) + 1
    : minToIdx(baseEnd);

  const getIdxFromClientX = (clientX: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return 0;
    return Math.max(0, Math.min(GRID_COLS - 1, Math.floor(((clientX - rect.left) / rect.width) * GRID_COLS)));
  };

  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    dragRef.current = null;
    setDragViz(null);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    if (!d) return;
    const s = Math.min(d.anchor, d.tip);
    const end = Math.max(d.anchor, d.tip) + 1;
    const newStart = idxToMin(s);
    const newEnd = idxToMin(end);
    setOptimistic({ start: newStart, end: newEnd });
    onChangeRef.current(newStart, newEnd);
  };

  const leftPct = (selStart / GRID_COLS) * 100;
  const widthPct = ((selEnd - selStart) / GRID_COLS) * 100;

  const startLabel = formatGridTime(idxToMin(selStart));
  const endLabel = formatGridTime(idxToMin(selEnd));

  return (
    <div
      ref={containerRef}
      role="slider"
      aria-label={`Working hours: ${startLabel} to ${endLabel}`}
      aria-valuetext={`${startLabel} to ${endLabel}`}
      title={disabled ? undefined : `${startLabel} – ${endLabel} (drag to change)`}
      className={[
        "relative flex-1 h-7 overflow-hidden",
        bare ? "bg-gray-100/80" : "rounded-md border border-gray-200 bg-gray-100",
        !disabled ? "cursor-col-resize" : "cursor-default opacity-40",
        !bare && !disabled ? "hover:border-orange-300" : "",
      ].filter(Boolean).join(" ")}
      style={{ userSelect: "none", touchAction: "none" }}
      onPointerDown={(e) => {
        if (disabled) return;
        e.preventDefault();
        const idx = getIdxFromClientX(e.clientX);
        const d = { anchor: idx, tip: idx };
        dragRef.current = d;
        setDragViz(d);
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!dragRef.current) return;
        e.preventDefault();
        const idx = getIdxFromClientX(e.clientX);
        const d = { anchor: dragRef.current.anchor, tip: idx };
        dragRef.current = d;
        setDragViz(d);
      }}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      {/* Hour tick marks */}
      {Array.from({ length: GRID_COLS - 1 }, (_, i) => (
        <div
          key={i}
          className={`absolute inset-y-0 w-px ${(i + 1) % 2 === 0 ? "bg-black/[0.07]" : "bg-black/[0.03]"}`}
          style={{ left: `${((i + 1) / GRID_COLS) * 100}%` }}
        />
      ))}
      {/* Selected range */}
      <div
        className="absolute inset-y-[2px] rounded-[3px] bg-orange-500 shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
        style={{
          left: `${leftPct}%`,
          width: `${widthPct}%`,
          transition: isDragging ? "none" : "left 0.12s ease, width 0.12s ease",
        }}
      />
      {/* Time labels inside bar */}
      {selEnd > selStart && (
        <div
          className="pointer-events-none absolute inset-y-0 flex items-center justify-between overflow-hidden px-1.5"
          style={{
            left: `${leftPct}%`,
            width: `${widthPct}%`,
            transition: isDragging ? "none" : "left 0.12s ease, width 0.12s ease",
          }}
        >
          <span className="shrink-0 text-[9px] font-semibold leading-none text-white drop-shadow-[0_1px_0_rgba(0,0,0,0.15)]">{startLabel}</span>
          {selEnd - selStart >= 3 && (
            <span className="shrink-0 text-[9px] font-semibold leading-none text-white drop-shadow-[0_1px_0_rgba(0,0,0,0.15)]">{endLabel}</span>
          )}
        </div>
      )}
    </div>
  );
}
// ─────────────────────────────────────────────────────────────────────────────

function SectionHeading({ children }: { children: React.ReactNode }) {
  return <h3 className="text-[13px] font-semibold text-gray-900">{children}</h3>;
}

function SectionDescription({ children }: { children: React.ReactNode }) {
  return <p className="mt-0.5 text-[12px] text-gray-400 leading-tight">{children}</p>;
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <span className="rc-label">{children}</span>;
}

export function SettingsModal({
  onClose,
  loading,
  clinicianProfile,
  workdayStartMinute,
  workdayEndMinute,
  onUpdateWorkdayRange,
  savingHours,
  workingDays,
  onToggleWorkingDay,
  savingWorkingDays,
  onSaveHomeLocation,
  onDetectCurrentLocation,
  lunchStartMinute,
  lunchDurationMinutes,
  lunchWindowMinutes,
  onUpdateLunch,
  savingLunch,
  onSaveDisplayName,
  chartingBufferMinutes,
  scheduleDensity,
  maxDriveMinutesPerDay,
  maxContinuousWorkMinutes,
  requiredBreakMinutes,
  onUpdateSchedulingSettings,
  savingSchedulingSettings,
  onSeedDemoPatients,
  onImportPatients,
  calendarConnectionsProps,
}: SettingsModalProps) {
  const [activeTab, setActiveTab] = useState<Tab>("general");
  const [detectingLocation, setDetectingLocation] = useState(false);

  const profileForm = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    mode: "onBlur",
    values: {
      displayName: clinicianProfile?.display_name ?? "",
      latitude: clinicianProfile?.home_latitude != null ? String(clinicianProfile.home_latitude) : "",
      longitude: clinicianProfile?.home_longitude != null ? String(clinicianProfile.home_longitude) : "",
    },
  });

  const submitDisplayName = profileForm.handleSubmit(async (data) => {
    await onSaveDisplayName(data.displayName.trim());
  });

  const submitHomeLocation = profileForm.handleSubmit(async (data) => {
    if (data.latitude === "" || data.longitude === "") return;
    await onSaveHomeLocation(Number(data.latitude), Number(data.longitude));
  });

  const useCurrentLocation = async () => {
    setDetectingLocation(true);
    try {
      const coords = await onDetectCurrentLocation();
      if (coords) {
        profileForm.setValue("latitude", String(coords.latitude), { shouldDirty: false });
        profileForm.setValue("longitude", String(coords.longitude), { shouldDirty: false });
      }
    } finally {
      setDetectingLocation(false);
    }
  };

  const lunchTimeOptions = useMemo(
    () =>
      Array.from({ length: 13 }).map((_, i) => {
        const minute = 660 + i * 15;
        const h = Math.floor(minute / 60);
        const m = minute % 60;
        const meridiem = h >= 12 ? "PM" : "AM";
        const h12 = ((h + 11) % 12) + 1;
        return { value: minute, label: `${h12}:${String(m).padStart(2, "0")} ${meridiem}` };
      }),
    [],
  );

  return createPortal(
    <div
      className="fixed inset-0 z-[800] flex items-start justify-center px-4 pt-[8vh] animate-[fadeIn_0.15s_ease-out] bg-black/25 backdrop-blur-[2px]"
      data-modal-overlay
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-[560px] max-h-[82vh] flex flex-col rounded-2xl bg-white shadow-2xl ring-1 ring-black/5 animate-[scaleIn_0.15s_ease-out]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 pt-4 pb-0">
          <h2 className="text-[15px] font-semibold text-gray-900">Settings</h2>
          <button
            aria-label="Close settings"
            className="rounded-full border-0 bg-gray-100 p-1.5 text-gray-400 shadow-none transition-colors hover:bg-gray-200 hover:text-gray-600"
            onClick={onClose}
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-gray-100 px-5 pt-3 pb-0">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 rounded-lg border-0 px-3 py-1.5 text-[12px] font-medium shadow-none transition-colors ${
                activeTab === tab.key
                  ? "bg-orange-50 text-orange-700"
                  : "bg-transparent text-gray-500 hover:bg-gray-100 hover:text-gray-700"
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4" style={{ scrollbarGutter: "stable" }}>
          {activeTab === "general" && (
            <div className="space-y-4">
              {/* Profile */}
              <section>
                <SectionHeading>Profile</SectionHeading>
                <div className="mt-2 grid grid-cols-2 items-start gap-3">
                  <div className="rc-field">
                    <FieldLabel>Display name</FieldLabel>
                    <form onSubmit={submitDisplayName} className="flex gap-2">
                      <input
                        className="flex-1"
                        {...profileForm.register("displayName")}
                        placeholder="Alex Smith"
                        maxLength={80}
                        aria-invalid={profileForm.formState.errors.displayName ? "true" : "false"}
                      />
                      <button
                        type="submit"
                        className="btn-sm whitespace-nowrap"
                        disabled={loading || profileForm.formState.isSubmitting}
                      >
                        {profileForm.formState.isSubmitting ? "Saving..." : "Save"}
                      </button>
                    </form>
                    {profileForm.formState.errors.displayName && (
                      <p className="text-[11px] text-red-600">
                        {profileForm.formState.errors.displayName.message}
                      </p>
                    )}
                  </div>
                  <div className="rc-field">
                    <FieldLabel>Home location</FieldLabel>
                    <form onSubmit={submitHomeLocation} className="flex items-center gap-2">
                      <input
                        className="w-[80px]"
                        {...profileForm.register("latitude")}
                        placeholder="Lat"
                        aria-invalid={profileForm.formState.errors.latitude ? "true" : "false"}
                      />
                      <input
                        className="w-[80px]"
                        {...profileForm.register("longitude")}
                        placeholder="Lng"
                        aria-invalid={profileForm.formState.errors.longitude ? "true" : "false"}
                      />
                      <button
                        type="submit"
                        className="btn-sm whitespace-nowrap"
                        disabled={loading || profileForm.formState.isSubmitting}
                      >
                        Save
                      </button>
                    </form>
                    {(profileForm.formState.errors.latitude || profileForm.formState.errors.longitude) && (
                      <p className="text-[11px] text-red-600">
                        {profileForm.formState.errors.latitude?.message ||
                          profileForm.formState.errors.longitude?.message}
                      </p>
                    )}
                    <button
                      type="button"
                      className="mt-1 flex items-center gap-1 border-0 bg-transparent p-0 text-[11px] font-medium text-orange-600 shadow-none hover:text-orange-800"
                      onClick={useCurrentLocation}
                      disabled={loading || detectingLocation}
                    >
                      <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 10.5c0 7.142-7.5 11.25-7.5 11.25S4.5 17.642 4.5 10.5a7.5 7.5 0 1 1 15 0Z" />
                      </svg>
                      {detectingLocation ? "Detecting..." : "Use current location"}
                    </button>
                  </div>
                </div>
              </section>

              {/* Working Days & Hours */}
              <section>
                <SectionHeading>Working days & hours</SectionHeading>
                <SectionDescription>Toggle days on/off, then drag a bar to set that day's availability. Unmodified days use the default.</SectionDescription>
                <div className="mt-3">
                  {/* Hour axis */}
                  <div className="mb-1 flex">
                    <div className="w-[52px] shrink-0" />
                    <div className="mx-2 flex-1">
                      <div className="grid" style={{ gridTemplateColumns: `repeat(${GRID_COLS}, 1fr)` }}>
                        {Array.from({ length: GRID_COLS }, (_, i) => {
                          const label = gridHourLabel(i);
                          const isNoon = GRID_FIRST_HOUR + i === 12;
                          return (
                            <div
                              key={i}
                              className={`text-center text-[9px] leading-none ${isNoon ? "font-semibold text-gray-500" : i % 2 === 0 ? "text-gray-400" : "text-gray-300"}`}
                            >
                              {label}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                    <div className="w-8 shrink-0" />
                  </div>

                  {/* Day rows */}
                  <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
                    {DAY_LABELS.map(([wday, label], rowIdx) => {
                      const active = workingDays.includes(wday);
                      const perDay = clinicianProfile?.per_day_hours ?? {};
                      const override = perDay[String(wday)];
                      const dayStart = override?.start ?? workdayStartMinute;
                      const dayEnd = override?.end ?? workdayEndMinute;
                      const hasCustom = active && !!override;
                      return (
                        <div
                          key={`wd-${wday}`}
                          className={`group flex items-center transition-all duration-150 ${rowIdx > 0 ? "border-t border-gray-100" : ""}`}
                        >
                          <button
                            type="button"
                            className={`flex h-10 w-[52px] shrink-0 items-center justify-center gap-1 border-0 border-r border-gray-100 text-[11px] font-semibold shadow-none transition-colors
                              ${active ? "bg-orange-50/80 text-orange-700" : "bg-gray-50 text-gray-400"}`}
                            onClick={() => onToggleWorkingDay(wday)}
                            disabled={loading || savingWorkingDays}
                            title={active ? `${label} is a working day (click to disable)` : `${label} is off (click to enable)`}
                          >
                            {label}
                            {hasCustom && (
                              <span className="inline-block h-1.5 w-1.5 rounded-full bg-orange-400" title="Custom hours" />
                            )}
                          </button>
                          <div className="mx-2 flex flex-1 items-center py-1.5">
                            <TimeWindowGrid
                              bare
                              startMinute={dayStart}
                              endMinute={dayEnd}
                              disabled={!active || loading || savingSchedulingSettings}
                              onChange={(s, e) => {
                                const next = { ...perDay, [String(wday)]: { start: s, end: e } };
                                onUpdateSchedulingSettings({ per_day_hours: next } as Record<string, unknown>);
                              }}
                            />
                          </div>
                          <div className="flex w-8 shrink-0 items-center justify-center">
                            {hasCustom && (
                              <button
                                aria-label="Reset to default hours"
                                className="rounded p-0.5 border-0 bg-transparent text-gray-300 shadow-none transition-colors hover:bg-gray-100 hover:text-gray-600"
                                title="Reset to default hours"
                                onClick={() => {
                                  const next = { ...perDay };
                                  delete next[String(wday)];
                                  onUpdateSchedulingSettings({ per_day_hours: next } as Record<string, unknown>);
                                }}
                              >
                                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3" />
                                </svg>
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}

                    {/* Default row */}
                    <div className="flex items-center border-t-2 border-gray-200 bg-gray-50/60">
                      <span className="flex h-10 w-[52px] shrink-0 items-center justify-center border-r border-gray-100 text-[9px] font-bold tracking-wider text-gray-400 uppercase">
                        Default
                      </span>
                      <div className="mx-2 flex flex-1 items-center py-1.5">
                        <TimeWindowGrid
                          bare
                          startMinute={workdayStartMinute}
                          endMinute={workdayEndMinute}
                          disabled={loading || savingHours}
                          onChange={(s, e) => onUpdateWorkdayRange(s, e)}
                        />
                      </div>
                      <div className="flex w-8 shrink-0 items-center justify-center">
                        {(savingHours || savingSchedulingSettings) && (
                          <svg className="h-3.5 w-3.5 animate-spin text-gray-400" viewBox="0 0 24 24" fill="none">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                          </svg>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </section>

              {/* Import */}
              <section className="rounded-lg border border-dashed border-gray-200 bg-gray-50/50 px-3 py-2 space-y-2">
                <div>
                  <span className="text-[12px] font-medium text-gray-500">Bulk import</span>
                  <p className="text-[11px] text-gray-400">Upload a CSV or Excel file of patients.</p>
                </div>
                <PatientImportControl onImport={onImportPatients} disabled={loading} />
              </section>

              {/* Demo */}
              <section className="rounded-lg border border-dashed border-gray-200 bg-gray-50/50 px-3 py-2">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="text-[12px] font-medium text-gray-500">Demo data</span>
                    <p className="text-[11px] text-gray-400">Populate sample patients for testing.</p>
                  </div>
                  <button className="btn-ghost btn-sm" onClick={onSeedDemoPatients} disabled={loading}>
                    Seed Patients
                  </button>
                </div>
              </section>
            </div>
          )}

          {activeTab === "schedule" && (
            <div className="space-y-6">
              {/* Lunch */}
              <section>
                <SectionHeading>Lunch break</SectionHeading>
                <SectionDescription>When you'd like to take lunch and how flexible the timing can be.</SectionDescription>
                <div className="mt-3 grid grid-cols-3 gap-3">
                  <div className="rc-field">
                    <FieldLabel>Preferred time</FieldLabel>
                    <select
                      value={lunchStartMinute}
                      onChange={(e) => onUpdateLunch(Number(e.target.value), lunchDurationMinutes, lunchWindowMinutes)}
                      disabled={loading || savingLunch}
                    >
                      {lunchTimeOptions.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="rc-field">
                    <FieldLabel>Duration</FieldLabel>
                    <select
                      value={lunchDurationMinutes}
                      onChange={(e) => onUpdateLunch(lunchStartMinute, Number(e.target.value), lunchWindowMinutes)}
                      disabled={loading || savingLunch}
                    >
                      {[0, 15, 30, 45, 60].map((d) => (
                        <option key={d} value={d}>{d === 0 ? "No lunch" : `${d} min`}</option>
                      ))}
                    </select>
                  </div>
                  <div className="rc-field">
                    <FieldLabel>Flexibility</FieldLabel>
                    <select
                      value={lunchWindowMinutes}
                      onChange={(e) => onUpdateLunch(lunchStartMinute, lunchDurationMinutes, Number(e.target.value))}
                      disabled={loading || savingLunch}
                    >
                      {[0, 30, 60, 90, 120, 180].map((w) => (
                        <option key={w} value={w}>{w === 0 ? "Exact time" : `\u00b1${w / 2} min`}</option>
                      ))}
                    </select>
                  </div>
                </div>
                {savingLunch && (
                  <div className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-400">
                    <svg className="h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                    </svg>
                    Saving
                  </div>
                )}
              </section>

              {/* Breaks */}
              <section>
                <SectionHeading>Breaks</SectionHeading>
                <SectionDescription>Mandatory rest after continuous work.</SectionDescription>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <div className="rc-field">
                    <FieldLabel>Break after</FieldLabel>
                    <select
                      value={maxContinuousWorkMinutes}
                      onChange={(e) => onUpdateSchedulingSettings({ max_continuous_work_minutes: Number(e.target.value) })}
                      disabled={loading || savingSchedulingSettings}
                    >
                      {[120, 180, 240, 300, 360, 420, 480].map((m) => (
                        <option key={m} value={m}>{`${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ""}`}</option>
                      ))}
                    </select>
                  </div>
                  <div className="rc-field">
                    <FieldLabel>Break length</FieldLabel>
                    <select
                      value={requiredBreakMinutes}
                      onChange={(e) => onUpdateSchedulingSettings({ required_break_minutes: Number(e.target.value) })}
                      disabled={loading || savingSchedulingSettings}
                    >
                      {[5, 10, 15, 20, 30, 45, 60].map((m) => (
                        <option key={m} value={m}>{m} min</option>
                      ))}
                    </select>
                  </div>
                </div>
              </section>

              {/* Scheduling preferences */}
              <section>
                <SectionHeading>Optimization</SectionHeading>
                <SectionDescription>Fine-tune how the scheduler builds your routes.</SectionDescription>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <div className="rc-field">
                    <FieldLabel>Charting buffer</FieldLabel>
                    <select
                      value={chartingBufferMinutes}
                      onChange={(e) => onUpdateSchedulingSettings({ charting_buffer_minutes: Number(e.target.value) })}
                      disabled={loading || savingSchedulingSettings}
                    >
                      {[0, 5, 10, 15, 20, 30].map((m) => (
                        <option key={m} value={m}>{m === 0 ? "None" : `${m} min`}</option>
                      ))}
                    </select>
                  </div>
                  <div className="rc-field">
                    <FieldLabel>Max drive per day</FieldLabel>
                    <select
                      value={maxDriveMinutesPerDay ?? ""}
                      onChange={(e) => onUpdateSchedulingSettings({ max_drive_minutes_per_day: e.target.value === "" ? null : Number(e.target.value) })}
                      disabled={loading || savingSchedulingSettings}
                    >
                      <option value="">No limit</option>
                      {[60, 90, 120, 150, 180, 240, 300, 360].map((m) => (
                        <option key={m} value={m}>{m >= 60 ? `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ""}` : `${m}m`}</option>
                      ))}
                    </select>
                  </div>
                  <div className="rc-field col-span-2">
                    <FieldLabel>Schedule style</FieldLabel>
                    <select
                      value={scheduleDensity}
                      onChange={(e) => onUpdateSchedulingSettings({ schedule_density: Number(e.target.value) })}
                      disabled={loading || savingSchedulingSettings}
                    >
                      <option value={0}>Spread evenly</option>
                      <option value={0.25}>Slightly packed</option>
                      <option value={0.5}>Balanced</option>
                      <option value={0.75}>Mostly packed</option>
                      <option value={1}>Fewest days</option>
                    </select>
                  </div>
                </div>
                {savingSchedulingSettings && (
                  <div className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-400">
                    <svg className="h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                    </svg>
                    Saving
                  </div>
                )}
              </section>
            </div>
          )}

          {activeTab === "calendar" && (
            <div>
              <SectionHeading>External calendars</SectionHeading>
              <SectionDescription>Sync with Google or Apple Calendar to block off busy times.</SectionDescription>
              <div className="mt-3">
                <CalendarConnectionsPanel {...calendarConnectionsProps} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

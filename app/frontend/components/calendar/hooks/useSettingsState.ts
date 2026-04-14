import { useState } from "react";
import type { ClinicianProfile } from "../../../types";

type SettingsCallbacks = {
  onUpdateWorkingHours: (start: number, end: number) => Promise<boolean>;
  onUpdateWorkingDays: (days: number[]) => Promise<boolean>;
  onUpdateLunchSettings: (start: number, duration: number, window: number) => Promise<boolean>;
  onUpdateDisplayName: (name: string) => Promise<boolean>;
  onUpdateSchedulingSettings: (settings: Record<string, unknown>) => Promise<boolean>;
  onSetHomeFromCurrentLocation: (lat: number, lng: number) => Promise<boolean>;
  onUpdateHomeLocation: (lat: number, lng: number) => Promise<boolean>;
};

export function useSettingsState(
  clinicianProfile: ClinicianProfile | null,
  callbacks: SettingsCallbacks,
) {
  // Derived profile values with defaults
  const defaultStart = clinicianProfile?.workday_start_minute ?? 8 * 60;
  const defaultEnd = clinicianProfile?.workday_end_minute ?? 18 * 60;
  const workdayStartMinute = defaultStart;
  const workdayEndMinute = defaultEnd;

  // Effective calendar range: envelope of default + all per-day overrides
  const perDay = clinicianProfile?.per_day_hours ?? {};
  const overrideStarts = Object.values(perDay).map((v) => v.start);
  const overrideEnds = Object.values(perDay).map((v) => v.end);
  const calendarStartMinute = overrideStarts.length
    ? Math.min(defaultStart, ...overrideStarts)
    : defaultStart;
  const calendarEndMinute = overrideEnds.length
    ? Math.max(defaultEnd, ...overrideEnds)
    : defaultEnd;
  const workingDays = clinicianProfile?.working_days?.length ? clinicianProfile.working_days : [1, 2, 3, 4, 5];
  const lunchStartMinute = clinicianProfile?.lunch_start_minute ?? 720;
  const lunchDurationMinutes = clinicianProfile?.lunch_duration_minutes ?? 30;
  const lunchWindowMinutes = clinicianProfile?.lunch_window_minutes ?? 90;
  const chartingBufferMinutes = clinicianProfile?.charting_buffer_minutes ?? 0;
  const scheduleDensity = clinicianProfile?.schedule_density ?? 0.5;
  const maxDriveMinutesPerDay = clinicianProfile?.max_drive_minutes_per_day ?? null;
  const maxContinuousWorkMinutes = clinicianProfile?.max_continuous_work_minutes ?? 480;
  const requiredBreakMinutes = clinicianProfile?.required_break_minutes ?? 15;

  // Saving flags for auto-saved sections (not RHF-managed)
  const [savingHours, setSavingHours] = useState(false);
  const [savingWorkingDays, setSavingWorkingDays] = useState(false);
  const [savingLunch, setSavingLunch] = useState(false);
  const [savingSchedulingSettings, setSavingSchedulingSettings] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const updateWorkdayRange = async (nextStart: number, nextEnd: number): Promise<boolean> => {
    if (nextEnd <= nextStart) return false;
    setSavingHours(true);
    try {
      return await callbacks.onUpdateWorkingHours(nextStart, nextEnd);
    } finally {
      setSavingHours(false);
    }
  };

  const toggleWorkingDay = async (wday: number): Promise<boolean> => {
    const next = workingDays.includes(wday) ? workingDays.filter((d) => d !== wday) : [...workingDays, wday].sort((a, b) => a - b);
    if (next.length === 0) return false;
    setSavingWorkingDays(true);
    try {
      return await callbacks.onUpdateWorkingDays(next);
    } finally {
      setSavingWorkingDays(false);
    }
  };

  const updateLunch = async (startMin: number, duration: number, window: number): Promise<boolean> => {
    setSavingLunch(true);
    try {
      return await callbacks.onUpdateLunchSettings(startMin, duration, window);
    } finally {
      setSavingLunch(false);
    }
  };

  const updateSchedulingSettings = async (settings: Record<string, unknown>): Promise<boolean> => {
    setSavingSchedulingSettings(true);
    try {
      return await callbacks.onUpdateSchedulingSettings(settings);
    } finally {
      setSavingSchedulingSettings(false);
    }
  };

  // RHF-owned form actions (called from SettingsModal's submit handlers).
  // These just forward to the clinician-profile callbacks — the form owns the
  // values and "saving" state (via RHF's isSubmitting).
  const saveDisplayName = (name: string) => callbacks.onUpdateDisplayName(name);
  const saveHomeLocation = (lat: number, lng: number) => callbacks.onUpdateHomeLocation(lat, lng);

  // Geolocation helper returns coordinates so the caller (RHF form) can
  // populate its own fields, then save.
  const detectCurrentLocation = async (): Promise<{ latitude: number; longitude: number } | null> => {
    try {
      const position = await new Promise<GeolocationPosition>((resolve, reject) =>
        navigator.geolocation.getCurrentPosition(resolve, reject),
      );
      await callbacks.onSetHomeFromCurrentLocation(position.coords.latitude, position.coords.longitude);
      return { latitude: position.coords.latitude, longitude: position.coords.longitude };
    } catch {
      return null;
    }
  };

  return {
    // Derived values
    workdayStartMinute,
    workdayEndMinute,
    calendarStartMinute,
    calendarEndMinute,
    workingDays,
    lunchStartMinute,
    lunchDurationMinutes,
    lunchWindowMinutes,
    chartingBufferMinutes,
    scheduleDensity,
    maxDriveMinutesPerDay,
    maxContinuousWorkMinutes,
    requiredBreakMinutes,
    // Saving flags
    savingHours,
    savingWorkingDays,
    savingLunch,
    savingSchedulingSettings,
    // Settings modal toggle
    showSettings,
    setShowSettings,
    // Actions
    updateWorkdayRange,
    toggleWorkingDay,
    updateLunch,
    updateSchedulingSettings,
    saveDisplayName,
    saveHomeLocation,
    detectCurrentLocation,
  };
}

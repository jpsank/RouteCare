import { useEffect, useState } from "react";
import type { ClinicianProfile } from "../../../types";

type SettingsCallbacks = {
  onUpdateWorkingHours: (start: number, end: number) => Promise<void>;
  onUpdateWorkingDays: (days: number[]) => Promise<void>;
  onUpdateLunchSettings: (start: number, duration: number, window: number) => Promise<void>;
  onUpdateDisplayName: (name: string) => Promise<void>;
  onUpdateSchedulingSettings: (settings: Record<string, unknown>) => Promise<void>;
  onSetHomeFromCurrentLocation: (lat: number, lng: number) => Promise<void>;
  onUpdateHomeLocation: (lat: number, lng: number) => Promise<void>;
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

  // Input state synced to profile
  const [homeLatitudeInput, setHomeLatitudeInput] = useState("");
  const [homeLongitudeInput, setHomeLongitudeInput] = useState("");
  const [displayNameInput, setDisplayNameInput] = useState("");

  useEffect(() => {
    setHomeLatitudeInput(clinicianProfile?.home_latitude != null ? String(clinicianProfile.home_latitude) : "");
    setHomeLongitudeInput(clinicianProfile?.home_longitude != null ? String(clinicianProfile.home_longitude) : "");
  }, [clinicianProfile?.home_latitude, clinicianProfile?.home_longitude]);

  useEffect(() => {
    setDisplayNameInput(clinicianProfile?.display_name ?? "");
  }, [clinicianProfile?.display_name]);

  // Saving flags
  const [savingHours, setSavingHours] = useState(false);
  const [savingWorkingDays, setSavingWorkingDays] = useState(false);
  const [savingHomeLocation, setSavingHomeLocation] = useState(false);
  const [savingHomeInput, setSavingHomeInput] = useState(false);
  const [savingLunch, setSavingLunch] = useState(false);
  const [savingSchedulingSettings, setSavingSchedulingSettings] = useState(false);
  const [savingDisplayName, setSavingDisplayName] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  // Wrapped async handlers
  const updateWorkdayRange = async (nextStart: number, nextEnd: number) => {
    if (nextEnd <= nextStart) return;
    setSavingHours(true);
    try {
      await callbacks.onUpdateWorkingHours(nextStart, nextEnd);
    } finally {
      setSavingHours(false);
    }
  };

  const toggleWorkingDay = async (wday: number) => {
    const next = workingDays.includes(wday) ? workingDays.filter((d) => d !== wday) : [...workingDays, wday].sort((a, b) => a - b);
    if (next.length === 0) return;
    setSavingWorkingDays(true);
    try {
      await callbacks.onUpdateWorkingDays(next);
    } finally {
      setSavingWorkingDays(false);
    }
  };

  const updateLunch = async (startMin: number, duration: number, window: number) => {
    setSavingLunch(true);
    try {
      await callbacks.onUpdateLunchSettings(startMin, duration, window);
    } finally {
      setSavingLunch(false);
    }
  };

  const updateSchedulingSettings = async (settings: Record<string, unknown>) => {
    setSavingSchedulingSettings(true);
    try {
      await callbacks.onUpdateSchedulingSettings(settings);
    } finally {
      setSavingSchedulingSettings(false);
    }
  };

  const saveDisplayName = async () => {
    setSavingDisplayName(true);
    try {
      await callbacks.onUpdateDisplayName(displayNameInput.trim());
    } finally {
      setSavingDisplayName(false);
    }
  };

  const saveHomeFromCurrentLocation = async () => {
    const currentPosition = await new Promise<GeolocationPosition>((resolve, reject) =>
      navigator.geolocation.getCurrentPosition(resolve, reject),
    );
    setSavingHomeLocation(true);
    try {
      setHomeLatitudeInput(String(currentPosition.coords.latitude));
      setHomeLongitudeInput(String(currentPosition.coords.longitude));
      await callbacks.onSetHomeFromCurrentLocation(currentPosition.coords.latitude, currentPosition.coords.longitude);
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
      await callbacks.onUpdateHomeLocation(latitude, longitude);
    } finally {
      setSavingHomeInput(false);
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
    // Input state
    homeLatitudeInput,
    setHomeLatitudeInput,
    homeLongitudeInput,
    setHomeLongitudeInput,
    displayNameInput,
    setDisplayNameInput,
    // Saving flags
    savingHours,
    savingWorkingDays,
    savingHomeLocation,
    savingHomeInput,
    savingLunch,
    savingSchedulingSettings,
    savingDisplayName,
    // Settings modal toggle
    showSettings,
    setShowSettings,
    // Actions
    updateWorkdayRange,
    toggleWorkingDay,
    updateLunch,
    updateSchedulingSettings,
    saveDisplayName,
    saveHomeFromCurrentLocation,
    saveHomeFromInputs,
  };
}

import { useMemo } from "react";
import type { ComponentProps } from "react";
import { CalendarConnectionsPanel } from "./CalendarConnectionsPanel";

type Props = {
  loading: boolean;
  workdayStartMinute: number;
  workdayEndMinute: number;
  onUpdateWorkdayRange: (nextStartMinute: number, nextEndMinute: number) => void;
  savingHours: boolean;
  homeLatitudeInput: string;
  homeLongitudeInput: string;
  onHomeLatitudeInputChange: (value: string) => void;
  onHomeLongitudeInputChange: (value: string) => void;
  onSaveHomeFromInputs: () => void;
  onSaveHomeFromCurrentLocation: () => void;
  savingHomeInput: boolean;
  savingHomeLocation: boolean;
  onSeedDemoPatients: () => void;
  lunchStartMinute: number;
  lunchDurationMinutes: number;
  lunchWindowMinutes: number;
  onUpdateLunch: (startMinute: number, durationMinutes: number, windowMinutes: number) => void;
  savingLunch: boolean;
  displayNameInput: string;
  onDisplayNameInputChange: (value: string) => void;
  onSaveDisplayName: () => void;
  savingDisplayName: boolean;
  chartingBufferMinutes: number;
  scheduleDensity: number;
  maxDriveMinutesPerDay: number | null;
  maxContinuousWorkMinutes: number;
  requiredBreakMinutes: number;
  onUpdateSchedulingSettings: (settings: {
    charting_buffer_minutes?: number;
    schedule_density?: number;
    max_drive_minutes_per_day?: number | null;
    max_continuous_work_minutes?: number;
    required_break_minutes?: number;
  }) => void;
  savingSchedulingSettings: boolean;
  calendarConnectionsProps: ComponentProps<typeof CalendarConnectionsPanel>;
};

export function WeeklySettingsPanel({
  loading,
  workdayStartMinute,
  workdayEndMinute,
  onUpdateWorkdayRange,
  savingHours,
  homeLatitudeInput,
  homeLongitudeInput,
  onHomeLatitudeInputChange,
  onHomeLongitudeInputChange,
  onSaveHomeFromInputs,
  onSaveHomeFromCurrentLocation,
  savingHomeInput,
  savingHomeLocation,
  onSeedDemoPatients,
  lunchStartMinute,
  lunchDurationMinutes,
  lunchWindowMinutes,
  onUpdateLunch,
  savingLunch,
  displayNameInput,
  onDisplayNameInputChange,
  onSaveDisplayName,
  savingDisplayName,
  chartingBufferMinutes,
  scheduleDensity,
  maxDriveMinutesPerDay,
  maxContinuousWorkMinutes,
  requiredBreakMinutes,
  onUpdateSchedulingSettings,
  savingSchedulingSettings,
  calendarConnectionsProps,
}: Props) {
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

  return (
    <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="rc-field">
          <span className="rc-label">Your name</span>
          <input
            className="w-48"
            value={displayNameInput}
            onChange={(e) => onDisplayNameInputChange(e.target.value)}
            placeholder="Alex Smith"
            maxLength={80}
          />
        </div>
        <button className="btn-sm" onClick={onSaveDisplayName} disabled={loading || savingDisplayName}>
          {savingDisplayName ? "Saving..." : "Save Name"}
        </button>
        <div className="rc-field">
          <span className="rc-label">Day starts</span>
          <select
            className="w-32"
            value={workdayStartMinute}
            onChange={(event) => onUpdateWorkdayRange(Number(event.target.value), workdayEndMinute)}
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
            className="w-32"
            value={workdayEndMinute}
            onChange={(event) => onUpdateWorkdayRange(workdayStartMinute, Number(event.target.value))}
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
            className="w-20"
            value={homeLatitudeInput}
            onChange={(e) => onHomeLatitudeInputChange(e.target.value)}
            placeholder="36.18"
          />
        </div>
        <div className="rc-field">
          <span className="rc-label">Home lng</span>
          <input
            className="w-20"
            value={homeLongitudeInput}
            onChange={(e) => onHomeLongitudeInputChange(e.target.value)}
            placeholder="-94.13"
          />
        </div>
        <button className="btn-sm" onClick={onSaveHomeFromInputs} disabled={loading || savingHomeInput}>
          {savingHomeInput ? "Saving..." : "Save Home"}
        </button>
        <button className="btn-sm" onClick={onSaveHomeFromCurrentLocation} disabled={loading || savingHomeLocation}>
          {savingHomeLocation ? "Saving..." : "Use Current Location"}
        </button>
        <button className="btn-ghost btn-sm" onClick={onSeedDemoPatients} disabled={loading}>
          Demo Patients
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-3 border-t border-gray-200 pt-2.5">
        <div className="rc-field">
          <span className="rc-label">Lunch at</span>
          <select
            className="w-28"
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
          <span className="rc-label">Duration</span>
          <select
            className="w-20"
            value={lunchDurationMinutes}
            onChange={(e) => onUpdateLunch(lunchStartMinute, Number(e.target.value), lunchWindowMinutes)}
            disabled={loading || savingLunch}
          >
            {[15, 30, 45, 60].map((d) => (
              <option key={d} value={d}>{d} min</option>
            ))}
          </select>
        </div>
        <div className="rc-field">
          <span className="rc-label">Flex window</span>
          <select
            className="w-20"
            value={lunchWindowMinutes}
            onChange={(e) => onUpdateLunch(lunchStartMinute, lunchDurationMinutes, Number(e.target.value))}
            disabled={loading || savingLunch}
          >
            {[0, 30, 60, 90, 120, 180].map((w) => (
              <option key={w} value={w}>{w === 0 ? "Fixed" : `±${w / 2}m`}</option>
            ))}
          </select>
        </div>
        {savingLunch && <span className="text-xs text-gray-400">Saving...</span>}
      </div>

      <div className="flex flex-wrap items-end gap-3 border-t border-gray-200 pt-2.5">
        <div className="rc-field">
          <span className="rc-label">Charting buffer</span>
          <select
            className="w-20"
            value={chartingBufferMinutes}
            onChange={(e) => onUpdateSchedulingSettings({ charting_buffer_minutes: Number(e.target.value) })}
            disabled={loading || savingSchedulingSettings}
          >
            {[0, 5, 10, 15, 20, 30].map((m) => (
              <option key={m} value={m}>{m} min</option>
            ))}
          </select>
        </div>
        <div className="rc-field">
          <span className="rc-label">Max drive/day</span>
          <select
            className="w-24"
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
        <div className="rc-field">
          <span className="rc-label">Break after</span>
          <select
            className="w-20"
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
          <span className="rc-label">Break length</span>
          <select
            className="w-20"
            value={requiredBreakMinutes}
            onChange={(e) => onUpdateSchedulingSettings({ required_break_minutes: Number(e.target.value) })}
            disabled={loading || savingSchedulingSettings}
          >
            {[5, 10, 15, 20, 30, 45, 60].map((m) => (
              <option key={m} value={m}>{m} min</option>
            ))}
          </select>
        </div>
        <div className="rc-field">
          <span className="rc-label">Schedule style</span>
          <select
            className="w-28"
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
        {savingSchedulingSettings && <span className="text-xs text-gray-400">Saving...</span>}
      </div>

      <CalendarConnectionsPanel {...calendarConnectionsProps} />
    </div>
  );
}

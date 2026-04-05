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

      <CalendarConnectionsPanel {...calendarConnectionsProps} />
    </div>
  );
}

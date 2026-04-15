import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { api } from "../lib/api";
import type { ClinicianProfile } from "../types";

const nameSchema = z.object({
  displayName: z
    .string()
    .trim()
    .min(2, "Please enter at least 2 characters")
    .max(80, "Name is too long"),
});
type NameForm = z.infer<typeof nameSchema>;

type Props = {
  clinicianProfile: ClinicianProfile | null;
  onComplete: () => void;
  onSeedDemo: () => Promise<boolean>;
};

const DAY_LABELS: Array<[number, string]> = [
  [1, "Mon"],
  [2, "Tue"],
  [3, "Wed"],
  [4, "Thu"],
  [5, "Fri"],
  [6, "Sat"],
  [0, "Sun"],
];

const HOUR_OPTIONS = Array.from({ length: 15 }, (_, i) => {
  const hour = i + 5;
  const label = hour <= 12 ? `${hour} AM` : `${hour - 12} PM`;
  return { value: hour * 60, label };
});

export function SetupWizard({ clinicianProfile, onComplete, onSeedDemo }: Props) {
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [locating, setLocating] = useState(false);
  const [locationSet, setLocationSet] = useState(false);
  const [workdayStart, setWorkdayStart] = useState(clinicianProfile?.workday_start_minute ?? 480);
  const [workdayEnd, setWorkdayEnd] = useState(clinicianProfile?.workday_end_minute ?? 1080);
  const [workingDays, setWorkingDays] = useState<number[]>(clinicianProfile?.working_days ?? [1, 2, 3, 4, 5]);
  const [loadingDemo, setLoadingDemo] = useState(false);

  const nameForm = useForm<NameForm>({
    resolver: zodResolver(nameSchema),
    defaultValues: { displayName: clinicianProfile?.display_name ?? "" },
    mode: "onBlur",
  });

  const saveAndNext = async () => {
    if (step === 0) {
      const valid = await nameForm.trigger();
      if (!valid) return;
    }
    setSaving(true);
    try {
      if (step === 0) {
        await api.updateClinicianProfile({ display_name: nameForm.getValues("displayName").trim() });
      } else if (step === 1) {
        await api.updateClinicianProfile({
          workday_start_minute: workdayStart,
          workday_end_minute: workdayEnd,
          working_days: workingDays,
        });
      }
      if (step < 2) {
        setStep(step + 1);
      } else {
        await api.updateClinicianProfile({ setup_completed_at: new Date().toISOString() });
        onComplete();
      }
    } finally {
      setSaving(false);
    }
  };

  const useMyLocation = async () => {
    setLocating(true);
    try {
      const position = await new Promise<GeolocationPosition>((resolve, reject) =>
        navigator.geolocation.getCurrentPosition(resolve, reject),
      );
      await api.updateClinicianProfile({
        home_latitude: position.coords.latitude,
        home_longitude: position.coords.longitude,
      });
      setLocationSet(true);
    } catch {
      // User denied geolocation — that's fine, skip
    } finally {
      setLocating(false);
    }
  };

  const loadDemo = async () => {
    setLoadingDemo(true);
    try {
      const ok = await onSeedDemo();
      if (!ok) return;
      await api.updateClinicianProfile({ setup_completed_at: new Date().toISOString() });
      onComplete();
    } finally {
      setLoadingDemo(false);
    }
  };

  return (
    <div className="flex min-h-[80vh] items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Progress */}
        <div className="mb-8 flex items-center justify-center gap-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className={`h-1.5 w-12 rounded-full transition-colors ${
                i <= step ? "bg-orange-500" : "bg-gray-200"
              }`}
            />
          ))}
        </div>

        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          {step === 0 && (
            <div className="space-y-5">
              <div className="text-center">
                <p className="text-sm font-semibold uppercase tracking-widest text-orange-600">Welcome to RouteCare</p>
                <h1 className="mt-2 text-xl font-bold tracking-tight text-gray-900">Let's get you set up</h1>
                <p className="mt-1 text-sm text-gray-500">This takes about 30 seconds.</p>
              </div>

              <div className="rc-field">
                <span className="rc-label">Your name</span>
                <input
                  {...nameForm.register("displayName")}
                  placeholder="Dr. Jane Smith"
                  aria-invalid={nameForm.formState.errors.displayName ? "true" : "false"}
                />
                {nameForm.formState.errors.displayName && (
                  <p className="text-xs text-red-600">
                    {nameForm.formState.errors.displayName.message}
                  </p>
                )}
              </div>

              <div className="rc-field">
                <span className="rc-label">Home / starting location</span>
                <button
                  className={`w-full ${locationSet ? "btn-secondary" : "btn-primary"}`}
                  onClick={useMyLocation}
                  disabled={locating}
                >
                  {locating ? "Detecting..." : locationSet ? "Location saved" : "Use My Current Location"}
                </button>
                <p className="text-xs text-gray-400">Used to optimize your daily driving routes.</p>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-5">
              <div className="text-center">
                <h2 className="text-xl font-bold tracking-tight text-gray-900">Your work schedule</h2>
                <p className="mt-1 text-sm text-gray-500">When do you see patients?</p>
              </div>

              <div className="rc-field">
                <span className="rc-label">Working days</span>
                <div className="flex flex-wrap gap-1.5">
                  {DAY_LABELS.map(([wday, label]) => (
                    <button
                      key={wday}
                      className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                        workingDays.includes(wday)
                          ? "bg-orange-100 text-orange-700 border-orange-200"
                          : "bg-gray-50 text-gray-400 border-gray-200"
                      }`}
                      onClick={() => {
                        setWorkingDays((prev) =>
                          prev.includes(wday) ? prev.filter((d) => d !== wday) : [...prev, wday].sort(),
                        );
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="rc-field">
                  <span className="rc-label">Start time</span>
                  <select value={workdayStart} onChange={(e) => setWorkdayStart(Number(e.target.value))}>
                    {HOUR_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>
                <div className="rc-field">
                  <span className="rc-label">End time</span>
                  <select value={workdayEnd} onChange={(e) => setWorkdayEnd(Number(e.target.value))}>
                    {HOUR_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-5">
              <div className="text-center">
                <h2 className="text-xl font-bold tracking-tight text-gray-900">Add your patients</h2>
                <p className="mt-1 text-sm text-gray-500">You can add patients now or later from the calendar.</p>
              </div>

              <div className="space-y-3">
                <button
                  className="btn-primary w-full justify-center"
                  onClick={loadDemo}
                  disabled={loadingDemo || saving}
                >
                  {loadingDemo ? "Setting up..." : "Load demo patients & optimize"}
                </button>
                <button
                  className="btn-secondary w-full justify-center"
                  onClick={saveAndNext}
                  disabled={saving}
                >
                  {saving ? "Finishing..." : "I'll add my own patients"}
                </button>
              </div>

              <p className="text-center text-xs text-gray-400">
                You can always load demo patients later from Settings.
              </p>
            </div>
          )}

          {step < 2 && (
            <div className="mt-5 flex items-center justify-between">
              {step > 0 ? (
                <button className="btn-ghost btn-sm" onClick={() => setStep(step - 1)}>Back</button>
              ) : (
                <div />
              )}
              <button className="btn-primary" onClick={saveAndNext} disabled={saving}>
                {saving ? "Saving..." : "Continue"}
              </button>
            </div>
          )}
        </div>

        <button
          className="mt-4 w-full text-center text-xs text-gray-400 hover:text-gray-600 border-0 bg-transparent shadow-none"
          onClick={async () => {
            await api.updateClinicianProfile({ setup_completed_at: new Date().toISOString() });
            onComplete();
          }}
        >
          Skip setup
        </button>
      </div>
    </div>
  );
}

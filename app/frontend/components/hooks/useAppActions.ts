import { useCallback } from "react";
import { api } from "../../lib/api";
import type { Alert, CalendarBlock, ClinicianProfile, Message, Patient, WeeklySchedule } from "../../types";
import type { PatientSavePayload } from "../calendar/utils";

type Setters = {
  setSchedule: React.Dispatch<React.SetStateAction<WeeklySchedule | null>>;
  setPatients: React.Dispatch<React.SetStateAction<Patient[]>>;
  setMessages: React.Dispatch<React.SetStateAction<Message[] | null>>;
  setAlerts: React.Dispatch<React.SetStateAction<Alert[] | null>>;
  setCalendarBlocks: React.Dispatch<React.SetStateAction<CalendarBlock[]>>;
  setClinicianProfile: React.Dispatch<React.SetStateAction<ClinicianProfile | null>>;
  setError: React.Dispatch<React.SetStateAction<string | null>>;
};

type LoadingControls = {
  startLoading: () => void;
  stopLoading: () => void;
};

/**
 * Wraps an async action with loading/error boilerplate.
 */
function useWrappedAction(
  { startLoading, stopLoading }: LoadingControls,
  setError: React.Dispatch<React.SetStateAction<string | null>>,
) {
  return useCallback(
    <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
      startLoading();
      setError(null);
      return fn()
        .catch((err: Error) => {
          setError(err.message);
          return undefined;
        })
        .finally(() => stopLoading());
    },
    [startLoading, stopLoading, setError],
  );
}

export function useAppActions(setters: Setters, loading: LoadingControls) {
  const {
    setSchedule,
    setPatients,
    setMessages,
    setAlerts,
    setCalendarBlocks,
    setClinicianProfile,
    setError,
  } = setters;

  const wrap = useWrappedAction(loading, setError);

  const optimizeAndRefreshBlocks = useCallback(async (start?: { latitude: number; longitude: number }) => {
    const optimized = await api.optimizeSchedule(undefined, start?.latitude, start?.longitude);
    setSchedule(optimized.schedule);
    const refreshedBlocks = await api.listCalendarBlocks();
    setCalendarBlocks(refreshedBlocks.calendar_blocks);
  }, [setSchedule, setCalendarBlocks]);

  /** Update clinician profile then re-optimize. Used by most settings handlers. */
  const updateProfileAndReoptimize = useCallback(
    (profileFields: Parameters<typeof api.updateClinicianProfile>[0]) =>
      wrap(async () => {
        const response = await api.updateClinicianProfile(profileFields);
        setClinicianProfile(response.clinician_profile);
        await optimizeAndRefreshBlocks();
      }),
    [wrap, setClinicianProfile, optimizeAndRefreshBlocks],
  );

  const optimizeSchedule = useCallback(
    (start?: { latitude: number; longitude: number }) =>
      wrap(() => optimizeAndRefreshBlocks(start)),
    [wrap, optimizeAndRefreshBlocks],
  );

  const createPatient = useCallback(
    (payload: PatientSavePayload) =>
      wrap(async () => {
        const created = await api.createPatient(payload);
        setPatients((prev) => [created.patient, ...prev]);
      }),
    [wrap, setPatients],
  );

  const updatePatient = useCallback(
    (patientId: number, payload: PatientSavePayload) =>
      wrap(async () => {
        const updated = await api.updatePatient(patientId, payload);
        setPatients((prev) => prev.map((p) => (p.id === patientId ? updated.patient : p)));
      }),
    [wrap, setPatients],
  );

  const seedDemoPatients = useCallback(
    () =>
      wrap(async () => {
        const response = await api.seedDemoPatients();
        setPatients(response.patients);
        await optimizeAndRefreshBlocks();
      }),
    [wrap, setPatients, optimizeAndRefreshBlocks],
  );

  const updateWorkingHours = useCallback(
    (workdayStartMinute: number, workdayEndMinute: number) =>
      updateProfileAndReoptimize({ workday_start_minute: workdayStartMinute, workday_end_minute: workdayEndMinute }),
    [updateProfileAndReoptimize],
  );

  const updateLunchSettings = useCallback(
    (lunchStartMinute: number, lunchDurationMinutes: number, lunchWindowMinutes: number) =>
      updateProfileAndReoptimize({
        lunch_start_minute: lunchStartMinute,
        lunch_duration_minutes: lunchDurationMinutes,
        lunch_window_minutes: lunchWindowMinutes,
      }),
    [updateProfileAndReoptimize],
  );

  const updateWorkingDays = useCallback(
    (workingDays: number[]) =>
      updateProfileAndReoptimize({ working_days: workingDays }),
    [updateProfileAndReoptimize],
  );

  const updateHomeLocation = useCallback(
    (latitude: number, longitude: number) =>
      updateProfileAndReoptimize({ home_latitude: latitude, home_longitude: longitude }),
    [updateProfileAndReoptimize],
  );

  const updateDisplayName = useCallback(
    (displayName: string) =>
      wrap(async () => {
        const response = await api.updateClinicianProfile({ display_name: displayName });
        setClinicianProfile(response.clinician_profile);
      }),
    [wrap, setClinicianProfile],
  );

  const updateSchedulingSettings = useCallback(
    (settings: Record<string, unknown>) =>
      updateProfileAndReoptimize(settings as Parameters<typeof api.updateClinicianProfile>[0]),
    [updateProfileAndReoptimize],
  );

  const sendMessage = useCallback(
    (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) =>
      wrap(async () => {
        const result = await api.createMessage(visitId, channel, body, sendImmediately);
        setMessages((prev) => [result.message, ...(prev ?? [])]);
        return result.message;
      }),
    [wrap, setMessages],
  );

  const bulkConfirmMessages = useCallback(
    () =>
      wrap(async () => {
        const result = await api.bulkConfirmMessages();
        setMessages((prev) => [...result.messages, ...(prev ?? [])]);
        return result;
      }),
    [wrap, setMessages],
  );

  const approveMessage = useCallback(
    (messageId: number) =>
      wrap(async () => {
        const result = await api.approveMessage(messageId);
        setMessages((prev) => (prev ?? []).map((m) => (m.id === messageId ? result.message : m)));
        return result.message;
      }),
    [wrap, setMessages],
  );

  const selectMessageSuggestion = useCallback(
    (messageId: number, suggestionIndex: number) =>
      wrap(async () => {
        const result = await api.selectMessageSuggestion(messageId, suggestionIndex);
        setMessages((prev) => (prev ?? []).map((m) => (m.id === messageId ? result.message : m)));
        return result.message;
      }),
    [wrap, setMessages],
  );

  const executeAlertAction = useCallback(
    (alertId: number) =>
      wrap(async () => {
        const result = await api.executeAlertAction(alertId);
        setAlerts((prev) => (prev ?? []).map((a) => (a.id === alertId ? result.alert : a)));
        if (result.message) {
          setMessages((prev) => [result.message!, ...(prev ?? [])]);
        }
      }),
    [wrap, setAlerts, setMessages],
  );

  const updateAlert = useCallback(
    (alertId: number, status: string) =>
      wrap(async () => {
        const result = await api.updateAlert(alertId, status);
        setAlerts((prev) => (prev ?? []).map((a) => (a.id === alertId ? result.alert : a)));
      }),
    [wrap, setAlerts],
  );

  return {
    optimizeSchedule,
    createPatient,
    updatePatient,
    seedDemoPatients,
    updateWorkingHours,
    updateLunchSettings,
    updateWorkingDays,
    updateHomeLocation,
    updateDisplayName,
    updateSchedulingSettings,
    sendMessage,
    bulkConfirmMessages,
    approveMessage,
    selectMessageSuggestion,
    executeAlertAction,
    updateAlert,
  };
}

import { useCallback } from "react";
import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { queryKeys } from "../../lib/queries";
import type {
  Alert,
  CalendarBlock,
  ClinicianProfile,
  Message,
  Patient,
  WeeklySchedule,
} from "../../types";
import type { PatientSavePayload } from "../calendar/utils";

type ProfileUpdate = Parameters<typeof api.updateClinicianProfile>[0];

type OptimizeArgs = { latitude: number; longitude: number } | undefined;

// Global mutationCache onError surfaces failures via toast; these helpers
// let form callers await the action without a try/catch while still being
// able to distinguish success from failure before advancing UI state.
async function tryRun<T>(promise: Promise<T>): Promise<T | undefined> {
  try {
    return await promise;
  } catch {
    return undefined;
  }
}

async function tryRunBool(promise: Promise<unknown>): Promise<boolean> {
  try {
    await promise;
    return true;
  } catch {
    return false;
  }
}

export function useAppActions() {
  const queryClient = useQueryClient();

  const setCache = useCallback(
    <T,>(key: QueryKey, value: T) => queryClient.setQueryData<T>(key, value),
    [queryClient],
  );

  const updateCollection = useCallback(
    <T,>(key: QueryKey, updater: (prev: T[]) => T[]) =>
      queryClient.setQueryData<T[]>(key, (prev) => updater(prev ?? [])),
    [queryClient],
  );

  const optimizeAndRefreshBlocks = useCallback(
    async (start?: { latitude: number; longitude: number }) => {
      const optimized = await api.optimizeSchedule(undefined, start?.latitude, start?.longitude);
      setCache<WeeklySchedule | null>(queryKeys.schedule, optimized.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCache<CalendarBlock[]>(queryKeys.calendarBlocks, refreshedBlocks.calendar_blocks);
    },
    [setCache],
  );

  const optimizeScheduleMutation = useMutation({
    mutationFn: (start: OptimizeArgs) => optimizeAndRefreshBlocks(start),
  });

  const updateProfileAndReoptimizeMutation = useMutation({
    mutationFn: async (profileFields: ProfileUpdate) => {
      const response = await api.updateClinicianProfile(profileFields);
      setCache<ClinicianProfile>(queryKeys.clinicianProfile, response.clinician_profile);
      await optimizeAndRefreshBlocks();
    },
  });

  const createPatientMutation = useMutation({
    mutationFn: (payload: PatientSavePayload) => api.createPatient(payload),
    onSuccess: (data) =>
      updateCollection<Patient>(queryKeys.patients, (prev) => [data.patient, ...prev]),
  });

  const updatePatientMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: PatientSavePayload }) =>
      api.updatePatient(id, payload),
    onSuccess: (data, { id }) =>
      updateCollection<Patient>(queryKeys.patients, (prev) =>
        prev.map((p) => (p.id === id ? data.patient : p)),
      ),
  });

  const seedDemoPatientsMutation = useMutation({
    mutationFn: async () => {
      const response = await api.seedDemoPatients();
      setCache<Patient[]>(queryKeys.patients, response.patients);
      await optimizeAndRefreshBlocks();
    },
  });

  const importPatientsMutation = useMutation({
    mutationFn: async (file: File) => {
      const response = await api.importPatients(file);
      setCache<Patient[]>(queryKeys.patients, response.patients);
      return { imported: response.imported, errors: response.errors, header_map: response.header_map };
    },
  });

  const updateDisplayNameMutation = useMutation({
    mutationFn: async (displayName: string) => {
      const response = await api.updateClinicianProfile({ display_name: displayName });
      setCache<ClinicianProfile>(queryKeys.clinicianProfile, response.clinician_profile);
    },
  });

  const sendMessageMutation = useMutation({
    mutationFn: async (args: {
      visitId: number;
      channel: "sms" | "email";
      body: string;
      sendImmediately: boolean;
    }) => {
      const result = await api.createMessage(args.visitId, args.channel, args.body, args.sendImmediately);
      return result.message;
    },
    onSuccess: (message) =>
      updateCollection<Message>(queryKeys.messages, (prev) => [message, ...prev]),
  });

  const bulkConfirmMessagesMutation = useMutation({
    mutationFn: () => api.bulkConfirmMessages(),
    onSuccess: (result) =>
      updateCollection<Message>(queryKeys.messages, (prev) => [...result.messages, ...prev]),
  });

  const approveMessageMutation = useMutation({
    mutationFn: async (messageId: number) => {
      const result = await api.approveMessage(messageId);
      return result.message;
    },
    onSuccess: (message) =>
      updateCollection<Message>(queryKeys.messages, (prev) =>
        prev.map((m) => (m.id === message.id ? message : m)),
      ),
  });

  const selectMessageSuggestionMutation = useMutation({
    mutationFn: async ({ messageId, suggestionIndex }: { messageId: number; suggestionIndex: number }) => {
      const result = await api.selectMessageSuggestion(messageId, suggestionIndex);
      return result.message;
    },
    onSuccess: (message) =>
      updateCollection<Message>(queryKeys.messages, (prev) =>
        prev.map((m) => (m.id === message.id ? message : m)),
      ),
  });

  const executeAlertActionMutation = useMutation({
    mutationFn: (alertId: number) => api.executeAlertAction(alertId),
    onSuccess: (result, alertId) => {
      updateCollection<Alert>(queryKeys.alerts, (prev) =>
        prev.map((a) => (a.id === alertId ? result.alert : a)),
      );
      if (result.message) {
        const message = result.message;
        updateCollection<Message>(queryKeys.messages, (prev) => [message, ...prev]);
      }
    },
  });

  const updateAlertMutation = useMutation({
    mutationFn: ({ alertId, status }: { alertId: number; status: string }) => api.updateAlert(alertId, status),
    onSuccess: (result, { alertId }) =>
      updateCollection<Alert>(queryKeys.alerts, (prev) =>
        prev.map((a) => (a.id === alertId ? result.alert : a)),
      ),
  });


  const optimizeSchedule = useCallback(
    (start?: { latitude: number; longitude: number }) =>
      tryRunBool(optimizeScheduleMutation.mutateAsync(start)),
    [optimizeScheduleMutation],
  );

  const createPatient = useCallback(
    (payload: PatientSavePayload) => tryRunBool(createPatientMutation.mutateAsync(payload)),
    [createPatientMutation],
  );

  const updatePatient = useCallback(
    (id: number, payload: PatientSavePayload) =>
      tryRunBool(updatePatientMutation.mutateAsync({ id, payload })),
    [updatePatientMutation],
  );

  const seedDemoPatients = useCallback(
    () => tryRunBool(seedDemoPatientsMutation.mutateAsync()),
    [seedDemoPatientsMutation],
  );

  const importPatients = useCallback(
    (file: File) => tryRun(importPatientsMutation.mutateAsync(file)),
    [importPatientsMutation],
  );

  const updateProfileAndReoptimize = useCallback(
    (profileFields: ProfileUpdate) =>
      tryRunBool(updateProfileAndReoptimizeMutation.mutateAsync(profileFields)),
    [updateProfileAndReoptimizeMutation],
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
    (workingDays: number[]) => updateProfileAndReoptimize({ working_days: workingDays }),
    [updateProfileAndReoptimize],
  );

  const updateHomeLocation = useCallback(
    (latitude: number, longitude: number) =>
      updateProfileAndReoptimize({ home_latitude: latitude, home_longitude: longitude }),
    [updateProfileAndReoptimize],
  );

  const updateDisplayName = useCallback(
    (displayName: string) => tryRunBool(updateDisplayNameMutation.mutateAsync(displayName)),
    [updateDisplayNameMutation],
  );

  const updateSchedulingSettings = useCallback(
    (settings: Record<string, unknown>) => updateProfileAndReoptimize(settings as ProfileUpdate),
    [updateProfileAndReoptimize],
  );

  const sendMessage = useCallback(
    (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) =>
      tryRun(sendMessageMutation.mutateAsync({ visitId, channel, body, sendImmediately })),
    [sendMessageMutation],
  );

  const bulkConfirmMessages = useCallback(
    () => tryRun(bulkConfirmMessagesMutation.mutateAsync()),
    [bulkConfirmMessagesMutation],
  );

  const approveMessage = useCallback(
    (messageId: number) => tryRun(approveMessageMutation.mutateAsync(messageId)),
    [approveMessageMutation],
  );

  const selectMessageSuggestion = useCallback(
    (messageId: number, suggestionIndex: number) =>
      tryRun(selectMessageSuggestionMutation.mutateAsync({ messageId, suggestionIndex })),
    [selectMessageSuggestionMutation],
  );

  const executeAlertAction = useCallback(
    (alertId: number) => tryRunBool(executeAlertActionMutation.mutateAsync(alertId)),
    [executeAlertActionMutation],
  );

  const updateAlert = useCallback(
    (alertId: number, status: string) =>
      tryRunBool(updateAlertMutation.mutateAsync({ alertId, status })),
    [updateAlertMutation],
  );

  return {
    optimizeSchedule,
    createPatient,
    updatePatient,
    seedDemoPatients,
    importPatients,
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

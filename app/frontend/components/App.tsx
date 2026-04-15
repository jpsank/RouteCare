import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useIsMutating, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import {
  queryKeys,
  useAlertsQuery,
  useCalendarBlocksQuery,
  useCalendarConnectionsQuery,
  useClinicianProfileQuery,
  useMessagesQuery,
  usePatientsQuery,
  useScheduleQuery,
} from "../lib/queries";
import type { ClinicianProfile } from "../types";
import { SetupWizard } from "./SetupWizard";
import { MessageHistorySlideOver } from "./MessageHistorySlideOver";
import { useAppActions } from "./hooks/useAppActions";

const WeeklyCalendarView = lazy(async () => {
  const module = await import("./WeeklyCalendarView");
  return { default: module.WeeklyCalendarView };
});


function PanelFallback() {
  return (
    <div className="rc-card flex items-center gap-2 py-8 justify-center">
      <svg className="h-4 w-4 animate-spin text-orange-500" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span className="text-sm text-gray-500">Loading...</span>
    </div>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const [messageSlideOverOpen, setMessageSlideOverOpen] = useState(false);

  const { data: schedule = null, isFetching: scheduleFetching } = useScheduleQuery();
  const { data: patients = [], isFetching: patientsFetching } = usePatientsQuery();
  const { data: messages } = useMessagesQuery();
  const { data: calendarBlocks = [], isFetching: blocksFetching } = useCalendarBlocksQuery();
  const { data: calendarConnections = [] } = useCalendarConnectionsQuery();
  const { data: clinicianProfile, isFetching: profileFetching } = useClinicianProfileQuery();
  const { data: alerts = [] } = useAlertsQuery();

  const mutatingCount = useIsMutating();
  const loading =
    mutatingCount > 0 ||
    scheduleFetching ||
    patientsFetching ||
    blocksFetching ||
    profileFetching;

  const actions = useAppActions();

  const refreshCalendar = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.schedule }),
      queryClient.invalidateQueries({ queryKey: queryKeys.patients }),
      queryClient.invalidateQueries({ queryKey: queryKeys.calendarBlocks }),
      queryClient.invalidateQueries({ queryKey: queryKeys.calendarConnections }),
      queryClient.invalidateQueries({ queryKey: queryKeys.messages }),
      queryClient.invalidateQueries({ queryKey: queryKeys.alerts }),
      queryClient.invalidateQueries({ queryKey: queryKeys.clinicianProfile }),
    ]);
  }, [queryClient]);

  useEffect(() => {
    if (!clinicianProfile) return;
    if (clinicianProfile.setup_completed_at) return;
    if (!clinicianProfile.home_latitude && patients.length === 0) return;

    const completedAt = new Date().toISOString();
    api
      .updateClinicianProfile({ setup_completed_at: completedAt })
      .then(() => {
        queryClient.setQueryData<ClinicianProfile>(queryKeys.clinicianProfile, (prev) =>
          prev ? { ...prev, setup_completed_at: completedAt } : prev,
        );
      })
      .catch(() => undefined);
  }, [clinicianProfile, patients.length, queryClient]);

  const pendingMessageCount = (messages ?? []).filter((m) => m.status === "pending_approval").length;

  const signOut = useCallback(() => {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
    fetch("/users/sign_out", {
      method: "DELETE",
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
      credentials: "same-origin",
    }).then(() => { window.location.href = "/users/sign_in"; });
  }, []);

  const needsSetup = clinicianProfile && !clinicianProfile.setup_completed_at;
  if (needsSetup) {
    return (
      <div className="rc-app">
        <div className="rc-shell">
          <SetupWizard
            clinicianProfile={clinicianProfile}
            onComplete={refreshCalendar}
            onSeedDemo={actions.seedDemoPatients}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="rc-app">
      <div className="rc-shell">
        <main>
          <Suspense fallback={<PanelFallback />}>
            <WeeklyCalendarView
              schedule={schedule}
              patients={patients}
              calendarBlocks={calendarBlocks}
              calendarConnections={calendarConnections}
              loading={loading}
              pendingMessageCount={pendingMessageCount}
              onOpenMessages={() => setMessageSlideOverOpen(true)}
              onSignOut={signOut}
              onOptimize={actions.optimizeSchedule}
              onCreatePatient={actions.createPatient}
              onUpdatePatient={actions.updatePatient}
              onSeedDemoPatients={actions.seedDemoPatients}
              onImportPatients={actions.importPatients}
              clinicianProfile={clinicianProfile ?? null}
              onUpdateWorkingHours={actions.updateWorkingHours}
              onUpdateWorkingDays={actions.updateWorkingDays}
              onUpdateLunchSettings={actions.updateLunchSettings}
              onUpdateDisplayName={actions.updateDisplayName}
              onUpdateSchedulingSettings={actions.updateSchedulingSettings}
              onSetHomeFromCurrentLocation={actions.updateHomeLocation}
              onUpdateHomeLocation={actions.updateHomeLocation}
              onCalendarRefresh={refreshCalendar}
              onBulkConfirm={actions.bulkConfirmMessages}
              onSendMessage={actions.sendMessage}
              messages={messages ?? null}
              alerts={alerts}
              onUpdateAlert={actions.updateAlert}
              onExecuteAlertAction={actions.executeAlertAction}
            />
          </Suspense>
        </main>

        {createPortal(
          <MessageHistorySlideOver
            messages={messages ?? []}
            isOpen={messageSlideOverOpen}
            onClose={() => setMessageSlideOverOpen(false)}
            onApprove={actions.approveMessage}
          />,
          document.body,
        )}
      </div>
    </div>
  );
}

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
      <svg className="h-4 w-4 animate-spin text-indigo-500" viewBox="0 0 24 24" fill="none">
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
        <header className="mb-4 flex flex-wrap items-center gap-x-1 gap-y-2 border-b border-gray-200 pb-2">
          <svg className="mr-1 h-5 w-5 flex-none text-indigo-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 6.75V15m6-6v8.25m.503 3.498 4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 0 0-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0Z" />
          </svg>
          <span className="mr-2 text-sm font-bold tracking-tight text-gray-900 sm:mr-4">RouteCare</span>

          <div className="ml-auto flex items-center gap-1">
            {loading && (
              <svg className="h-3.5 w-3.5 animate-spin text-indigo-400" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            <button
              className="relative flex items-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100"
              onClick={() => setMessageSlideOverOpen(true)}
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
            <button
              className="rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-400 shadow-none hover:bg-gray-100 hover:text-gray-600"
              onClick={() => {
                const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");
                fetch("/users/sign_out", {
                  method: "DELETE",
                  headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
                  credentials: "same-origin",
                }).then(() => { window.location.href = "/users/sign_in"; });
              }}
            >
              <svg className="h-4 w-4 sm:hidden" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3 0 3-3m0 0-3-3m3 3H9" />
              </svg>
              <span className="hidden sm:inline">Sign out</span>
            </button>
          </div>
        </header>

        <main>
          <Suspense fallback={<PanelFallback />}>
            <WeeklyCalendarView
              schedule={schedule}
              patients={patients}
              calendarBlocks={calendarBlocks}
              calendarConnections={calendarConnections}
              loading={loading}
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

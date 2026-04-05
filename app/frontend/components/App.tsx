import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Alert, CalendarBlock, CalendarConnection, ClinicianProfile, Message, Patient, WeeklySchedule, Visit } from "../types";
import { SetupWizard } from "./SetupWizard";

const AlertsPanel = lazy(async () => {
  const module = await import("./AlertsPanel");
  return { default: module.AlertsPanel };
});

const MessagingPanel = lazy(async () => {
  const module = await import("./MessagingPanel");
  return { default: module.MessagingPanel };
});

const WeeklyCalendarView = lazy(async () => {
  const module = await import("./WeeklyCalendarView");
  return { default: module.WeeklyCalendarView };
});

type Tab = "schedule" | "messages" | "alerts";
type PatientSavePayload = {
  full_name: string;
  phone: string;
  email?: string;
  address_line1: string;
  address_line2?: string;
  city: string;
  state: string;
  postal_code: string;
  required_visits_per_week: number;
  visit_duration_minutes: number;
  notes?: string;
  latitude?: number;
  longitude?: number;
};

const TABS: Array<[Tab, string, string]> = [
  ["schedule", "Calendar", "M4.5 3.75a3 3 0 0 0-3 3v10.5a3 3 0 0 0 3 3h15a3 3 0 0 0 3-3V6.75a3 3 0 0 0-3-3h-15Zm9 4.5a.75.75 0 0 0-1.5 0v4.5a.75.75 0 0 0 .4.664l3 1.5a.75.75 0 1 0 .671-1.342L13.5 12.466V8.25Z"],
  ["messages", "Messages", "M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 0 1-.825-.242m9.345-8.334a2.126 2.126 0 0 0-.476-.095 48.64 48.64 0 0 0-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0 0 11.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155"],
  ["alerts", "Alerts", "M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0"],
];

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
  const [activeTab, setActiveTab] = useState<Tab>("schedule");
  const [loadingCount, setLoadingCount] = useState(0);
  const loading = loadingCount > 0;
  const startLoading = useCallback(() => setLoadingCount((c) => c + 1), []);
  const stopLoading = useCallback(() => setLoadingCount((c) => Math.max(0, c - 1)), []);
  const [schedule, setSchedule] = useState<WeeklySchedule | null>(null);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [calendarBlocks, setCalendarBlocks] = useState<CalendarBlock[]>([]);
  const [calendarConnections, setCalendarConnections] = useState<CalendarConnection[]>([]);
  const [clinicianProfile, setClinicianProfile] = useState<ClinicianProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshData = useCallback(async () => {
    startLoading();
    setError(null);
    try {
      const [scheduleResponse, patientsResponse, messagesResponse, alertsResponse, blocksResponse, connectionsResponse, profileResponse] = await Promise.all([
        api.getSchedule(),
        api.listPatients(),
        api.listMessages(),
        api.listAlerts(),
        api.listCalendarBlocks(),
        api.listCalendarConnections(),
        api.getClinicianProfile(),
      ]);
      setSchedule(scheduleResponse.schedule);
      setPatients(patientsResponse.patients);
      setMessages(messagesResponse.messages);
      setAlerts(alertsResponse.alerts);
      setCalendarBlocks(blocksResponse.calendar_blocks);
      setCalendarConnections(connectionsResponse.calendar_connections);
      setClinicianProfile(profileResponse.clinician_profile);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  }, []);

  useEffect(() => {
    refreshData().catch(() => undefined);
  }, [refreshData]);

  // Auto-complete setup for existing users who already have data
  useEffect(() => {
    if (clinicianProfile && !clinicianProfile.setup_completed_at && (clinicianProfile.home_latitude || patients.length > 0)) {
      api.updateClinicianProfile({ setup_completed_at: new Date().toISOString() })
        .then(() => setClinicianProfile((prev) => prev ? { ...prev, setup_completed_at: new Date().toISOString() } : prev))
        .catch(() => undefined);
    }
  }, [clinicianProfile, patients.length]);

  const visits = useMemo(() => schedule?.visits ?? [], [schedule]);
  const optimizeSchedule = async (start?: { latitude: number; longitude: number }) => {
    startLoading();
    setError(null);
    try {
      const response = await api.optimizeSchedule(undefined, start?.latitude, start?.longitude);
      setSchedule(response.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCalendarBlocks(refreshedBlocks.calendar_blocks);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const createPatient = async (payload: PatientSavePayload) => {
    startLoading();
    setError(null);
    try {
      const created = await api.createPatient(payload);
      setPatients((prev) => [created.patient, ...prev]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const updatePatient = async (patientId: number, payload: PatientSavePayload) => {
    startLoading();
    setError(null);
    try {
      const updated = await api.updatePatient(patientId, payload);
      setPatients((prev) => prev.map((patient) => (patient.id === patientId ? updated.patient : patient)));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const seedDemoPatients = async () => {
    startLoading();
    setError(null);
    try {
      const response = await api.seedDemoPatients();
      setPatients(response.patients);
      const optimized = await api.optimizeSchedule();
      setSchedule(optimized.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCalendarBlocks(refreshedBlocks.calendar_blocks);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const updateWorkingHours = async (workdayStartMinute: number, workdayEndMinute: number) => {
    startLoading();
    setError(null);
    try {
      const response = await api.updateClinicianProfile({
        workday_start_minute: workdayStartMinute,
        workday_end_minute: workdayEndMinute,
      });
      setClinicianProfile(response.clinician_profile);
      const optimized = await api.optimizeSchedule();
      setSchedule(optimized.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCalendarBlocks(refreshedBlocks.calendar_blocks);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const updateLunchSettings = async (lunchStartMinute: number, lunchDurationMinutes: number, lunchWindowMinutes: number) => {
    startLoading();
    setError(null);
    try {
      const response = await api.updateClinicianProfile({
        lunch_start_minute: lunchStartMinute,
        lunch_duration_minutes: lunchDurationMinutes,
        lunch_window_minutes: lunchWindowMinutes,
      });
      setClinicianProfile(response.clinician_profile);
      const optimized = await api.optimizeSchedule();
      setSchedule(optimized.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCalendarBlocks(refreshedBlocks.calendar_blocks);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const updateWorkingDays = async (workingDays: number[]) => {
    startLoading();
    setError(null);
    try {
      const response = await api.updateClinicianProfile({
        working_days: workingDays,
      });
      setClinicianProfile(response.clinician_profile);
      const optimized = await api.optimizeSchedule();
      setSchedule(optimized.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCalendarBlocks(refreshedBlocks.calendar_blocks);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const updateHomeLocation = async (latitude: number, longitude: number) => {
    startLoading();
    setError(null);
    try {
      const response = await api.updateClinicianProfile({
        home_latitude: latitude,
        home_longitude: longitude,
      });
      setClinicianProfile(response.clinician_profile);
      const optimized = await api.optimizeSchedule();
      setSchedule(optimized.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCalendarBlocks(refreshedBlocks.calendar_blocks);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const updateDisplayName = async (displayName: string) => {
    startLoading();
    setError(null);
    try {
      const response = await api.updateClinicianProfile({
        display_name: displayName,
      });
      setClinicianProfile(response.clinician_profile);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const sendMessage = async (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => {
    startLoading();
    setError(null);
    try {
      const result = await api.createMessage(visitId, channel, body, sendImmediately);
      setMessages((prev) => [result.message, ...prev]);
      return result.message;
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const approveMessage = async (messageId: number) => {
    startLoading();
    setError(null);
    try {
      const result = await api.approveMessage(messageId);
      setMessages((prev) => prev.map((message) => (message.id === messageId ? result.message : message)));
      return result.message;
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const selectMessageSuggestion = async (messageId: number, suggestionIndex: number) => {
    startLoading();
    setError(null);
    try {
      const result = await api.selectMessageSuggestion(messageId, suggestionIndex);
      setMessages((prev) => prev.map((message) => (message.id === messageId ? result.message : message)));
      return result.message;
    } catch (err) {
      setError((err as Error).message);
    } finally {
      stopLoading();
    }
  };

  const updateAlert = async (alertId: number, status: string) => {
    setError(null);
    try {
      const result = await api.updateAlert(alertId, status);
      setAlerts((prev) => prev.map((a) => (a.id === alertId ? result.alert : a)));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const renderMain = () => {
    if (activeTab === "schedule") {
      return (
        <WeeklyCalendarView
          schedule={schedule}
          patients={patients}
          calendarBlocks={calendarBlocks}
          calendarConnections={calendarConnections}
          loading={loading}
          onOptimize={optimizeSchedule}
          onCreatePatient={createPatient}
          onUpdatePatient={updatePatient}
          onSeedDemoPatients={seedDemoPatients}
          clinicianProfile={clinicianProfile}
          onUpdateWorkingHours={updateWorkingHours}
          onUpdateWorkingDays={updateWorkingDays}
          onUpdateLunchSettings={updateLunchSettings}
          onUpdateDisplayName={updateDisplayName}
          onSetHomeFromCurrentLocation={updateHomeLocation}
          onUpdateHomeLocation={updateHomeLocation}
          onCalendarRefresh={refreshData}
        />
      );
    }
    if (activeTab === "messages") {
      return (
        <MessagingPanel
          visits={visits}
          messages={messages}
          onSend={sendMessage}
          onApprove={approveMessage}
          onSelectSuggestion={selectMessageSuggestion}
        />
      );
    }
    return <AlertsPanel alerts={alerts} onUpdateAlert={updateAlert} />;
  };

  const openAlertCount = alerts.filter((a) => a.status === "open").length;
  const pendingMessageCount = messages.filter((m) => m.status === "pending_approval").length;

  const badgeCount = (tab: Tab): number => {
    if (tab === "alerts") return openAlertCount;
    if (tab === "messages") return pendingMessageCount;
    return 0;
  };

  // Show onboarding wizard for new users
  const needsSetup = clinicianProfile && !clinicianProfile.setup_completed_at;
  if (needsSetup) {
    return (
      <div className="rc-app">
        <div className="rc-shell">
          <SetupWizard
            clinicianProfile={clinicianProfile}
            onComplete={refreshData}
            onSeedDemo={seedDemoPatients}
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

          <nav className="flex gap-0.5">
            {TABS.map(([tab, label, iconPath]) => {
              const count = badgeCount(tab);
              return (
                <button
                  key={tab}
                  className={`relative flex items-center gap-1 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium shadow-none transition-colors hover:bg-gray-100 sm:gap-1.5 sm:px-3 ${
                    activeTab === tab
                      ? "bg-indigo-50 text-indigo-600"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                  onClick={() => setActiveTab(tab)}
                >
                  <svg className="h-3.5 w-3.5 flex-none" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d={iconPath} />
                  </svg>
                  <span className="hidden sm:inline">{label}</span>
                  {count > 0 && (
                    <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {loading && (
              <svg className="h-3.5 w-3.5 animate-spin text-indigo-400" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
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

        {error && <div className="rc-error">{error}</div>}
        <main>
          <Suspense fallback={<PanelFallback />}>
            {renderMain()}
          </Suspense>
        </main>
      </div>
    </div>
  );
}

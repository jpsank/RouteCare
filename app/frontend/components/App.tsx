import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Alert, CalendarBlock, CalendarConnection, ClinicianProfile, Message, Patient, Schedule, Visit } from "../types";
import { AlertsPanel } from "./AlertsPanel";
import { MessagingPanel } from "./MessagingPanel";
import { WeeklyCalendarView } from "./WeeklyCalendarView";

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

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>("schedule");
  const [loading, setLoading] = useState(false);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [calendarBlocks, setCalendarBlocks] = useState<CalendarBlock[]>([]);
  const [calendarConnections, setCalendarConnections] = useState<CalendarConnection[]>([]);
  const [clinicianProfile, setClinicianProfile] = useState<ClinicianProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshData = useCallback(async () => {
    setLoading(true);
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
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshData().catch(() => undefined);
  }, [refreshData]);

  const visits = useMemo(() => schedule?.visits ?? [], [schedule]);
  const optimizeSchedule = async (start?: { latitude: number; longitude: number }) => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.optimizeSchedule(undefined, start?.latitude, start?.longitude);
      setSchedule(response.schedule);
      const refreshedBlocks = await api.listCalendarBlocks();
      setCalendarBlocks(refreshedBlocks.calendar_blocks);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const createPatient = async (payload: PatientSavePayload) => {
    setLoading(true);
    setError(null);
    try {
      const created = await api.createPatient(payload);
      setPatients((prev) => [created.patient, ...prev]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const updatePatient = async (patientId: number, payload: PatientSavePayload) => {
    setLoading(true);
    setError(null);
    try {
      const updated = await api.updatePatient(patientId, payload);
      setPatients((prev) => prev.map((patient) => (patient.id === patientId ? updated.patient : patient)));
    } catch (err) {
      setError((err as Error).message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const seedDemoPatients = async () => {
    setLoading(true);
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
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const updateWorkingHours = async (workdayStartMinute: number, workdayEndMinute: number) => {
    setLoading(true);
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
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const updateWorkingDays = async (workingDays: number[]) => {
    setLoading(true);
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
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const setHomeFromCurrentLocation = async (latitude: number, longitude: number) => {
    setLoading(true);
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
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const updateHomeLocation = async (latitude: number, longitude: number) => {
    setLoading(true);
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
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const sendMessage = async (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.createMessage(visitId, channel, body, sendImmediately);
      setMessages((prev) => [result.message, ...prev]);
      return result.message;
    } catch (err) {
      setError((err as Error).message);
      throw err;
    } finally {
      setLoading(false);
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
          onSetHomeFromCurrentLocation={setHomeFromCurrentLocation}
          onUpdateHomeLocation={updateHomeLocation}
          onCalendarRefresh={refreshData}
        />
      );
    }
    if (activeTab === "messages") {
      return <MessagingPanel visits={visits} messages={messages} onSend={sendMessage} />;
    }
    return <AlertsPanel alerts={alerts} />;
  };

  return (
    <div className="routecare-app">
      <div className="routecare-shell">
        <header className="routecare-header rounded-2xl border border-slate-200 bg-white/85 p-5 shadow-soft backdrop-blur">
          <div>
            <p className="mb-2 text-xs font-bold uppercase tracking-[0.12em] text-teal-700">Clinician Workspace</p>
            <h1 className="routecare-title">RouteCare</h1>
            <p className="routecare-subtitle">Route-optimized field scheduling for home-visit clinicians</p>
          </div>
        </header>

        <nav className="tabs">
          {([
            ["schedule", "Weekly Calendar"],
            ["messages", "Patient Messages"],
            ["alerts", "Alerts"],
          ] as Array<[Tab, string]>).map(([tab, label]) => (
            <button
              key={tab}
              className={activeTab === tab ? "active ring-2 ring-teal-100" : ""}
              onClick={() => setActiveTab(tab)}
            >
              {label}
            </button>
          ))}
        </nav>

        {error && <div className="error">{error}</div>}
        <main className="grid gap-3">{renderMain()}</main>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { Alert, Message, Patient, WeeklySchedule, Visit } from "../types";
import { AlertsPanel } from "./AlertsPanel";
import { DailyRouteView } from "./DailyRouteView";
import { MessagingPanel } from "./MessagingPanel";
import { PatientRoster } from "./PatientRoster";
import { WeeklyCalendarView } from "./WeeklyCalendarView";

type Tab = "schedule" | "route" | "patients" | "messages" | "alerts";

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>("schedule");
  const [loading, setLoading] = useState(false);
  const [schedule, setSchedule] = useState<WeeklySchedule | null>(null);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refreshData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [scheduleResponse, patientsResponse, messagesResponse, alertsResponse] = await Promise.all([
        api.getSchedule(),
        api.listPatients(),
        api.listMessages(),
        api.listAlerts(),
      ]);
      setSchedule(scheduleResponse.schedule);
      setPatients(patientsResponse.patients);
      setMessages(messagesResponse.messages);
      setAlerts(alertsResponse.alerts);
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
  const pendingCount = useMemo(
    () => visits.filter((visit) => visit.status === "pending_patient_confirmation").length,
    [visits],
  );
  const confirmedCount = useMemo(
    () => visits.filter((visit) => visit.status === "confirmed").length,
    [visits],
  );
  const openAlertsCount = useMemo(
    () => alerts.filter((alert) => alert.status === "open").length,
    [alerts],
  );
  const selectedRouteDate =
    visits.length > 0
      ? visits[0].starts_at.slice(0, 10)
      : new Date().toISOString().slice(0, 10);

  const optimizeSchedule = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.optimizeSchedule();
      setSchedule(response.schedule);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const approveSchedule = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.approveSchedule();
      setSchedule(response.schedule);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const createPatient = async (payload: {
    full_name: string;
    phone: string;
    email?: string;
    address_line1: string;
    city: string;
    state: string;
    postal_code: string;
    required_visits_per_week: number;
    visit_duration_minutes: number;
    notes?: string;
  }) => {
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
          loading={loading}
          onOptimize={optimizeSchedule}
          onApprove={approveSchedule}
        />
      );
    }
    if (activeTab === "route") {
      return <DailyRouteView visits={visits} date={selectedRouteDate} />;
    }
    if (activeTab === "patients") {
      return <PatientRoster patients={patients} onCreate={createPatient} onRefresh={refreshData} />;
    }
    if (activeTab === "messages") {
      return <MessagingPanel visits={visits} messages={messages} onSend={sendMessage} />;
    }
    return <AlertsPanel alerts={alerts} />;
  };

  return (
    <div className="routecare-app">
      <div className="routecare-shell">
        <header className="routecare-header">
          <div>
            <p className="eyebrow">RouteCare</p>
            <h1 className="routecare-title">Field Scheduling Command Center</h1>
            <p className="routecare-subtitle">
              Route-optimized home-visit scheduling for PT, OT, and home health clinicians.
            </p>
          </div>
          <div className="actions">
            <button className="primary-btn" onClick={refreshData} disabled={loading}>
              {loading ? "Refreshing..." : "Refresh Data"}
            </button>
          </div>
        </header>

        <section className="metrics-row">
          <article className="metric-card">
            <p className="metric-label">Total Weekly Visits</p>
            <p className="metric-value">{visits.length}</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Pending Confirmations</p>
            <p className="metric-value">{pendingCount}</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Confirmed Visits</p>
            <p className="metric-value">{confirmedCount}</p>
          </article>
          <article className="metric-card">
            <p className="metric-label">Open Alerts</p>
            <p className="metric-value">{openAlertsCount}</p>
          </article>
        </section>

        <nav className="tabs">
          {([
            ["schedule", "Weekly Calendar"],
            ["route", "Daily Route"],
            ["patients", "Patient Roster"],
            ["messages", "Patient Messages"],
            ["alerts", "Alerts"],
          ] as Array<[Tab, string]>).map(([tab, label]) => (
            <button key={tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>
              {label}
            </button>
          ))}
        </nav>

        {error && <div className="error-banner">{error}</div>}
        <main>{renderMain()}</main>
      </div>
    </div>
  );
}

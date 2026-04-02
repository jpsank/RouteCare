import type { Schedule } from "../types";

type Props = {
  schedule: Schedule | null;
  loading: boolean;
  onOptimize: () => Promise<void>;
  onApprove: () => Promise<void>;
};

function fmt(date: string): string {
  return new Date(date).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function statusClass(status: string): string {
  switch (status) {
    case "confirmed":
      return "status-success";
    case "declined":
      return "status-danger";
    case "unscheduled":
      return "status-muted";
    default:
      return "status-warning";
  }
}

export function WeeklyCalendarView({ schedule, loading, onOptimize, onApprove }: Props) {
  if (!schedule) {
    return (
      <section className="card">
        <h2>Weekly Calendar</h2>
        <p>No schedule generated yet.</p>
        <button className="primary" onClick={onOptimize} disabled={loading}>
          {loading ? "Optimizing..." : "Generate Optimized Schedule"}
        </button>
      </section>
    );
  }

  const grouped = schedule.visits.reduce<Record<string, typeof schedule.visits>>((acc, visit) => {
    const key = new Date(visit.starts_at).toDateString();
    acc[key] ||= [];
    acc[key].push(visit);
    return acc;
  }, {});

  return (
    <section className="card">
      <h2>Weekly Calendar</h2>
      <p className="section-subtitle">
        Visits are sequenced to reduce drive time while respecting patient availability and blocked calendar time.
      </p>
      <div className="controls">
        <button className="primary" onClick={onOptimize} disabled={loading}>
          {loading ? "Optimizing..." : "Re-optimize Week"}
        </button>
        <button onClick={onApprove} disabled={loading || schedule.status === "clinician_approved"}>
          {schedule.status === "clinician_approved" ? "Approved" : "Approve Schedule"}
        </button>
      </div>
      {Object.entries(grouped).map(([day, visits]) => (
        <div className="day-column" key={day}>
          <h3>{day}</h3>
          {visits
            .sort((a, b) => a.starts_at.localeCompare(b.starts_at))
            .map((visit) => (
              <article key={visit.id} className={`visit ${visit.status}`}>
                <div className="visit-title">
                  {visit.patient_name} | {fmt(visit.starts_at)} - {fmt(visit.ends_at)}
                </div>
                <div className="visit-meta">
                  <span className={`pill ${statusClass(visit.status)}`}>{visit.status.replaceAll("_", " ")}</span>
                  <span>Drive from previous: {visit.drive_from_previous_minutes} min</span>
                </div>
              </article>
            ))}
        </div>
      ))}
    </section>
  );
}

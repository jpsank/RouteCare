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

function shortDay(value: string): string {
  return new Date(value).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
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
      <section className="surface-card">
        <h2>Weekly Calendar</h2>
        <p className="section-subtitle">No schedule generated yet. Optimize this week to build an efficient route.</p>
        <button className="btn btn-primary" onClick={onOptimize} disabled={loading}>
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
    <section className="surface-card">
      <div className="section-head">
        <h2>Weekly Calendar</h2>
        <span className={`pill ${statusClass(schedule.status)}`}>{schedule.status.replaceAll("_", " ")}</span>
      </div>
      <p className="section-subtitle">
        Visits are sequenced to reduce drive time while respecting patient availability and blocked calendar time.
      </p>
      <div className="schedule-metrics">
        <article className="mini-metric">
          <span>Optimized Drive</span>
          <strong>{schedule.total_drive_minutes}m</strong>
        </article>
        <article className="mini-metric">
          <span>Baseline Drive</span>
          <strong>{schedule.baseline_drive_minutes}m</strong>
        </article>
        <article className="mini-metric">
          <span>Saved</span>
          <strong>{schedule.drive_minutes_saved}m</strong>
        </article>
      </div>
      <div className="controls">
        <button className="btn btn-primary" onClick={onOptimize} disabled={loading}>
          {loading ? "Optimizing..." : "Re-optimize Week"}
        </button>
        <button className="btn" onClick={onApprove} disabled={loading || schedule.status === "clinician_approved"}>
          {schedule.status === "clinician_approved" ? "Approved" : "Approve Schedule"}
        </button>
      </div>
      <div className="week-grid">
        {Object.entries(grouped).map(([day, visits]) => (
          <div className="day-column" key={day}>
            <h3>{shortDay(day)}</h3>
            {visits
              .sort((a, b) => a.starts_at.localeCompare(b.starts_at))
              .map((visit) => (
                <article key={visit.id} className={`visit ${visit.status}`}>
                  <div className="visit-title">{visit.patient_name}</div>
                  <div className="visit-time">
                    {fmt(visit.starts_at)} - {fmt(visit.ends_at)}
                  </div>
                  <div className="visit-meta">
                    <span>Drive from previous: {visit.drive_from_previous_minutes} min</span>
                    <span className={`status-pill ${statusClass(visit.status)}`}>{visit.status.replaceAll("_", " ")}</span>
                  </div>
                </article>
              ))}
          </div>
        ))}
      </div>
    </section>
  );
}

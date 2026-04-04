import type { Alert } from "../types";

type Props = {
  alerts: Alert[];
};

function formatDate(value?: string | null): string {
  if (!value) return "No due date";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "No due date" : date.toLocaleString();
}

export function AlertsPanel({ alerts }: Props) {
  const sortedAlerts = [...alerts].sort((a, b) => {
    const severityScore = { high: 3, medium: 2, low: 1 };
    const scoreDiff = severityScore[b.severity] - severityScore[a.severity];
    if (scoreDiff !== 0) return scoreDiff;

    return (new Date(b.due_at || 0).getTime() || 0) - (new Date(a.due_at || 0).getTime() || 0);
  });

  return (
    <section className="panel-card">
      <div className="panel-header">
        <h2 className="panel-title">Notifications & Alerts</h2>
        <span className="pill">{alerts.length} open</span>
      </div>

      {alerts.length === 0 && <p className="muted">No active alerts.</p>}

      {alerts.length > 0 && (
        <ul className="alerts-list">
          {sortedAlerts.map((alert) => (
            <li key={alert.id} className={`alert-item severity-${alert.severity} bg-white`}>
              <p className="alert-message">{alert.message}</p>
              <div className="alert-meta">
                <span>{alert.category.replaceAll("_", " ")}</span>
                <span>{alert.status}</span>
                <span>Severity: {alert.severity}</span>
                <span>Due: {formatDate(alert.due_at)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

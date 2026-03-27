import type { Alert } from "../types";

type Props = {
  alerts: Alert[];
};

export function AlertsPanel({ alerts }: Props) {
  return (
    <section className="panel">
      <h2>Notifications & Alerts</h2>
      {alerts.length === 0 && <p className="muted">No active alerts.</p>}
      {alerts.length > 0 && (
        <ul className="alerts-list">
          {alerts.map((alert) => (
            <li key={alert.id} className={`alert-item severity-${alert.severity}`}>
              <p>{alert.message}</p>
              <small>
                {alert.category} · {alert.status}
              </small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

import type { Alert } from "../types";

type Props = {
  alerts: Alert[];
};

function formatDate(value?: string | null): string {
  if (!value) return "No due date";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "No due date" : date.toLocaleString();
}

function sevClass(severity: string): string {
  if (severity === "high") return "rc-alert-item sev-high";
  if (severity === "medium") return "rc-alert-item sev-medium";
  return "rc-alert-item sev-low";
}

export function AlertsPanel({ alerts }: Props) {
  const sortedAlerts = [...alerts].sort((a, b) => {
    const severityScore = { high: 3, medium: 2, low: 1 };
    const scoreDiff = severityScore[b.severity] - severityScore[a.severity];
    if (scoreDiff !== 0) return scoreDiff;
    return (new Date(b.due_at || 0).getTime() || 0) - (new Date(a.due_at || 0).getTime() || 0);
  });

  return (
    <div className="rc-card space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="rc-section-title">Alerts</h2>
          <p className="rc-section-subtitle">Monitor conflicts and follow-up items.</p>
        </div>
        <span className="rc-pill">{alerts.length} open</span>
      </div>

      {alerts.length === 0 && <div className="rc-empty">No active alerts.</div>}

      <div className="space-y-2">
        {sortedAlerts.map((alert) => (
          <div key={alert.id} className={sevClass(alert.severity)}>
            <div className="text-sm font-medium text-gray-900">{alert.message}</div>
            <div className="mt-1 flex flex-wrap gap-2 text-xs text-gray-500">
              <span>{alert.category.replaceAll("_", " ")}</span>
              <span>&middot;</span>
              <span>{alert.status}</span>
              <span>&middot;</span>
              <span>{alert.severity}</span>
              <span>&middot;</span>
              <span>{formatDate(alert.due_at)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

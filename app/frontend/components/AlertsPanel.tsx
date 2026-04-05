import { useState, useMemo } from "react";
import type { Alert } from "../types";

type Props = {
  alerts: Alert[];
  onUpdateAlert: (alertId: number, status: Alert["status"]) => Promise<void>;
};

function formatDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function sevClass(severity: string): string {
  if (severity === "high") return "rc-alert-item sev-high";
  if (severity === "medium") return "rc-alert-item sev-medium";
  return "rc-alert-item sev-low";
}

const STATUS_OPTIONS: Array<[Alert["status"], string]> = [
  ["open", "Open"],
  ["acknowledged", "Acknowledged"],
  ["resolved", "Resolved"],
];

export function AlertsPanel({ alerts, onUpdateAlert }: Props) {
  const [showResolved, setShowResolved] = useState(false);
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const filtered = useMemo(() => {
    const base = showResolved ? alerts : alerts.filter((a) => a.status !== "resolved");
    return [...base].sort((a, b) => {
      const severityScore: Record<string, number> = { high: 3, medium: 2, low: 1 };
      const scoreDiff = (severityScore[b.severity] ?? 0) - (severityScore[a.severity] ?? 0);
      if (scoreDiff !== 0) return scoreDiff;
      // Nearest due_at first (most urgent)
      const aTime = a.due_at ? new Date(a.due_at).getTime() : Infinity;
      const bTime = b.due_at ? new Date(b.due_at).getTime() : Infinity;
      return aTime - bTime;
    });
  }, [alerts, showResolved]);

  const openCount = alerts.filter((a) => a.status === "open").length;

  async function changeStatus(alertId: number, status: Alert["status"]) {
    setUpdatingId(alertId);
    try {
      await onUpdateAlert(alertId, status);
    } finally {
      setUpdatingId(null);
    }
  }

  return (
    <div className="rc-card space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="rc-section-title">Alerts</h2>
          <p className="rc-section-subtitle">Monitor conflicts and follow-up items.</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="inline-flex items-center gap-1.5 text-xs text-gray-500">
            <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />
            Show resolved
          </label>
          <span className="rc-pill">{openCount} open</span>
        </div>
      </div>

      {filtered.length === 0 && <div className="rc-empty">No active alerts.</div>}

      <div className="space-y-2">
        {filtered.map((alert) => (
          <div key={alert.id} className={sevClass(alert.severity)}>
            <div className="text-sm font-medium text-gray-900">{alert.message}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
              <span>{alert.category.replaceAll("_", " ")}</span>
              <span>&middot;</span>
              <span>{alert.severity}</span>
              {alert.due_at && (
                <>
                  <span>&middot;</span>
                  <span>Due {formatDate(alert.due_at)}</span>
                </>
              )}
              {alert.created_at && (
                <>
                  <span>&middot;</span>
                  <span>{formatDate(alert.created_at)}</span>
                </>
              )}
              <span className="ml-auto flex items-center gap-1.5">
                {STATUS_OPTIONS.map(([value, label]) => (
                  <button
                    key={value}
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      alert.status === value
                        ? "bg-indigo-100 text-indigo-700"
                        : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                    }`}
                    disabled={alert.status === value || updatingId === alert.id}
                    onClick={() => void changeStatus(alert.id, value)}
                  >
                    {label}
                  </button>
                ))}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

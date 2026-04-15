import { useState, useMemo } from "react";
import type { Alert } from "../types";

type Props = {
  alerts: Alert[];
  onUpdateAlert: (alertId: number, status: Alert["status"]) => Promise<void>;
  onExecuteAction?: (alertId: number) => Promise<void>;
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

export function AlertsPanel({ alerts, onUpdateAlert, onExecuteAction }: Props) {
  const [showResolved, setShowResolved] = useState(false);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [actingId, setActingId] = useState<number | null>(null);

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

  const highCount = alerts.filter((a) => a.severity === "high" && a.status === "open").length;

  return (
    <div className="rc-card space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="rc-section-title">Alerts</h2>
          <p className="rc-section-subtitle">Monitor conflicts and follow-up items.</p>
        </div>
        <div className="flex items-center gap-3">
          {highCount > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-md bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">
              <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
              {highCount} urgent
            </span>
          )}
          <span className="rc-pill">{openCount} open</span>
          <label className="inline-flex items-center gap-1.5 text-xs text-gray-500">
            <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />
            Show resolved
          </label>
        </div>
      </div>

      {filtered.length === 0 && (
        <div className="rc-empty">
          <svg className="mx-auto mb-2 h-8 w-8 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
          No active alerts — you're all clear.
        </div>
      )}

      <div className="space-y-2">
        {filtered.map((alert) => (
          <div key={alert.id} className={`${sevClass(alert.severity)} transition-colors ${alert.status === "resolved" ? "opacity-50" : ""}`}>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium text-gray-900">{alert.message}</div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="capitalize">{alert.category.replaceAll("_", " ")}</span>
                  <span>&middot;</span>
                  <span className="capitalize">{alert.severity}</span>
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
                </div>
              </div>
              <div className="flex flex-none items-center gap-1.5">
                {alert.category === "unconfirmed_visit" && Boolean(alert.metadata?.visit_id) && alert.status === "open" && onExecuteAction && (
                  <button
                    className="rounded-md bg-orange-600 px-2 py-1 text-[10px] font-medium text-white transition-colors hover:bg-orange-700"
                    disabled={actingId === alert.id}
                    onClick={async () => {
                      setActingId(alert.id);
                      try { await onExecuteAction(alert.id); } finally { setActingId(null); }
                    }}
                  >
                    {actingId === alert.id ? "Sending..." : "Send Reminder"}
                  </button>
                )}
                {STATUS_OPTIONS.map(([value, label]) => (
                  <button
                    key={value}
                    className={`rounded-md px-2 py-1 text-[10px] font-medium transition-colors ${
                      alert.status === value
                        ? "bg-orange-100 text-orange-700"
                        : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                    }`}
                    disabled={alert.status === value || updatingId === alert.id}
                    onClick={() => void changeStatus(alert.id, value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

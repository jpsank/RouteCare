import { useState, useRef } from "react";
import type { Alert } from "../../types";
import { useClickOutside } from "../hooks/useClickOutside";
import { AlertsTrendChart } from "./AlertsTrendChart";

type Props = {
  alerts: Alert[];
  onUpdateAlert: (alertId: number, status: Alert["status"]) => Promise<boolean>;
  onExecuteAction?: (alertId: number) => Promise<boolean>;
};

const STATUS_OPTIONS: Array<[Alert["status"], string]> = [
  ["open", "Open"],
  ["acknowledged", "Ack"],
  ["resolved", "Done"],
];

export function InlineAlertSummary({ alerts, onUpdateAlert, onExecuteAction }: Props) {
  const [open, setOpen] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const openAlerts = alerts.filter((a) => a.status !== "resolved");
  const highCount = openAlerts.filter((a) => a.severity === "high").length;
  const topAlerts = openAlerts
    .sort((a, b) => {
      const sev: Record<string, number> = { high: 3, medium: 2, low: 1 };
      return (sev[b.severity] ?? 0) - (sev[a.severity] ?? 0);
    })
    .slice(0, 5);

  useClickOutside(ref, () => setOpen(false), open);

  if (openAlerts.length === 0) return null;

  return (
    <div ref={ref} className="relative">
      <button
        className="relative flex items-center gap-1.5 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100"
        onClick={() => setOpen(!open)}
      >
        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0" />
        </svg>
        <span className="hidden sm:inline">{openAlerts.length} alert{openAlerts.length !== 1 ? "s" : ""}</span>
        {highCount > 0 && (
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {highCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-[min(380px,calc(100vw-24px))] rounded-2xl bg-white p-3.5 shadow-2xl ring-1 ring-black/5 animate-[scaleIn_0.12s_ease-out]">
          <AlertsTrendChart alerts={alerts} />
          <h4 className="mb-2 mt-3 text-xs font-semibold text-gray-500">Active Alerts</h4>
          <div className="max-h-64 space-y-2 overflow-y-auto">
            {topAlerts.map((alert) => (
              <div
                key={alert.id}
                className={`rounded-lg border p-2.5 text-xs ${
                  alert.severity === "high"
                    ? "border-l-[3px] border-l-red-500 border-gray-200"
                    : alert.severity === "medium"
                      ? "border-l-[3px] border-l-amber-500 border-gray-200"
                      : "border-gray-200"
                }`}
              >
                <p className="font-medium text-gray-900">{alert.message}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {alert.category === "unconfirmed_visit" && Boolean(alert.metadata?.visit_id) && onExecuteAction && (
                    <button
                      className="rounded bg-indigo-600 px-2 py-0.5 text-[10px] font-medium text-white hover:bg-indigo-700"
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
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        alert.status === value
                          ? "bg-indigo-100 text-indigo-700"
                          : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                      }`}
                      disabled={alert.status === value}
                      onClick={() => void onUpdateAlert(alert.id, value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {openAlerts.length > 5 && (
            <p className="mt-2 text-center text-[10px] text-gray-400">
              + {openAlerts.length - 5} more alerts
            </p>
          )}
        </div>
      )}
    </div>
  );
}

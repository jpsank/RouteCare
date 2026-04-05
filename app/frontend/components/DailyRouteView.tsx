import { useMemo } from "react";
import type { Message, Visit } from "../types";
import { patientColor } from "./calendar/utils";

type Props = {
  visits: Visit[];
  date: string;
  onSendMessage?: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => Promise<Message | undefined>;
  homeOrigin?: { latitude: number; longitude: number } | null;
};

function fmt(dateStr: string): string {
  return new Date(dateStr).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function navigateUrl(lat?: number | null, lng?: number | null): string | null {
  if (lat == null || lng == null) return null;
  // Use universal maps link that works on both iOS and Android
  return `https://maps.google.com/maps?daddr=${lat},${lng}`;
}

export function DailyRouteView({ visits, date, onSendMessage, homeOrigin }: Props) {
  const dayVisits = useMemo(
    () =>
      visits
        .filter((v) => new Date(v.starts_at).toISOString().slice(0, 10) === date)
        .sort((a, b) => a.position_in_day - b.position_in_day),
    [visits, date],
  );

  const now = new Date();
  const nextVisitIdx = dayVisits.findIndex((v) => new Date(v.starts_at) > now);
  const completedCount = nextVisitIdx === -1 ? dayVisits.length : nextVisitIdx;
  const nextVisit = nextVisitIdx >= 0 ? dayVisits[nextVisitIdx] : null;

  const dateLabel = new Date(date + "T12:00:00").toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  if (dayVisits.length === 0) {
    return (
      <div className="rc-card py-8 text-center">
        <p className="text-sm text-gray-500">No visits scheduled for today.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">{dateLabel}</h2>
          <p className="text-xs text-gray-400">{dayVisits.length} visits &middot; {completedCount} done</p>
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          className="h-full rounded-full bg-indigo-500 transition-all"
          style={{ width: `${(completedCount / dayVisits.length) * 100}%` }}
        />
      </div>

      {/* Next stop hero card */}
      {nextVisit && (
        <div className="rounded-xl border-2 border-indigo-200 bg-indigo-50 p-4">
          <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-indigo-500">Next stop</p>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-base font-semibold text-gray-900">{nextVisit.patient_name}</p>
              <p className="text-xs text-gray-500">{nextVisit.patient_address || "No address"}</p>
              <p className="mt-1 text-sm font-medium text-indigo-700">{fmt(nextVisit.starts_at)}</p>
              {nextVisit.drive_from_previous_minutes > 0 && (
                <p className="text-xs text-gray-400">{nextVisit.drive_from_previous_minutes} min drive</p>
              )}
            </div>
            {navigateUrl(nextVisit.patient_latitude, nextVisit.patient_longitude) && (
              <a
                href={navigateUrl(nextVisit.patient_latitude, nextVisit.patient_longitude)!}
                target="_blank"
                rel="noreferrer"
                className="btn-primary btn-sm flex-none"
              >
                Navigate
              </a>
            )}
          </div>
        </div>
      )}

      {/* Full timeline */}
      <div className="space-y-1.5">
        {dayVisits.map((visit, idx) => {
          const pc = patientColor(visit.patient_id);
          const isPast = new Date(visit.ends_at) < now;
          const isCurrent = nextVisitIdx === idx;
          const navUrl = navigateUrl(visit.patient_latitude, visit.patient_longitude);

          return (
            <div
              key={visit.id}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors ${
                isCurrent ? "bg-indigo-50 border border-indigo-200" : isPast ? "opacity-50" : "bg-white border border-gray-100"
              }`}
            >
              <div className="flex flex-col items-center gap-0.5">
                <span
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-bold text-white"
                  style={{ backgroundColor: isPast ? "#9ca3af" : pc.accent }}
                >
                  {idx + 1}
                </span>
              </div>

              <div className="min-w-0 flex-1">
                <p className={`text-sm font-medium ${isPast ? "text-gray-400 line-through" : "text-gray-900"}`}>
                  {visit.patient_name}
                </p>
                <p className="text-xs text-gray-400">
                  {fmt(visit.starts_at)}
                  {visit.drive_from_previous_minutes > 0 && ` · ${visit.drive_from_previous_minutes}m drive`}
                </p>
              </div>

              <div className="flex flex-none items-center gap-1.5">
                {navUrl && !isPast && (
                  <a
                    href={navUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-400 hover:text-gray-600"
                    title="Navigate"
                  >
                    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9 6.75V15m6-6v8.25m.503 3.498 4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 0 0-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0Z" />
                    </svg>
                  </a>
                )}
                {onSendMessage && !isPast && visit.status === "pending_patient_confirmation" && (
                  <button
                    className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-400 hover:text-indigo-600"
                    title="Send confirmation"
                    onClick={() => onSendMessage(visit.id, "sms", "", true)}
                  >
                    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Navigate home */}
      {homeOrigin && (
        <a
          href={`https://maps.google.com/maps?daddr=${homeOrigin.latitude},${homeOrigin.longitude}`}
          target="_blank"
          rel="noreferrer"
          className="btn-secondary w-full justify-center text-xs"
        >
          Navigate home
        </a>
      )}
    </div>
  );
}

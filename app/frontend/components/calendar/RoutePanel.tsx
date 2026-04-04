import type { RefObject } from "react";
import type { CalendarBlock, Visit } from "../../types";
import { fmt, formatDistance, formatDuration, patientColor, type RouteSnapshot } from "./utils";

type Props = {
  selectedDate: string;
  dayVisits: Visit[];
  dayBlocks: CalendarBlock[];
  selectedVisitId: number | null;
  setSelectedVisitId: (id: number | null) => void;
  googleMapsUrl: string;
  appleMapsUrl: string;
  mapContainerRef: RefObject<HTMLDivElement | null>;
  routeLoading: boolean;
  routeSnapshot: RouteSnapshot | null;
  routeError: string | null;
};

function totalDriveMinutes(visits: Visit[]): number {
  return visits.reduce((sum, v) => sum + (v.drive_from_previous_minutes || 0), 0);
}

export function RoutePanel({
  selectedDate,
  dayVisits,
  dayBlocks,
  selectedVisitId,
  setSelectedVisitId,
  googleMapsUrl,
  appleMapsUrl,
  mapContainerRef,
  routeLoading,
  routeSnapshot,
  routeError,
}: Props) {
  const dateLabel = new Date(selectedDate + "T12:00:00").toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  const driveTotal = totalDriveMinutes(dayVisits);

  return (
    <aside className="rc-route-panel sticky top-3">
      {/* Header */}
      <div className="mb-2 flex items-center justify-between">
        <div className="min-w-0">
          <h3 className="text-[13px] font-semibold text-gray-900">{dateLabel}</h3>
          {dayVisits.length > 0 && (
            <p className="text-[11px] text-gray-400">
              {dayVisits.length} visit{dayVisits.length !== 1 ? "s" : ""}
              {driveTotal > 0 && <> &middot; {driveTotal} min driving</>}
            </p>
          )}
        </div>
        {dayVisits.length > 0 && (
          <div className="flex gap-1">
            <a href={googleMapsUrl} target="_blank" rel="noreferrer" className="rc-map-link" title="Open in Google Maps">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
            </a>
            <a href={appleMapsUrl} target="_blank" rel="noreferrer" className="rc-map-link" title="Open in Apple Maps">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>
            </a>
          </div>
        )}
      </div>

      {dayVisits.length === 0 ? (
        <div className="rounded-md border border-dashed border-gray-200 px-3 py-5 text-center text-xs text-gray-400">
          No visits scheduled
        </div>
      ) : (
        <div className="rc-timeline">
          {dayVisits.map((visit, idx) => {
            const pc = patientColor(visit.patient_id);
            const isActive = selectedVisitId === visit.id;
            const driveMin = visit.drive_from_previous_minutes || 0;
            const showTransit = idx > 0 || driveMin > 0;

            return (
              <div key={visit.id} className="rc-timeline-item">
                {showTransit && (
                  <div className="rc-timeline-transit">
                    <span className="rc-transit-chip">
                      {idx === 0 ? "From home" : "Drive"} &middot; {driveMin} min
                    </span>
                  </div>
                )}
                <div
                  className={`rc-timeline-stop${isActive ? " active" : ""}`}
                  onClick={() => setSelectedVisitId(visit.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedVisitId(visit.id); }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <span
                    className="rc-stop-dot"
                    style={{ backgroundColor: pc.accent }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-1">
                      <span className="truncate text-[13px] font-medium text-gray-900">{visit.patient_name}</span>
                      <span className="flex-none text-[11px] tabular-nums text-gray-400">{fmt(visit.starts_at)}</span>
                    </div>
                    <div className="truncate text-[11px] text-gray-400">{visit.patient_address || "No address"}</div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {dayBlocks.length > 0 && (
        <div className="mt-2 space-y-1">
          {dayBlocks.map((block) => (
            <div key={block.id} className="flex items-center gap-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-[11px]">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
              <span className="font-medium text-amber-800">{block.title || "Blocked"}</span>
              <span className="ml-auto text-amber-600">{fmt(block.starts_at)}–{fmt(block.ends_at)}</span>
            </div>
          ))}
        </div>
      )}

      {dayVisits.length > 0 && (
        <>
          <div ref={mapContainerRef} className="rc-map" />
          {routeLoading && <p className="mt-1.5 text-[11px] text-gray-400">Loading route...</p>}
          {routeSnapshot && (
            <div className="mt-2 flex items-center gap-3 text-[11px] text-gray-500">
              <span className="font-medium text-gray-700">{formatDistance(routeSnapshot.distanceMeters)}</span>
              <span>&middot;</span>
              <span className="font-medium text-gray-700">{formatDuration(routeSnapshot.durationSeconds)}</span>
              <span className="ml-auto text-gray-400">total route</span>
            </div>
          )}
          {routeError && <div className="rc-error mt-2">{routeError}</div>}
        </>
      )}
    </aside>
  );
}

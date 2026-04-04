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
  return (
    <aside className="rc-route-panel sticky top-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">
          {new Date(selectedDate + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}
        </h3>
        <div className="flex gap-1">
          <a href={googleMapsUrl} target="_blank" rel="noreferrer" className="rc-map-link" title="Google Maps">G</a>
          <a href={appleMapsUrl} target="_blank" rel="noreferrer" className="rc-map-link" title="Apple Maps">A</a>
        </div>
      </div>

      {dayVisits.length === 0 ? (
        <div className="rc-empty">No visits scheduled</div>
      ) : (
        <div className="space-y-1">
          {dayVisits.map((visit, idx) => (
            <div key={visit.id}>
              {(idx > 0 || (idx === 0 && (visit.drive_from_previous_minutes || 0) > 0)) && (
                <div className="ml-9 py-0.5">
                  <span className="rc-transit">
                    🚗 {idx === 0 ? "From home" : "Transit"}: {visit.drive_from_previous_minutes || 0} min
                  </span>
                </div>
              )}
              <div
                className={`rc-route-stop ${selectedVisitId === visit.id ? "active" : ""}`}
                onClick={() => setSelectedVisitId(visit.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedVisitId(visit.id); }
                }}
                role="button"
                tabIndex={0}
              >
                <span
                  className="rc-route-index"
                  style={{ backgroundColor: patientColor(visit.patient_id).bg, color: patientColor(visit.patient_id).text }}
                >{idx + 1}</span>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-gray-900">{visit.patient_name}</div>
                  <div className="text-xs text-gray-500">
                    {fmt(visit.starts_at)} – {fmt(visit.ends_at)}
                  </div>
                  <div className="truncate text-xs text-gray-400">{visit.patient_address || "No address"}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {dayBlocks.length > 0 && (
        <div className="mt-3 space-y-1">
          {dayBlocks.map((block) => (
            <div key={block.id} className="rc-alert-item sev-medium">
              <div className="text-sm font-medium text-gray-900">{block.title || "Personal event"}</div>
              <div className="text-xs text-gray-500">{fmt(block.starts_at)} – {fmt(block.ends_at)}</div>
            </div>
          ))}
        </div>
      )}

      {dayVisits.length > 0 && (
        <>
          <div ref={mapContainerRef} className="rc-map" />
          {routeLoading && <p className="mt-2 text-xs text-gray-500">Loading route...</p>}
          {routeSnapshot && (
            <div className="rc-route-summary">
              <div className="text-sm font-medium text-gray-800">
                {formatDistance(routeSnapshot.distanceMeters)} &middot; {formatDuration(routeSnapshot.durationSeconds)}
              </div>
              <ol className="mt-2 list-none space-y-1 p-0">
                {routeSnapshot.steps.slice(0, 6).map((step, idx) => (
                  <li key={`${step.instruction}-${idx}`} className="text-xs text-gray-600">
                    {step.instruction}
                    <span className="ml-1 text-gray-400">
                      ({formatDistance(step.distanceMeters)})
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {routeError && <div className="rc-error mt-2">{routeError}</div>}
        </>
      )}
    </aside>
  );
}

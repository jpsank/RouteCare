import type { RefObject } from "react";
import type { CalendarBlock, Visit } from "../../types";
import { fmt, formatDistance, formatDuration, type RouteSnapshot } from "./utils";

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
    <aside className="calendar-day-route sticky top-4">
      <div className="section-head">
        <h3 className="text-base font-bold text-slate-900">{new Date(selectedDate).toDateString()}</h3>
        <div className="route-links">
          <a href={googleMapsUrl} target="_blank" rel="noreferrer" className="map-icon-link" title="Open in Google Maps" aria-label="Open in Google Maps">
            G
          </a>
          <a href={appleMapsUrl} target="_blank" rel="noreferrer" className="map-icon-link" title="Open in Apple Maps" aria-label="Open in Apple Maps">
            A
          </a>
        </div>
      </div>

      {dayVisits.length === 0 ? (
        <p className="empty-state">No visits on this day.</p>
      ) : (
        <ol className="route-list">
          {dayVisits.map((visit, idx) => (
            <li key={visit.id}>
              {(idx > 0 || (idx === 0 && (visit.drive_from_previous_minutes || 0) > 0)) && (
                <div className="route-transit-meta">
                  <span className="transit-chip">
                    <span className="transit-icon" aria-hidden="true">
                      🚗
                    </span>
                    <span>
                      {idx === 0 ? "Drive from home" : "Drive from previous stop"}: {visit.drive_from_previous_minutes || 0} min
                    </span>
                  </span>
                </div>
              )}
              <div
                className={`route-stop ${selectedVisitId === visit.id ? "active bg-teal-50" : ""}`}
                onClick={() => setSelectedVisitId(visit.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedVisitId(visit.id);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <span className="route-index">{idx + 1}</span>
                <div className="route-stop-body">
                  <div className="route-stop-title truncate">{visit.patient_name}</div>
                  <div className="route-stop-meta">
                    {fmt(visit.starts_at)} - {fmt(visit.ends_at)}
                  </div>
                  <div className="route-stop-detail">{visit.patient_address || "No address on file"}</div>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      {dayBlocks.length > 0 && (
        <ul className="alerts-list mt-3">
          {dayBlocks.map((block) => (
            <li key={block.id} className="alert-item severity-medium">
              <p className="alert-message">{block.title || "Personal event"}</p>
              <div className="alert-meta">
                <span>
                  {fmt(block.starts_at)} - {fmt(block.ends_at)}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {dayVisits.length > 0 && (
        <>
          <div ref={mapContainerRef} className="route-map" />
          {routeLoading && <p className="section-meta">Loading turn-by-turn route...</p>}
          {routeSnapshot && (
            <div className="route-summary">
              <strong className="text-sm font-bold text-slate-800">
                {formatDistance(routeSnapshot.distanceMeters)} • {formatDuration(routeSnapshot.durationSeconds)}
              </strong>
              <ol className="direction-list">
                {routeSnapshot.steps.slice(0, 8).map((step, idx) => (
                  <li key={`${step.instruction}-${idx}`}>
                    <span>{step.instruction}</span>
                    <small>
                      {formatDistance(step.distanceMeters)} • {formatDuration(step.durationSeconds)}
                    </small>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {routeError && <div className="error">{routeError}</div>}
        </>
      )}
    </aside>
  );
}

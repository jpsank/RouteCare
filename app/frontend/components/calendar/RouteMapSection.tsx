import type { Visit } from "../../types";
import { useRouteMap } from "./hooks/useRouteMap";
import { formatDistance, formatDuration, type Point } from "./utils";

type Props = {
  dayVisits: Visit[];
  selectedDate: string;
  homeOrigin?: Point | null;
  selectedVisitId?: number | null;
};

export function RouteMapSection({ dayVisits, selectedDate, homeOrigin, selectedVisitId }: Props) {
  const {
    mapContainerRef,
    routeLoading,
    routeSnapshot,
    routeError,
    mapUnavailable,
    googleMapsUrl,
    appleMapsUrl,
  } = useRouteMap({ dayVisits, selectedDate, homeOrigin, selectedVisitId });

  return (
    <>
      <div className="relative">
        <div ref={mapContainerRef} className="rc-map" />
        {mapUnavailable && (
          <div className="rc-map absolute inset-0 mt-0 flex flex-col items-center justify-center gap-2 bg-gray-50 text-center">
            <span className="text-[11px] text-gray-400">Map unavailable</span>
            <div className="flex gap-1">
              <a href={googleMapsUrl} target="_blank" rel="noreferrer" className="rc-map-link" title="Open in Google Maps">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
              </a>
              <a href={appleMapsUrl} target="_blank" rel="noreferrer" className="rc-map-link" title="Open in Apple Maps">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>
              </a>
            </div>
          </div>
        )}
      </div>
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
  );
}

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
  } = useRouteMap({ dayVisits, selectedDate, homeOrigin, selectedVisitId });

  return (
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
  );
}

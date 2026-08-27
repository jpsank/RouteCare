import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Visit } from "../../../types";
import { googleMapsKey, mapboxToken } from "../mapProviders";
import {
  buildAppleMapsUrl,
  buildGoogleMapsUrl,
  fetchRouteSnapshot,
  patientColor,
  type Point,
  type RouteSnapshot,
} from "../utils";
import { initGoogleRenderer } from "./mapRenderers/googleRenderer";
import { initMapboxRenderer } from "./mapRenderers/mapboxRenderer";
import { initOsmRenderer } from "./mapRenderers/osmRenderer";
import type { MapMarker, RouteRenderer } from "./mapRenderers/types";

type Args = {
  dayVisits: Visit[];
  selectedDate: string;
  homeOrigin?: Point | null;
  selectedVisitId?: number | null;
};

type RouteStop = Point & {
  patientId: number;
  patientName: string;
  patientAddress: string;
  startsAt: string;
  endsAt: string;
};

// Tries Mapbox first, then Google Maps if Mapbox is unconfigured or fails to load
// (e.g. a suspended/invalid token), then falls back to the free/keyless OSM
// renderer, which has no configuration prerequisite and is the guaranteed floor.
async function initRenderer(container: HTMLDivElement): Promise<RouteRenderer | null> {
  const mapbox = mapboxToken();
  if (mapbox) {
    try {
      return await initMapboxRenderer(container, mapbox);
    } catch (error) {
      console.warn("Mapbox map failed to load, falling back to Google Maps:", error);
    }
  }

  const google = googleMapsKey();
  if (google) {
    try {
      return await initGoogleRenderer(container, google);
    } catch (error) {
      console.warn("Google Maps failed to load, falling back to OpenStreetMap:", error);
    }
  }

  try {
    return await initOsmRenderer(container);
  } catch (error) {
    console.warn("OpenStreetMap renderer failed to load:", error);
  }

  return null;
}

export function useRouteMap({ dayVisits, selectedDate, homeOrigin, selectedVisitId }: Args) {
  const [routeError, setRouteError] = useState<string | null>(null);
  const [routeLoading, setRouteLoading] = useState(false);
  const [routeSnapshot, setRouteSnapshot] = useState<RouteSnapshot | null>(null);
  const [mapUnavailable, setMapUnavailable] = useState(false);

  const rendererRef = useRef<RouteRenderer | null>(null);
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const markerDefsRef = useRef<MapMarker[]>([]);
  const requestIdRef = useRef(0);
  // Guards against React StrictMode's dev-only double-invoke of effects: since
  // initRenderer can't be cancelled mid-flight, rendererInitStartedRef stops a
  // second concurrent call, and rendererActiveRef (re-armed by the second
  // effect run) decides whether the eventual result should be kept or
  // destroyed — correctly distinguishing "StrictMode replayed this effect"
  // from "the component actually unmounted before init finished."
  const rendererInitStartedRef = useRef(false);
  const rendererActiveRef = useRef(false);

  const routableStops = useMemo(
    () =>
      dayVisits
        .filter((visit) => visit.patient_latitude != null && visit.patient_longitude != null)
        .map((visit) => ({
          latitude: Number(visit.patient_latitude),
          longitude: Number(visit.patient_longitude),
          patientId: visit.patient_id,
          patientName: visit.patient_name,
          patientAddress: visit.patient_address || "No address on file",
          startsAt: visit.starts_at,
          endsAt: visit.ends_at,
        })),
    [dayVisits],
  );

  const routePlan = useMemo(() => {
    if (routableStops.length < 1) return null;
    const origin = homeOrigin || routableStops[0];
    const stops = homeOrigin ? routableStops : routableStops.slice(1);
    return { origin, stops };
  }, [homeOrigin, routableStops]);

  const googleMapsUrl = useMemo(
    () => buildGoogleMapsUrl(routableStops, homeOrigin || undefined),
    [routableStops, homeOrigin],
  );
  const appleMapsUrl = useMemo(
    () => buildAppleMapsUrl(routableStops, homeOrigin || undefined),
    [routableStops, homeOrigin],
  );

  useEffect(() => {
    if (!routePlan || routePlan.stops.length === 0) {
      requestIdRef.current += 1;
      setRouteSnapshot(null);
      setRouteError(null);
      setRouteLoading(false);
      rendererRef.current?.clearRoute();
      return;
    }
    const requestId = ++requestIdRef.current;
    setRouteLoading(true);
    setRouteError(null);
    setRouteSnapshot(null);
    fetchRouteSnapshot(routePlan.origin, routePlan.stops)
      .then((snapshot) => {
        if (requestIdRef.current !== requestId) return;
        setRouteSnapshot(snapshot);
      })
      .catch((error) => {
        if (requestIdRef.current !== requestId) return;
        setRouteError((error as Error).message);
      })
      .finally(() => {
        if (requestIdRef.current !== requestId) return;
        setRouteLoading(false);
      });
  }, [routePlan, selectedDate]);

  // Initialize the map renderer once on mount, destroy on unmount.
  useEffect(() => {
    const container = mapContainerRef.current;
    if (!container) return;

    rendererActiveRef.current = true;

    if (!rendererInitStartedRef.current) {
      rendererInitStartedRef.current = true;
      void initRenderer(container).then((renderer) => {
        rendererInitStartedRef.current = false;
        if (!rendererActiveRef.current) {
          renderer?.destroy();
          return;
        }
        if (!renderer) {
          setMapUnavailable(true);
          return;
        }
        rendererRef.current = renderer;
      });
    }

    return () => {
      rendererActiveRef.current = false;
      rendererRef.current?.destroy();
      rendererRef.current = null;
    };
  }, []);

  // Draw route + markers when snapshot or plan changes.
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;

    if (!routeSnapshot || !routePlan) {
      renderer.clearRoute();
      return;
    }

    renderer.drawRoute(routeSnapshot.geometry);

    const allPoints = [routePlan.origin, ...routePlan.stops].filter(Boolean) as Array<Point | RouteStop>;
    const markerDefs: MapMarker[] = allPoints.map((point, idx) => {
      const isHome = idx === 0 && homeOrigin != null;
      const stop = point as RouteStop;

      if (isHome) {
        return { id: "home", lat: point.latitude, lng: point.longitude, color: "#6b7280", label: "H", popupText: "Home" };
      }

      const pc = patientColor(stop.patientId);
      const stopNumber = homeOrigin ? idx : idx + 1;
      const popupText = `${stopNumber}. ${stop.patientName}\n${stop.patientAddress}\n${new Date(stop.startsAt).toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
      })} – ${new Date(stop.endsAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;

      const visitMatch = dayVisits.find((v) => v.patient_id === stop.patientId && v.starts_at === stop.startsAt);

      return {
        id: visitMatch ? String(visitMatch.id) : `stop-${idx}`,
        visitId: visitMatch?.id,
        lat: point.latitude,
        lng: point.longitude,
        color: pc.accent,
        label: String(stopNumber),
        popupText,
      };
    });

    markerDefsRef.current = markerDefs;
    renderer.setMarkers(markerDefs);
    renderer.fitBounds(allPoints.map((p) => ({ lat: p.latitude, lng: p.longitude })));
  }, [routeSnapshot, routePlan, homeOrigin, dayVisits]);

  // Highlight selected marker.
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    markerDefsRef.current.forEach((def) => {
      renderer.setMarkerHighlighted(def.id, def.visitId != null && def.visitId === selectedVisitId);
    });
  }, [selectedVisitId]);

  return {
    mapContainerRef,
    routeLoading,
    routeSnapshot,
    routeError,
    mapUnavailable,
    googleMapsUrl,
    appleMapsUrl,
  };
}

import mapboxgl from "mapbox-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Visit } from "../../../types";
import { buildAppleMapsUrl, buildGoogleMapsUrl, fetchRouteSnapshot, type Point, type RouteSnapshot } from "../utils";

const MAPBOX_FALLBACK_PUBLIC_TOKEN =
  "pk.eyJ1IjoicHVmZnlib2EiLCJhIjoiY2sxbXNqbng1MDQ1cDNocWQ1bGVucGwxYyJ9.BsdxpULi2RpbCiaEyW3rgA";

function mapboxToken(): string {
  return (import.meta.env.VITE_MAPBOX_TOKEN as string | undefined) || MAPBOX_FALLBACK_PUBLIC_TOKEN;
}

const ROUTE_SOURCE_ID = "daily-route";
const ROUTE_LAYER_ID = "daily-route-line";

type Args = {
  dayVisits: Visit[];
  selectedDate: string;
  homeOrigin?: Point | null;
};

type RouteStop = Point & {
  patientName: string;
  patientAddress: string;
  startsAt: string;
  endsAt: string;
};

export function useRouteMap({ dayVisits, selectedDate, homeOrigin }: Args) {
  const [routeError, setRouteError] = useState<string | null>(null);
  const [routeLoading, setRouteLoading] = useState(false);
  const [routeSnapshot, setRouteSnapshot] = useState<RouteSnapshot | null>(null);

  const mapRef = useRef<mapboxgl.Map | null>(null);
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const markersRef = useRef<mapboxgl.Marker[]>([]);
  const requestIdRef = useRef(0);

  const clearRenderedRoute = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;

    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current = [];

    if (map.getLayer(ROUTE_LAYER_ID)) map.removeLayer(ROUTE_LAYER_ID);
    if (map.getSource(ROUTE_SOURCE_ID)) map.removeSource(ROUTE_SOURCE_ID);
  }, []);

  const routableStops = useMemo(
    () =>
      dayVisits
        .filter((visit) => visit.patient_latitude != null && visit.patient_longitude != null)
        .map((visit) => ({
          latitude: Number(visit.patient_latitude),
          longitude: Number(visit.patient_longitude),
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
      requestIdRef.current += 1; // Cancel any in-flight route fetch.
      setRouteSnapshot(null);
      setRouteError(null);
      setRouteLoading(false);
      clearRenderedRoute();
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
      return;
    }
    const requestId = ++requestIdRef.current;
    setRouteLoading(true);
    setRouteError(null);
    setRouteSnapshot(null);
    fetchRouteSnapshot(mapboxToken(), routePlan.origin, routePlan.stops)
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
  }, [routePlan, selectedDate, clearRenderedRoute]);

  useEffect(() => {
    const container = mapContainerRef.current;
    if (!container) return;
    if (!mapRef.current) {
      mapRef.current = new mapboxgl.Map({
        container,
        style: "mapbox://styles/mapbox/streets-v12",
        center: [-96, 37.8],
        zoom: 3,
        accessToken: mapboxToken(),
      });
      mapRef.current.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");
    }
    if (!routeSnapshot) return;

    const map = mapRef.current;
    const draw = () => {
      const geojson: GeoJSON.FeatureCollection = {
        type: "FeatureCollection",
        features: [{ type: "Feature", geometry: routeSnapshot.geometry, properties: {} }],
      };
      if (map.getSource(ROUTE_SOURCE_ID)) (map.getSource(ROUTE_SOURCE_ID) as mapboxgl.GeoJSONSource).setData(geojson);
      else {
        map.addSource(ROUTE_SOURCE_ID, { type: "geojson", data: geojson });
        map.addLayer({ id: ROUTE_LAYER_ID, type: "line", source: ROUTE_SOURCE_ID, paint: { "line-color": "#0f766e", "line-width": 5 } });
      }

      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current = [];
      const points = [routePlan?.origin, ...(routePlan?.stops || [])].filter(Boolean) as Array<Point | RouteStop>;
      points.forEach((point, idx) => {
        const stop = point as RouteStop;
        const popupText =
          idx === 0
            ? "Start"
            : `${stop.patientName}\n${stop.patientAddress}\n${new Date(stop.startsAt).toLocaleTimeString([], {
                hour: "numeric",
                minute: "2-digit",
              })} - ${new Date(stop.endsAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
        const marker = new mapboxgl.Marker({ color: idx === 0 ? "#0f766e" : "#1d4ed8" })
          .setLngLat([point.longitude, point.latitude])
          .setPopup(new mapboxgl.Popup({ offset: 14 }).setText(popupText))
          .addTo(map);
        markersRef.current.push(marker);
      });
      const bounds = new mapboxgl.LngLatBounds();
      points.forEach((point) => bounds.extend([point.longitude, point.latitude]));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 48, maxZoom: 12 });
    };
    if (map.isStyleLoaded()) draw();
    else map.once("load", draw);
  }, [routeSnapshot, routePlan]);

  return {
    mapContainerRef,
    routeLoading,
    routeSnapshot,
    routeError,
    googleMapsUrl,
    appleMapsUrl,
  };
}

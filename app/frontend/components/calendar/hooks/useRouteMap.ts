import mapboxgl from "mapbox-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Visit } from "../../../types";
import {
  buildAppleMapsUrl,
  buildGoogleMapsUrl,
  fetchRouteSnapshot,
  patientColor,
  type Point,
  type RouteSnapshot,
} from "../utils";

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
  patientId: number;
  patientName: string;
  patientAddress: string;
  startsAt: string;
  endsAt: string;
};

function createDotElement(color: string, label?: string): HTMLElement {
  const el = document.createElement("div");
  el.style.width = "16px";
  el.style.height = "16px";
  el.style.borderRadius = "50%";
  el.style.backgroundColor = color;
  el.style.border = "2px solid #fff";
  el.style.boxShadow = "0 1px 4px rgba(0,0,0,0.18)";
  el.style.cursor = "pointer";
  if (label) {
    el.style.display = "flex";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.style.fontSize = "8px";
    el.style.fontWeight = "700";
    el.style.color = "#fff";
    el.textContent = label;
  }
  return el;
}

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
        style: "mapbox://styles/mapbox/light-v11",
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
        map.addLayer({
          id: ROUTE_LAYER_ID,
          type: "line",
          source: ROUTE_SOURCE_ID,
          paint: { "line-color": "#6366f1", "line-width": 3, "line-opacity": 0.7 },
        });
      }

      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current = [];
      const allPoints = [routePlan?.origin, ...(routePlan?.stops || [])].filter(Boolean) as Array<Point | RouteStop>;
      allPoints.forEach((point, idx) => {
        const isHome = idx === 0 && homeOrigin != null;
        const stop = point as RouteStop;

        let el: HTMLElement;
        let popupText: string;

        if (isHome) {
          el = createDotElement("#6b7280", "H");
          popupText = "Home";
        } else {
          const pc = patientColor(stop.patientId);
          el = createDotElement(pc.accent);
          popupText = `${stop.patientName}\n${stop.patientAddress}\n${new Date(stop.startsAt).toLocaleTimeString([], {
            hour: "numeric",
            minute: "2-digit",
          })} – ${new Date(stop.endsAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
        }

        const marker = new mapboxgl.Marker({ element: el, anchor: "center" })
          .setLngLat([point.longitude, point.latitude])
          .setPopup(new mapboxgl.Popup({ offset: 12, closeButton: false }).setText(popupText))
          .addTo(map);
        markersRef.current.push(marker);
      });

      const bounds = new mapboxgl.LngLatBounds();
      allPoints.forEach((point) => bounds.extend([point.longitude, point.latitude]));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 40, maxZoom: 13 });
    };
    if (map.isStyleLoaded()) draw();
    else map.once("load", draw);
  }, [routeSnapshot, routePlan, homeOrigin]);

  return {
    mapContainerRef,
    routeLoading,
    routeSnapshot,
    routeError,
    googleMapsUrl,
    appleMapsUrl,
  };
}

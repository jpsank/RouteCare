import type mapboxgl from "mapbox-gl";
import type { MapMarker, RouteRenderer } from "./types";
import { DOT_BORDER, DOT_LABEL_FONT_SIZE, DOT_LABEL_FONT_WEIGHT, DOT_SHADOW, dotDiameter } from "./utils";

const ROUTE_SOURCE_ID = "daily-route";
const ROUTE_LAYER_ID = "daily-route-line";

function createDotElement(color: string, label?: string): HTMLElement {
  const el = document.createElement("div");
  const hasLabel = Boolean(label);
  const size = `${dotDiameter(hasLabel)}px`;
  el.style.width = size;
  el.style.height = size;
  el.style.borderRadius = "50%";
  el.style.backgroundColor = color;
  el.style.border = DOT_BORDER;
  el.style.boxShadow = DOT_SHADOW;
  el.style.cursor = "pointer";
  el.style.transition = "width 0.15s, height 0.15s, border 0.15s, box-shadow 0.15s";
  if (hasLabel) {
    el.style.display = "flex";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.style.fontSize = DOT_LABEL_FONT_SIZE;
    el.style.fontWeight = DOT_LABEL_FONT_WEIGHT;
    el.style.color = "#fff";
    el.style.lineHeight = "1";
    el.textContent = label!;
  }
  return el;
}

function applyHighlight(el: HTMLElement, highlighted: boolean): void {
  if (highlighted) {
    el.style.width = "24px";
    el.style.height = "24px";
    el.style.fontSize = DOT_LABEL_FONT_SIZE;
    el.style.border = "2.5px solid #4f46e5";
    el.style.boxShadow = `0 0 0 2px rgba(79,70,229,0.2), ${DOT_SHADOW}`;
    el.style.zIndex = "10";
  } else {
    const size = `${dotDiameter(true)}px`;
    el.style.width = size;
    el.style.height = size;
    el.style.fontSize = DOT_LABEL_FONT_SIZE;
    el.style.border = DOT_BORDER;
    el.style.boxShadow = DOT_SHADOW;
    el.style.zIndex = "";
  }
}

const LOAD_TIMEOUT_MS = 8000;

// A 401/403 on the style/tile request means the token itself is bad (e.g.
// suspended) — no amount of waiting fixes that, so fail over immediately.
// Anything else (a missing sprite icon, a single dropped tile, a transient
// blip) is often recoverable and shouldn't abandon an otherwise-working map;
// those just fall through to the load timeout below instead.
function isAuthError(error: unknown): boolean {
  const status = (error as { status?: number } | undefined)?.status;
  return status === 401 || status === 403;
}

/**
 * Resolves once Mapbox's initial style finishes loading; rejects on an auth
 * error (bad/suspended token) or if loading doesn't complete within a
 * timeout, so the caller can fall back to another provider.
 */
export async function initMapboxRenderer(container: HTMLDivElement, token: string): Promise<RouteRenderer> {
  const mapbox = (await import("mapbox-gl")).default;

  return new Promise((resolve, reject) => {
    const map = new mapbox.Map({
      container,
      style: "mapbox://styles/mapbox/light-v11",
      center: [-96, 37.8],
      zoom: 3,
      accessToken: token,
    });

    let settled = false;

    const fail = (error: unknown) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      map.remove();
      reject(error instanceof Error ? error : new Error("Mapbox failed to load"));
    };

    const timeoutId = setTimeout(() => fail(new Error("Mapbox timed out loading")), LOAD_TIMEOUT_MS);

    map.on("error", (e: mapboxgl.ErrorEvent) => {
      if (isAuthError(e.error)) fail(e.error);
      // Non-auth errors are logged by mapbox-gl itself; let the load timeout
      // above be the final word on whether the map actually came up.
    });

    map.on("load", () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      map.addControl(new mapbox.NavigationControl({ showCompass: false }), "top-right");
      resolve(buildRenderer(map, mapbox));
    });
  });
}

function buildRenderer(map: mapboxgl.Map, mapbox: typeof mapboxgl): RouteRenderer {
  let markers: mapboxgl.Marker[] = [];
  const markerElements = new Map<string, HTMLElement>();

  return {
    drawRoute(geometry) {
      const geojson: GeoJSON.FeatureCollection = {
        type: "FeatureCollection",
        features: [ { type: "Feature", geometry, properties: {} } ],
      };
      if (map.getSource(ROUTE_SOURCE_ID)) {
        (map.getSource(ROUTE_SOURCE_ID) as mapboxgl.GeoJSONSource).setData(geojson);
      } else {
        map.addSource(ROUTE_SOURCE_ID, { type: "geojson", data: geojson });
        map.addLayer({
          id: ROUTE_LAYER_ID,
          type: "line",
          source: ROUTE_SOURCE_ID,
          paint: { "line-color": "#f97316", "line-width": 3, "line-opacity": 0.7 },
        });
      }
    },

    clearRoute() {
      if (map.getLayer(ROUTE_LAYER_ID)) map.removeLayer(ROUTE_LAYER_ID);
      if (map.getSource(ROUTE_SOURCE_ID)) map.removeSource(ROUTE_SOURCE_ID);
    },

    setMarkers(markerDefs: MapMarker[]) {
      markers.forEach((m) => m.remove());
      markers = [];
      markerElements.clear();
      markerDefs.forEach((def) => {
        const el = createDotElement(def.color, def.label);
        markerElements.set(def.id, el);
        const marker = new mapbox.Marker({ element: el, anchor: "center" })
          .setLngLat([ def.lng, def.lat ])
          .setPopup(new mapbox.Popup({ offset: 12, closeButton: false }).setText(def.popupText))
          .addTo(map);
        markers.push(marker);
      });
    },

    setMarkerHighlighted(id, highlighted) {
      const el = markerElements.get(id);
      if (el) applyHighlight(el, highlighted);
    },

    fitBounds(points) {
      if (points.length === 0) return;
      const bounds = new mapbox.LngLatBounds();
      points.forEach((p) => bounds.extend([ p.lng, p.lat ]));
      if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 40, maxZoom: 13, duration: 500 });
    },

    destroy() {
      markers.forEach((m) => m.remove());
      markers = [];
      map.remove();
    },
  };
}

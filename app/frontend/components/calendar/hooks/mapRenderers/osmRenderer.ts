import type * as Leaflet from "leaflet";
import type { MapMarker, RouteRenderer } from "./types";
import { DOT_BORDER, DOT_LABEL_FONT_SIZE, DOT_LABEL_FONT_WEIGHT, DOT_SHADOW, dotDiameter, escapeHtml, popupHtml } from "./utils";

// Free, keyless last-resort renderer using OpenStreetMap tiles via Leaflet.
// No API key required, so unlike Mapbox/Google this always succeeds as long as
// the container mounts — it's the guaranteed floor beneath the other two tiers.
export async function initOsmRenderer(container: HTMLDivElement): Promise<RouteRenderer> {
  const [ L ] = await Promise.all([
    import("leaflet").then((m) => m.default),
    import("leaflet/dist/leaflet.css"),
  ]);

  const map = L.map(container, { attributionControl: true }).setView([ 37.8, -96 ], 3);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors",
  }).addTo(map);

  return buildRenderer(L, map);
}

function buildRenderer(L: typeof Leaflet, map: Leaflet.Map): RouteRenderer {
  let polyline: Leaflet.Polyline | null = null;
  let markers: Leaflet.Marker[] = [];
  const markerById = new Map<string, Leaflet.Marker>();

  return {
    drawRoute(geometry) {
      polyline?.remove();
      const latlngs = geometry.coordinates.map(([ lng, lat ]) => [ lat, lng ] as [number, number]);
      polyline = L.polyline(latlngs, { color: "#f97316", weight: 3, opacity: 0.7 }).addTo(map);
    },

    clearRoute() {
      polyline?.remove();
      polyline = null;
    },

    setMarkers(markerDefs: MapMarker[]) {
      markers.forEach((m) => m.remove());
      markers = [];
      markerById.clear();

      markerDefs.forEach((def) => {
        const marker = L.marker([ def.lat, def.lng ], { icon: buildDotIcon(L, def.color, def.label) }).addTo(map);
        marker.bindPopup(popupHtml(def.popupText));
        markerById.set(def.id, marker);
        markers.push(marker);
      });
    },

    setMarkerHighlighted(id, highlighted) {
      const marker = markerById.get(id);
      const el = marker?.getElement();
      if (el) el.style.zIndex = highlighted ? "1000" : "";
    },

    fitBounds(points) {
      if (points.length === 0) return;
      const bounds = L.latLngBounds(points.map((p) => [ p.lat, p.lng ] as [number, number]));
      map.fitBounds(bounds, { padding: [ 40, 40 ], maxZoom: 13 });
    },

    destroy() {
      markers.forEach((m) => m.remove());
      markers = [];
      polyline?.remove();
      polyline = null;
      map.remove();
    },
  };
}

function buildDotIcon(L: typeof Leaflet, color: string, label?: string): Leaflet.DivIcon {
  const hasLabel = Boolean(label);
  const size = dotDiameter(hasLabel);
  const html = `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};border:${DOT_BORDER};box-shadow:${DOT_SHADOW};display:flex;align-items:center;justify-content:center;font-size:${DOT_LABEL_FONT_SIZE};font-weight:${DOT_LABEL_FONT_WEIGHT};color:#fff;">${hasLabel ? escapeHtml(label!) : ""}</div>`;
  return L.divIcon({
    className: "",
    html,
    iconSize: [ size + 4, size + 4 ],
    iconAnchor: [ (size + 4) / 2, (size + 4) / 2 ],
  });
}

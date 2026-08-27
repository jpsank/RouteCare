import { importLibrary, setOptions } from "@googlemaps/js-api-loader";
import type { MapMarker, RouteRenderer } from "./types";
import { dotDiameter, escapeHtml, popupHtml } from "./utils";

let optionsConfigured = false;

function configure(apiKey: string): void {
  if (optionsConfigured) return;
  setOptions({ key: apiKey, v: "weekly" });
  optionsConfigured = true;
}

export async function initGoogleRenderer(container: HTMLDivElement, apiKey: string): Promise<RouteRenderer> {
  configure(apiKey);

  const [ { Map, InfoWindow, Polyline }, { Marker } ] = await Promise.all([
    importLibrary("maps") as Promise<google.maps.MapsLibrary>,
    importLibrary("marker") as Promise<google.maps.MarkerLibrary>,
  ]);

  const map = new Map(container, {
    center: { lat: 37.8, lng: -96 },
    zoom: 3,
    disableDefaultUI: false,
    streetViewControl: false,
    fullscreenControl: false,
    mapTypeControl: false,
  });

  return buildRenderer(map, { InfoWindow, Polyline, Marker });
}

// google.maps.Marker is deprecated in favor of AdvancedMarkerElement, but the
// latter requires a Cloud Console Map ID to render custom HTML content. This
// is a fallback renderer exercised only when Mapbox is unavailable, so the
// classic Marker (still fully supported) keeps setup to just an API key.
type GoogleCtors = {
  InfoWindow: typeof google.maps.InfoWindow;
  Polyline: typeof google.maps.Polyline;
  Marker: typeof google.maps.Marker;
};

function buildRenderer(map: google.maps.Map, { InfoWindow, Polyline, Marker }: GoogleCtors): RouteRenderer {
  let polyline: google.maps.Polyline | null = null;
  let markers: google.maps.Marker[] = [];
  const markerById = new Map<string, google.maps.Marker>();
  let openInfoWindow: google.maps.InfoWindow | null = null;

  return {
    drawRoute(geometry) {
      polyline?.setMap(null);
      const path = geometry.coordinates.map(([ lng, lat ]) => ({ lat, lng }));
      polyline = new Polyline({
        path,
        strokeColor: "#f97316",
        strokeOpacity: 0.7,
        strokeWeight: 3,
        map,
      });
    },

    clearRoute() {
      polyline?.setMap(null);
      polyline = null;
    },

    setMarkers(markerDefs: MapMarker[]) {
      markers.forEach((m) => m.setMap(null));
      markers = [];
      markerById.clear();

      markerDefs.forEach((def) => {
        const marker = new Marker({
          position: { lat: def.lat, lng: def.lng },
          map,
          icon: buildDotIcon(def.color, def.label),
          zIndex: 1,
        });
        marker.addListener("click", () => {
          openInfoWindow?.close();
          openInfoWindow = new InfoWindow({ content: popupHtml(def.popupText) });
          openInfoWindow.open({ map, anchor: marker });
        });
        markerById.set(def.id, marker);
        markers.push(marker);
      });
    },

    setMarkerHighlighted(id, highlighted) {
      const marker = markerById.get(id);
      marker?.setZIndex(highlighted ? 10 : 1);
    },

    fitBounds(points) {
      if (points.length === 0) return;
      const bounds = new google.maps.LatLngBounds();
      points.forEach((p) => bounds.extend(p));
      map.fitBounds(bounds, 40);
    },

    destroy() {
      markers.forEach((m) => m.setMap(null));
      markers = [];
      polyline?.setMap(null);
      polyline = null;
      openInfoWindow?.close();
    },
  };
}

function buildDotIcon(color: string, label?: string): google.maps.Icon {
  const hasLabel = Boolean(label);
  const diameter = dotDiameter(hasLabel);
  const size = diameter + 4;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">
    <circle cx="${size / 2}" cy="${size / 2}" r="${diameter / 2}" fill="${color}" stroke="#fff" stroke-width="2"/>
    ${hasLabel ? `<text x="${size / 2}" y="${size / 2 + 3}" font-size="10" font-weight="700" fill="#fff" text-anchor="middle" font-family="sans-serif">${escapeHtml(label!)}</text>` : ""}
  </svg>`;
  return {
    url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    scaledSize: new google.maps.Size(size, size),
    anchor: new google.maps.Point(size / 2, size / 2),
  };
}

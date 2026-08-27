export type MapMarker = {
  id: string;
  lat: number;
  lng: number;
  color: string;
  label?: string;
  popupText: string;
  // The visit this marker represents, or undefined for the home marker.
  // Lets callers highlight the selected marker without a separate lookup table.
  visitId?: number;
};

export type RouteRenderer = {
  drawRoute(geometry: GeoJSON.LineString): void;
  clearRoute(): void;
  setMarkers(markers: MapMarker[]): void;
  setMarkerHighlighted(id: string, highlighted: boolean): void;
  fitBounds(points: Array<{ lat: number; lng: number }>): void;
  destroy(): void;
};

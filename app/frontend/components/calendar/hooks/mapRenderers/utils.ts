// Shared visual spec + helpers for the marker dot icons and popups, so the
// three renderers (Mapbox DOM elements, Google SVG data-URIs, Leaflet divIcon
// HTML strings) stay visually consistent without each hand-copying the same
// sizes/colors/escaping logic in its own rendering technology.

export const DOT_DIAMETER = 16;
export const DOT_DIAMETER_LABELED = 22;
export const DOT_BORDER = "2px solid #fff";
export const DOT_SHADOW = "0 1px 4px rgba(0,0,0,0.18)";
export const DOT_LABEL_FONT_SIZE = "10px";
export const DOT_LABEL_FONT_WEIGHT = "700";

export function dotDiameter(hasLabel: boolean): number {
  return hasLabel ? DOT_DIAMETER_LABELED : DOT_DIAMETER;
}

export function escapeHtml(text: string): string {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

export function popupHtml(text: string): string {
  return escapeHtml(text).replace(/\n/g, "<br/>");
}

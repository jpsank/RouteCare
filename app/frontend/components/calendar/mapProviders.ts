export function mapboxToken(): string | undefined {
  return import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;
}

export function googleMapsKey(): string | undefined {
  return import.meta.env.VITE_GOOGLE_MAPS_API_KEY as string | undefined;
}

import { useEffect, useMemo, useState } from "react";
import { api } from "../../../lib/api";
import type { CalendarConnection, CalendarOption } from "../../../types";

type Args = {
  calendarConnections: CalendarConnection[];
  selectedDate: string;
  onCalendarRefresh: () => Promise<void>;
};

export function useCalendarConnections({ calendarConnections, selectedDate, onCalendarRefresh }: Args) {
  const [connectingProvider, setConnectingProvider] = useState<"google" | "apple">("google");
  const [externalCalendarId, setExternalCalendarId] = useState("primary");
  const [appleIcsUrl, setAppleIcsUrl] = useState("");
  const [googleCalendars, setGoogleCalendars] = useState<CalendarOption[]>([]);
  const [selectedGoogleCalendarId, setSelectedGoogleCalendarId] = useState("");
  const [calendarConfigMessage, setCalendarConfigMessage] = useState<string | null>(null);

  const googleConnection = useMemo(
    () => calendarConnections.find((connection) => connection.provider === "google"),
    [calendarConnections],
  );

  useEffect(() => {
    if (!googleConnection) return;
    setSelectedGoogleCalendarId(googleConnection.external_calendar_id || "primary");
  }, [googleConnection]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!googleConnection || params.get("google_connected") !== "1") return;
    api
      .listConnectionCalendars(googleConnection.id)
      .then((result) => setGoogleCalendars(result.calendars))
      .finally(() => {
        params.delete("google_connected");
        const query = params.toString();
        window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
      });
  }, [googleConnection]);

  const connectCalendar = async () => {
    if (connectingProvider === "google") {
      window.location.href = "/auth/google/start";
      return;
    }
    await api.connectCalendar({
      provider: "apple",
      external_calendar_id: externalCalendarId,
      metadata: { ics_url: appleIcsUrl },
    });
    setCalendarConfigMessage("Apple calendar connected.");
    await onCalendarRefresh();
  };

  const syncConnection = async (connectionId: number) => {
    const weekStart = selectedDate;
    const weekEnd = new Date(new Date(selectedDate).getTime() + 6 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    await api.syncCalendarConnection(connectionId, weekStart, weekEnd);
    await onCalendarRefresh();
  };

  const pushToConnection = async (connectionId: number) => {
    await api.pushVisitsToCalendar(connectionId, selectedDate);
    await onCalendarRefresh();
  };

  const loadGoogleCalendars = async () => {
    if (!googleConnection) return;
    const result = await api.listConnectionCalendars(googleConnection.id);
    setGoogleCalendars(result.calendars);
  };

  const saveGoogleCalendarSelection = async () => {
    if (!googleConnection || !selectedGoogleCalendarId) return;
    await api.selectConnectionCalendar(googleConnection.id, selectedGoogleCalendarId);
    setCalendarConfigMessage("Google target calendar saved.");
    await onCalendarRefresh();
  };

  return {
    connectingProvider,
    setConnectingProvider,
    externalCalendarId,
    setExternalCalendarId,
    appleIcsUrl,
    setAppleIcsUrl,
    googleCalendars,
    selectedGoogleCalendarId,
    setSelectedGoogleCalendarId,
    calendarConfigMessage,
    googleConnection,
    connectCalendar,
    syncConnection,
    pushToConnection,
    loadGoogleCalendars,
    saveGoogleCalendarSelection,
  };
}

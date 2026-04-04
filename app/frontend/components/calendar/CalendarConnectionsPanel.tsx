import type { CalendarConnection, CalendarOption } from "../../types";

type Props = {
  connectingProvider: "google" | "apple";
  setConnectingProvider: (provider: "google" | "apple") => void;
  externalCalendarId: string;
  setExternalCalendarId: (value: string) => void;
  appleIcsUrl: string;
  setAppleIcsUrl: (value: string) => void;
  googleConnection: CalendarConnection | undefined;
  googleCalendars: CalendarOption[];
  selectedGoogleCalendarId: string;
  setSelectedGoogleCalendarId: (value: string) => void;
  calendarConnections: CalendarConnection[];
  calendarConfigMessage: string | null;
  connectCalendar: () => Promise<void>;
  loadGoogleCalendars: () => Promise<void>;
  saveGoogleCalendarSelection: () => Promise<void>;
  syncConnection: (connectionId: number) => Promise<void>;
  pushToConnection: (connectionId: number) => Promise<void>;
  calendarFeedUrl: string;
};

export function CalendarConnectionsPanel({
  connectingProvider,
  setConnectingProvider,
  externalCalendarId,
  setExternalCalendarId,
  appleIcsUrl,
  setAppleIcsUrl,
  googleConnection,
  googleCalendars,
  selectedGoogleCalendarId,
  setSelectedGoogleCalendarId,
  calendarConnections,
  calendarConfigMessage,
  connectCalendar,
  loadGoogleCalendars,
  saveGoogleCalendarSelection,
  syncConnection,
  pushToConnection,
  calendarFeedUrl,
}: Props) {
  return (
    <details className="calendar-sync-panel">
      <summary className="flex items-center justify-between">
        <span>Calendar Connections</span>
        <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
          Integrations
        </span>
      </summary>
      <div className="sync-body">
        <div className="message-form">
          <label className="field">
            <span className="field-label">Provider</span>
            <select value={connectingProvider} onChange={(event) => setConnectingProvider(event.target.value as "google" | "apple")}>
              <option value="google">Google Calendar</option>
              <option value="apple">Apple Calendar (ICS)</option>
            </select>
          </label>
          {connectingProvider === "apple" && (
            <>
              <label className="field">
                <span className="field-label">External Calendar ID</span>
                <input value={externalCalendarId} onChange={(event) => setExternalCalendarId(event.target.value)} placeholder="primary" />
              </label>
              <label className="field field-full">
                <span className="field-label">Apple ICS URL</span>
                <input value={appleIcsUrl} onChange={(event) => setAppleIcsUrl(event.target.value)} placeholder="https://..." />
              </label>
            </>
          )}
        </div>
        <div className="controls">
          <button className="btn-primary" onClick={connectCalendar}>
            {connectingProvider === "google" ? "Connect Google" : "Save Apple"}
          </button>
          {googleConnection && (
            <>
              <button className="btn-quiet" onClick={loadGoogleCalendars}>
                Load Google Calendars
              </button>
              <select
                className="max-w-[240px]"
                value={selectedGoogleCalendarId}
                onChange={(event) => setSelectedGoogleCalendarId(event.target.value)}
              >
                <option value="">Select Google calendar</option>
                {googleCalendars.map((calendar) => (
                  <option key={calendar.id} value={calendar.id}>
                    {calendar.summary}
                    {calendar.primary ? " (Primary)" : ""}
                  </option>
                ))}
              </select>
              <button className="btn-primary" onClick={saveGoogleCalendarSelection} disabled={!selectedGoogleCalendarId}>
                Save Calendar
              </button>
            </>
          )}
          {calendarConnections.map((connection) => (
            <button className="btn-quiet" key={`sync-${connection.id}`} onClick={() => syncConnection(connection.id)}>
              Sync {connection.provider}
            </button>
          ))}
          {calendarConnections
            .filter((connection) => connection.provider !== "apple")
            .map((connection) => (
              <button className="btn-quiet" key={`push-${connection.id}`} onClick={() => pushToConnection(connection.id)}>
                Push to {connection.provider}
              </button>
            ))}
          <a href={calendarFeedUrl} className="calendar-link">
            Download ICS
          </a>
        </div>
        {calendarConfigMessage && <p className="section-meta">{calendarConfigMessage}</p>}
      </div>
    </details>
  );
}

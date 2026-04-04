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
    <details className="rc-collapsible">
      <summary>Calendar Connections</summary>
      <div className="rc-collapsible-body space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="rc-field">
            <span className="rc-label">Provider</span>
            <select className="w-44" value={connectingProvider} onChange={(e) => setConnectingProvider(e.target.value as "google" | "apple")}>
              <option value="google">Google Calendar</option>
              <option value="apple">Apple Calendar (ICS)</option>
            </select>
          </div>
          {connectingProvider === "apple" && (
            <>
              <div className="rc-field">
                <span className="rc-label">Calendar ID</span>
                <input className="w-36" value={externalCalendarId} onChange={(e) => setExternalCalendarId(e.target.value)} placeholder="primary" />
              </div>
              <div className="rc-field min-w-[200px] flex-1">
                <span className="rc-label">Apple ICS URL</span>
                <input value={appleIcsUrl} onChange={(e) => setAppleIcsUrl(e.target.value)} placeholder="https://..." />
              </div>
            </>
          )}
          <button className="btn-primary btn-sm" onClick={connectCalendar}>
            {connectingProvider === "google" ? "Connect Google" : "Save Apple"}
          </button>
        </div>

        {googleConnection && (
          <div className="flex flex-wrap items-end gap-2">
            <button className="btn-sm" onClick={loadGoogleCalendars}>Load Calendars</button>
            <select className="w-48" value={selectedGoogleCalendarId} onChange={(e) => setSelectedGoogleCalendarId(e.target.value)}>
              <option value="">Select calendar...</option>
              {googleCalendars.map((c) => (
                <option key={c.id} value={c.id}>{c.summary}{c.primary ? " (Primary)" : ""}</option>
              ))}
            </select>
            <button className="btn-primary btn-sm" onClick={saveGoogleCalendarSelection} disabled={!selectedGoogleCalendarId}>
              Save
            </button>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {calendarConnections.map((c) => (
            <button key={`sync-${c.id}`} className="btn-sm" onClick={() => syncConnection(c.id)}>
              Sync {c.provider}
            </button>
          ))}
          {calendarConnections
            .filter((c) => c.provider !== "apple")
            .map((c) => (
              <button key={`push-${c.id}`} className="btn-sm" onClick={() => pushToConnection(c.id)}>
                Push to {c.provider}
              </button>
            ))}
          <a href={calendarFeedUrl} className="inline-flex items-center rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 no-underline hover:bg-gray-50">
            Download ICS
          </a>
        </div>

        {calendarConfigMessage && <p className="text-xs text-gray-500">{calendarConfigMessage}</p>}
      </div>
    </details>
  );
}

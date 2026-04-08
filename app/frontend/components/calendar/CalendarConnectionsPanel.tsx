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
    <div className="space-y-4">
      {/* Add connection */}
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-3">
        <div className="rc-field">
          <span className="rc-label">Provider</span>
          <select value={connectingProvider} onChange={(e) => setConnectingProvider(e.target.value as "google" | "apple")}>
            <option value="google">Google Calendar</option>
            <option value="apple">Apple Calendar (ICS)</option>
          </select>
        </div>
        {connectingProvider === "apple" && (
          <>
            <div className="rc-field">
              <span className="rc-label">Calendar ID</span>
              <input value={externalCalendarId} onChange={(e) => setExternalCalendarId(e.target.value)} placeholder="primary" />
            </div>
            <div className="rc-field">
              <span className="rc-label">Apple ICS URL</span>
              <input value={appleIcsUrl} onChange={(e) => setAppleIcsUrl(e.target.value)} placeholder="https://..." />
            </div>
          </>
        )}
        <button className="btn-primary btn-sm" onClick={connectCalendar}>
          {connectingProvider === "google" ? "Connect Google" : "Save Apple"}
        </button>
      </div>

      {/* Google calendar picker */}
      {googleConnection && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-2">
          <span className="rc-label">Google calendar</span>
          <div className="flex items-center gap-2">
            <button className="btn-sm whitespace-nowrap" onClick={loadGoogleCalendars}>Load Calendars</button>
            <select className="flex-1" value={selectedGoogleCalendarId} onChange={(e) => setSelectedGoogleCalendarId(e.target.value)}>
              <option value="">Select calendar...</option>
              {googleCalendars.map((c) => (
                <option key={c.id} value={c.id}>{c.summary}{c.primary ? " (Primary)" : ""}</option>
              ))}
            </select>
            <button className="btn-primary btn-sm" onClick={saveGoogleCalendarSelection} disabled={!selectedGoogleCalendarId}>
              Save
            </button>
          </div>
        </div>
      )}

      {/* Connected calendars */}
      {calendarConnections.length > 0 && (
        <div>
          <span className="rc-label mb-2 block">Connected</span>
          <div className="space-y-2">
            {calendarConnections.map((c) => (
              <div key={c.id} className="flex items-center justify-between rounded-lg border border-gray-200 bg-white px-3 py-2">
                <span className="text-[13px] font-medium text-gray-700 capitalize">{c.provider}</span>
                <div className="flex gap-1.5">
                  <button className="btn-sm btn-ghost" onClick={() => syncConnection(c.id)}>
                    Sync
                  </button>
                  {c.provider !== "apple" && (
                    <button className="btn-sm btn-ghost" onClick={() => pushToConnection(c.id)}>
                      Push
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ICS download */}
      <a
        href={calendarFeedUrl}
        className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-[12px] font-medium text-gray-600 no-underline transition-colors hover:bg-gray-50 hover:text-gray-900"
      >
        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
        </svg>
        Download ICS feed
      </a>

      {calendarConfigMessage && <p className="mt-2 text-xs text-gray-500">{calendarConfigMessage}</p>}
    </div>
  );
}

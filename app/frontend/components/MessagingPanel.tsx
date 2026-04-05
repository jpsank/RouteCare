import { useState } from "react";
import type { Message, Visit } from "../types";

type SuggestedVisitTime = {
  starts_at: string;
  ends_at: string;
  label: string;
};

type Props = {
  visits: Visit[];
  messages: Message[];
  onSend: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => Promise<Message | undefined>;
  onApprove: (messageId: number) => Promise<Message | undefined>;
  onSelectSuggestion: (messageId: number, suggestionIndex: number) => Promise<Message | undefined>;
};

function titleCase(value: string): string {
  return value
    .replaceAll("_", " ")
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function badgeClass(status: string): string {
  if (status === "confirmed" || status === "sent" || status === "received") return "rc-badge rc-badge-success";
  if (status === "declined" || status === "failed") return "rc-badge rc-badge-danger";
  if (status === "pending_approval") return "rc-badge rc-badge-warning";
  return "rc-badge rc-badge-neutral";
}

function suggestedVisitTimes(message: Message): SuggestedVisitTime[] {
  const suggestions = message.metadata.suggested_visit_times;
  return Array.isArray(suggestions) ? suggestions as SuggestedVisitTime[] : [];
}

export function MessagingPanel({ visits, messages, onSend, onApprove, onSelectSuggestion }: Props) {
  const [visitId, setVisitId] = useState<string>("");
  const [body, setBody] = useState("");
  const [channel, setChannel] = useState<"sms" | "email">("sms");
  const [loading, setLoading] = useState(false);
  const [approvingMessageId, setApprovingMessageId] = useState<number | null>(null);
  const [selectingSuggestionKey, setSelectingSuggestionKey] = useState<string | null>(null);

  async function sendMessage(sendImmediately: boolean) {
    if (!visitId) return;
    setLoading(true);
    try {
      await onSend(Number(visitId), channel, body, sendImmediately);
      setBody("");
    } finally {
      setLoading(false);
    }
  }

  async function approvePendingMessage(messageId: number) {
    setApprovingMessageId(messageId);
    try {
      await onApprove(messageId);
    } finally {
      setApprovingMessageId(null);
    }
  }

  function canApprove(message: Message): boolean {
    return message.direction === "outbound" && (message.status === "draft" || message.status === "pending_approval") && !message.approved_at;
  }

  async function chooseSuggestion(messageId: number, suggestionIndex: number) {
    const key = `${messageId}:${suggestionIndex}`;
    setSelectingSuggestionKey(key);
    try {
      await onSelectSuggestion(messageId, suggestionIndex);
    } finally {
      setSelectingSuggestionKey(null);
    }
  }

  return (
    <div className="rc-card space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="rc-section-title">Messages</h2>
          <p className="rc-section-subtitle">Send appointment proposals and review responses.</p>
        </div>
        {messages.length > 0 && (
          <span className="rc-pill">{messages.length} total</span>
        )}
      </div>

      <details className="rounded-lg border border-gray-200 bg-gray-50" open={messages.length === 0}>
        <summary className="cursor-pointer select-none px-4 py-2.5 text-xs font-semibold text-gray-600">
          Compose new message
        </summary>
        <div className="rc-form-grid border-t border-gray-200 p-4">
          <div className="rc-field">
            <span className="rc-label">Visit</span>
            <select value={visitId} onChange={(e) => setVisitId(e.target.value)}>
              <option value="">Select visit...</option>
              {visits.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.patient_name} - {new Date(v.starts_at).toLocaleString()}
                </option>
              ))}
            </select>
          </div>

          <div className="rc-field">
            <span className="rc-label">Channel</span>
            <div className="flex gap-2">
              {(["sms", "email"] as const).map((ch) => (
                <button
                  key={ch}
                  className={`flex-1 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    channel === ch
                      ? "bg-indigo-100 text-indigo-700 border-indigo-200"
                      : "bg-white text-gray-500 border-gray-200 hover:bg-gray-50"
                  }`}
                  onClick={() => setChannel(ch)}
                >
                  {ch === "sms" ? "SMS" : "Email"}
                </button>
              ))}
            </div>
          </div>

          <div className="rc-field sm:col-span-2">
            <span className="rc-label">Message</span>
            <textarea
              placeholder="Leave blank to use RouteCare template"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={3}
            />
          </div>

          <div className="sm:col-span-2">
            <div className="flex flex-wrap gap-2">
              <button className="btn-secondary" onClick={() => void sendMessage(false)} disabled={loading || !visitId}>
                {loading ? "Saving..." : "Save Draft"}
              </button>
              <button className="btn-primary" onClick={() => void sendMessage(true)} disabled={loading || !visitId}>
                {loading ? "Sending..." : "Send Message"}
              </button>
            </div>
          </div>
        </div>
      </details>

      <div className="space-y-2">
        {messages.length === 0 && (
          <div className="rc-empty">
            <svg className="mx-auto mb-2 h-8 w-8 text-gray-300" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
            </svg>
            No messages yet. Compose one above to get started.
          </div>
        )}
        {messages.map((msg) => {
          const suggestions = suggestedVisitTimes(msg);
          const isInbound = msg.direction === "inbound";
          return (
            <div key={msg.id} className={`rc-msg-item ${isInbound ? "border-l-[3px] border-l-indigo-400" : ""}`}>
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                {msg.patient_name && <span className="font-semibold text-gray-800">{msg.patient_name}</span>}
                <span className="inline-flex items-center gap-1 rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-600">
                  {msg.channel === "sms" ? "SMS" : "Email"}
                </span>
                <span>{isInbound ? "Received" : "Sent"}</span>
                <span className={badgeClass(msg.status)}>{titleCase(msg.status)}</span>
              </div>
              {msg.body && <p className="mt-1.5 text-sm leading-relaxed text-gray-700">{msg.body}</p>}
              {msg.proposed_starts_at && (
                <p className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-700">
                  <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
                  </svg>
                  Selected: {new Date(msg.proposed_starts_at).toLocaleString()}
                </p>
              )}
              {suggestions.length > 0 && !msg.proposed_starts_at && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {suggestions.map((suggestion, index) => {
                    const key = `${msg.id}:${index}`;
                    return (
                      <button
                        key={suggestion.starts_at}
                        className="btn-secondary btn-sm"
                        onClick={() => void chooseSuggestion(msg.id, index)}
                        disabled={selectingSuggestionKey === key}
                      >
                        {selectingSuggestionKey === key ? "Selecting..." : suggestion.label}
                      </button>
                    );
                  })}
                </div>
              )}
              {canApprove(msg) && (
                <div className="mt-2">
                  <button
                    className="btn-primary btn-sm"
                    onClick={() => void approvePendingMessage(msg.id)}
                    disabled={approvingMessageId === msg.id}
                  >
                    {approvingMessageId === msg.id ? "Approving..." : "Approve & Send"}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

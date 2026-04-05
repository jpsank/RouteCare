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
      <div>
        <h2 className="rc-section-title">Messages</h2>
        <p className="rc-section-subtitle">Send appointment proposals and review responses.</p>
      </div>

      <div className="rc-form-grid rounded-lg border border-gray-200 bg-gray-50 p-4">
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
          <select value={channel} onChange={(e) => setChannel(e.target.value as "sms" | "email")}>
            <option value="sms">SMS</option>
            <option value="email">Email</option>
          </select>
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

      <div className="space-y-2">
        {messages.length === 0 && <div className="rc-empty">No messages sent yet.</div>}
        {messages.map((msg) => (
          <div key={msg.id} className="rc-msg-item">
            {(() => {
              const suggestions = suggestedVisitTimes(msg);
              return (
                <>
            <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
              {msg.patient_name && <span className="font-semibold text-gray-800">{msg.patient_name}</span>}
              <span className="font-semibold text-gray-700">{msg.channel.toUpperCase()}</span>
              <span>{titleCase(msg.direction)}</span>
              <span className={badgeClass(msg.status)}>{titleCase(msg.status)}</span>
            </div>
            {msg.body && <p className="mt-1 text-sm text-gray-700">{msg.body}</p>}
            {msg.proposed_starts_at && (
              <p className="mt-2 text-xs text-indigo-700">Selected slot: {new Date(msg.proposed_starts_at).toLocaleString()}</p>
            )}
            {suggestions.length > 0 && !msg.proposed_starts_at && (
              <div className="mt-2 flex flex-wrap gap-2">
                {suggestions.map((suggestion, index) => {
                  const key = `${msg.id}:${index}`;
                  return (
                    <button
                      key={suggestion.starts_at}
                      className="btn-secondary"
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
                  className="btn-primary"
                  onClick={() => void approvePendingMessage(msg.id)}
                  disabled={approvingMessageId === msg.id}
                >
                  {approvingMessageId === msg.id ? "Approving..." : "Approve & Send"}
                </button>
              </div>
            )}
                </>
              );
            })()}
          </div>
        ))}
      </div>
    </div>
  );
}

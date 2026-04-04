import { useState } from "react";
import type { Message, Visit } from "../types";

type Props = {
  visits: Visit[];
  messages: Message[];
  onSend: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => Promise<Message>;
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

export function MessagingPanel({ visits, messages, onSend }: Props) {
  const [visitId, setVisitId] = useState<string>("");
  const [body, setBody] = useState("");
  const [channel, setChannel] = useState<"sms" | "email">("sms");
  const [loading, setLoading] = useState(false);

  async function sendMessage() {
    if (!visitId) return;
    setLoading(true);
    try {
      await onSend(Number(visitId), channel, body, true);
      setBody("");
    } finally {
      setLoading(false);
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
          <button className="btn-primary" onClick={sendMessage} disabled={loading || !visitId}>
            {loading ? "Sending..." : "Send Message"}
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {messages.length === 0 && <div className="rc-empty">No messages sent yet.</div>}
        {messages.slice(0, 8).map((msg) => (
          <div key={msg.id} className="rc-msg-item">
            <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
              <span className="font-semibold text-gray-700">{msg.channel.toUpperCase()}</span>
              <span>{titleCase(msg.direction)}</span>
              <span className={badgeClass(msg.status)}>{titleCase(msg.status)}</span>
            </div>
            {msg.body && <p className="mt-1 text-sm text-gray-700">{msg.body}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}

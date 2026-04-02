import { useState } from "react";
import type { Message, Visit } from "../types";

type Props = {
  visits: Visit[];
  messages: Message[];
  onSend: (visitId: number, channel: "sms" | "email", body: string, sendImmediately: boolean) => Promise<Message>;
};

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
    <section className="card">
      <header className="section-header">
        <div>
          <h2 className="section-title">Patient Communication</h2>
          <p className="section-subtitle">Send appointment proposals and review inbound responses.</p>
        </div>
      </header>

      <div className="message-form">
        <label className="field">
          <span className="field-label">Visit</span>
          <select value={visitId} onChange={(event) => setVisitId(event.target.value)}>
            <option value="">Select visit</option>
            {visits.map((visit) => (
              <option key={visit.id} value={visit.id}>
                {visit.patient_name} - {new Date(visit.starts_at).toLocaleString()}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field-label">Channel</span>
          <select value={channel} onChange={(event) => setChannel(event.target.value as "sms" | "email")}>
            <option value="sms">SMS</option>
            <option value="email">Email</option>
          </select>
        </label>

        <label className="field field-full">
          <span className="field-label">Message</span>
          <textarea
            placeholder="Optional custom message body (leave blank to use RouteCare template)"
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={4}
          />
        </label>

        <button type="button" className="btn btn-primary" onClick={sendMessage} disabled={loading || !visitId}>
          {loading ? "Sending..." : "Send Message"}
        </button>
      </div>

      <ul className="message-list">
        {messages.slice(0, 8).map((message) => (
          <li key={message.id} className="message-item">
            <div className="message-item-meta">
              <strong>{message.channel.toUpperCase()}</strong>
              <span>{message.direction}</span>
              <span className={`badge badge-${message.status}`}>{message.status}</span>
            </div>
            <p className="message-item-body">{message.body}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

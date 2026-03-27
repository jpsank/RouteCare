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
      <div className="section-header">
        <h3>Patient Communication</h3>
      </div>
      <div className="message-form">
        <select value={visitId} onChange={(event) => setVisitId(event.target.value)}>
          <option value="">Select visit</option>
          {visits.map((visit) => (
            <option key={visit.id} value={visit.id}>
              {visit.patient_name} - {new Date(visit.starts_at).toLocaleString()}
            </option>
          ))}
        </select>
        <select value={channel} onChange={(event) => setChannel(event.target.value as "sms" | "email")}>
          <option value="sms">SMS</option>
          <option value="email">Email</option>
        </select>
        <textarea
          placeholder="Optional custom message body (leave blank to use RouteCare template)"
          value={body}
          onChange={(event) => setBody(event.target.value)}
        />
        <button type="button" onClick={sendMessage} disabled={loading || !visitId}>
          {loading ? "Sending..." : "Send"}
        </button>
      </div>

      <ul className="message-list">
        {messages.slice(0, 8).map((message) => (
          <li key={message.id}>
            <div>
              <strong>{message.channel.toUpperCase()}</strong> · {message.direction} · {message.status}
            </div>
            <p>{message.body}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

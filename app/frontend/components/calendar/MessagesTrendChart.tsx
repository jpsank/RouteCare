import { useMemo } from "react";
import { format as formatDate, startOfDay, subDays } from "date-fns";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Message } from "../../types";

type Props = {
  messages: Message[];
  days?: number;
};

type Row = {
  day: string;
  outbound: number;
  inbound: number;
};

export function MessagesTrendChart({ messages, days = 14 }: Props) {
  const rows = useMemo<Row[]>(() => {
    const today = startOfDay(new Date());
    const buckets: Row[] = [];
    const index = new Map<string, Row>();

    for (let i = days - 1; i >= 0; i--) {
      const date = subDays(today, i);
      const key = formatDate(date, "yyyy-MM-dd");
      const row: Row = { day: formatDate(date, "MMM d"), outbound: 0, inbound: 0 };
      buckets.push(row);
      index.set(key, row);
    }

    for (const message of messages) {
      if (!message.created_at) continue;
      const key = formatDate(new Date(message.created_at), "yyyy-MM-dd");
      const row = index.get(key);
      if (!row) continue;
      if (message.direction === "inbound") row.inbound += 1;
      else row.outbound += 1;
    }

    return buckets;
  }, [messages, days]);

  const total = rows.reduce((sum, r) => sum + r.outbound + r.inbound, 0);
  if (total === 0) return null;

  return (
    <div className="border-b border-gray-100 px-4 py-3">
      <div className="mb-1 flex items-baseline justify-between">
        <h5 className="text-[11px] font-semibold text-gray-500">Last {days} days</h5>
        <span className="text-[10px] text-gray-400">{total} message{total === 1 ? "" : "s"}</span>
      </div>
      <div className="h-24">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" vertical={false} />
            <XAxis dataKey="day" tick={{ fontSize: 9, fill: "#9ca3af" }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 9, fill: "#9ca3af" }} axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e5e7eb" }} />
            <Bar dataKey="outbound" stackId="dir" fill="#6366f1" name="Sent" />
            <Bar dataKey="inbound" stackId="dir" fill="#10b981" name="Received" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

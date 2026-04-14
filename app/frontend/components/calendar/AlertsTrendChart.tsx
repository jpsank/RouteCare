import { useMemo } from "react";
import { format as formatDate, startOfDay, subDays } from "date-fns";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Alert } from "../../types";

type Props = {
  alerts: Alert[];
  days?: number;
};

type Row = {
  day: string;
  high: number;
  medium: number;
  low: number;
};

export function AlertsTrendChart({ alerts, days = 14 }: Props) {
  const rows = useMemo<Row[]>(() => {
    const today = startOfDay(new Date());
    const buckets: Row[] = [];
    const index = new Map<string, Row>();

    for (let i = days - 1; i >= 0; i--) {
      const date = subDays(today, i);
      const key = formatDate(date, "yyyy-MM-dd");
      const row: Row = { day: formatDate(date, "MMM d"), high: 0, medium: 0, low: 0 };
      buckets.push(row);
      index.set(key, row);
    }

    for (const alert of alerts) {
      if (!alert.created_at) continue;
      const key = formatDate(new Date(alert.created_at), "yyyy-MM-dd");
      const row = index.get(key);
      if (!row) continue;
      row[alert.severity] += 1;
    }

    return buckets;
  }, [alerts, days]);

  const total = rows.reduce((sum, r) => sum + r.high + r.medium + r.low, 0);
  if (total === 0) return null;

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <h5 className="text-[11px] font-semibold text-gray-500">Last {days} days</h5>
        <span className="text-[10px] text-gray-400">{total} alert{total === 1 ? "" : "s"}</span>
      </div>
      <div className="h-28">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" vertical={false} />
            <XAxis dataKey="day" tick={{ fontSize: 9, fill: "#9ca3af" }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
            <YAxis tick={{ fontSize: 9, fill: "#9ca3af" }} axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e5e7eb" }} />
            <Bar dataKey="low" stackId="sev" fill="#d1d5db" />
            <Bar dataKey="medium" stackId="sev" fill="#f59e0b" />
            <Bar dataKey="high" stackId="sev" fill="#ef4444" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

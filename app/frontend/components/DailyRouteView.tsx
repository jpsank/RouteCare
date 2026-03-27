import { useMemo } from "react";
import type { Visit } from "../types";

type Props = {
  visits: Visit[];
  date?: string;
};

function fmt(date: string): string {
  return new Date(date).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function DailyRouteView({ visits, date }: Props) {
  const grouped = useMemo(() => {
    const byDate: Record<string, Visit[]> = {};
    visits.forEach((visit) => {
      const key = new Date(visit.starts_at).toISOString().slice(0, 10);
      byDate[key] ||= [];
      byDate[key].push(visit);
    });
    return Object.entries(byDate)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, dayVisits]) => ({
        date,
        visits: dayVisits.sort((a, b) => a.position_in_day - b.position_in_day),
      }));
  }, [visits]);

  const filteredGrouped = date ? grouped.filter((entry) => entry.date === date) : grouped;

  return (
    <section className="panel">
      <h2>Daily Route View</h2>
      {filteredGrouped.length === 0 && <p>No visits scheduled.</p>}
      {filteredGrouped.map((day) => (
        <div key={day.date} style={{ marginBottom: "1rem" }}>
          <h3>{new Date(day.date).toDateString()}</h3>
          <ol>
            {day.visits.map((visit, idx) => (
              <li key={visit.id} style={{ marginBottom: "0.5rem" }}>
                <strong>
                  {idx + 1}. {visit.patient_name}
                </strong>{" "}
                ({fmt(visit.starts_at)} - {fmt(visit.ends_at)}) - Drive from previous: {visit.drive_from_previous_minutes}m
              </li>
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}

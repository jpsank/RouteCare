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
    <section className="card">
      <div className="section-head">
        <h2>Daily Route</h2>
        <span className="section-meta">Optimized stop order for field use</span>
      </div>
      {filteredGrouped.length === 0 && <p className="empty-state">No visits scheduled for this date.</p>}
      {filteredGrouped.map((day) => (
        <div key={day.date} className="route-day">
          <h3>{new Date(day.date).toDateString()}</h3>
          <ol className="route-list">
            {day.visits.map((visit, idx) => (
              <li key={visit.id} className="route-stop">
                <span className="route-index">{idx + 1}</span>
                <div className="route-stop-body">
                  <div className="route-stop-title">{visit.patient_name}</div>
                  <div className="route-stop-meta">
                    {fmt(visit.starts_at)} - {fmt(visit.ends_at)}
                  </div>
                  <div className="route-stop-detail">
                    Drive from previous: {visit.drive_from_previous_minutes}m
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      ))}
    </section>
  );
}

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
    <section className="routecare-panel">
      <div className="panel-header">
        <div>
          <h2 className="panel-title">Daily Route</h2>
          <p className="panel-subtitle">Optimized stop order for field use throughout the day.</p>
        </div>
      </div>
      {filteredGrouped.length === 0 && <p className="empty-state">No visits scheduled for this date.</p>}
      {filteredGrouped.map((day) => (
        <article key={day.date} className="route-day-card">
          <h3 className="route-day-title">{new Date(day.date).toDateString()}</h3>
          <ol className="route-stop-list">
            {day.visits.map((visit, idx) => (
              <li key={visit.id} className="route-stop-card">
                <span className="route-index">{idx + 1}</span>
                <div className="route-stop-content">
                  <p className="route-stop-title">{visit.patient_name}</p>
                  <p className="route-stop-time">
                    {fmt(visit.starts_at)} - {fmt(visit.ends_at)}
                  </p>
                  <p className="route-stop-detail">
                    Drive from previous: {visit.drive_from_previous_minutes}m
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </article>
      ))}
    </section>
  );
}

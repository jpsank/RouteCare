import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { format as formatDate } from "date-fns";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Visit, WeeklySchedule } from "../../types";
import { useClickOutside } from "../hooks/useClickOutside";
import { asDateKey } from "./utils";

const EMPTY_RETURN_HOME: Record<string, number> = {};

// Start off-screen but already `position: fixed` so the popover is never in
// normal flow — prevents the header layout thrashing on first open.
const OFFSCREEN_STYLE: React.CSSProperties = { position: "fixed", left: -9999, top: 0 };

type Props = {
  schedule: WeeklySchedule | null;
};

type DayRow = {
  day: string;
  driveMinutes: number;
  visitCount: number;
};

function aggregateByDay(visits: Visit[], returnHomeByDay: Record<string, number>): DayRow[] {
  const byKey = new Map<string, { date: Date; driveMinutes: number; visitCount: number }>();
  for (const v of visits) {
    if (!v.starts_at) continue;
    const key = asDateKey(v.starts_at);
    const existing = byKey.get(key);
    if (existing) {
      existing.driveMinutes += v.drive_from_previous_minutes ?? 0;
      existing.visitCount += 1;
    } else {
      byKey.set(key, {
        date: new Date(v.starts_at),
        driveMinutes: v.drive_from_previous_minutes ?? 0,
        visitCount: 1,
      });
    }
  }
  for (const [key, minutes] of Object.entries(returnHomeByDay)) {
    const existing = byKey.get(key);
    if (existing) {
      existing.driveMinutes += minutes;
    } else {
      byKey.set(key, { date: new Date(`${key}T00:00:00`), driveMinutes: minutes, visitCount: 0 });
    }
  }
  return [...byKey.values()]
    .sort((a, b) => a.date.getTime() - b.date.getTime())
    .map((row) => ({
      day: formatDate(row.date, "EEE"),
      driveMinutes: Math.round(row.driveMinutes),
      visitCount: row.visitCount,
    }));
}

export function WeekStatsPopover({ schedule }: Props) {
  const [open, setOpen] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>(OFFSCREEN_STYLE);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const { rows, totalDrive } = useMemo(() => {
    const returnHomeByDay = schedule?.optimization_summary?.return_home_by_day ?? EMPTY_RETURN_HOME;
    const aggregated = aggregateByDay(schedule?.visits ?? [], returnHomeByDay);
    return {
      rows: aggregated,
      totalDrive: aggregated.reduce((sum, r) => sum + r.driveMinutes, 0),
    };
  }, [schedule]);

  const totalVisits = useMemo(
    () => rows.reduce((sum, r) => sum + r.visitCount, 0),
    [rows],
  );

  useClickOutside(containerRef, () => setOpen(false), open);

  useLayoutEffect(() => {
    if (!open) {
      setPopoverStyle(OFFSCREEN_STYLE);
      return;
    }
    if (!buttonRef.current) return;

    let frame = 0;
    const reposition = () => {
      if (!buttonRef.current) return;
      const rect = buttonRef.current.getBoundingClientRect();
      const margin = 12;
      const width = Math.min(420, window.innerWidth - margin * 2);
      const idealLeft = rect.right - width;
      const left = Math.max(margin, Math.min(idealLeft, window.innerWidth - width - margin));
      setPopoverStyle({ position: "fixed", left, top: rect.bottom + 4, width });
    };
    const scheduleReposition = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        reposition();
      });
    };

    reposition();
    window.addEventListener("resize", scheduleReposition);
    window.addEventListener("scroll", scheduleReposition);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("resize", scheduleReposition);
      window.removeEventListener("scroll", scheduleReposition);
    };
  }, [open]);

  if (!schedule || rows.length === 0) return null;

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        className="relative flex items-center gap-1.5 rounded-lg border-0 bg-transparent px-2 py-1.5 text-xs font-medium text-gray-500 shadow-none hover:bg-gray-100"
        onClick={() => setOpen(!open)}
      >
        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
        </svg>
        <span className="hidden sm:inline">Stats</span>
      </button>

      {open && (
        <div
          className="z-50 rounded-2xl bg-white p-4 shadow-2xl ring-1 ring-black/5 animate-[scaleIn_0.12s_ease-out]"
          style={popoverStyle}
        >
          <div className="mb-3 flex items-baseline justify-between">
            <h4 className="text-xs font-semibold text-gray-500">Week stats</h4>
            <span className="text-[10px] text-gray-400">Drive time per day</span>
          </div>

          <div className="mb-3 grid grid-cols-2 gap-2 text-center">
            <div className="rounded-lg bg-gray-50 p-2">
              <div className="text-[10px] uppercase tracking-wide text-gray-400">Total drive</div>
              <div className="text-sm font-semibold text-gray-900">{totalDrive} min</div>
            </div>
            <div className="rounded-lg bg-gray-50 p-2">
              <div className="text-[10px] uppercase tracking-wide text-gray-400">Visits</div>
              <div className="text-sm font-semibold text-gray-900">{totalVisits}</div>
            </div>
          </div>

          <div className="h-40">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 10, fill: "#9ca3af" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: "#9ca3af" }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ fontSize: 11, borderRadius: 8, border: "1px solid #e5e7eb" }}
                  formatter={(value, name) => [`${value} min`, name === "driveMinutes" ? "Drive" : String(name)]}
                />
                <Bar dataKey="driveMinutes" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

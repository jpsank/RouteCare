import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WeekStatsPopover } from "../app/frontend/components/calendar/WeekStatsPopover";
import type { WeeklySchedule } from "../app/frontend/types";

function buildSchedule(overrides: Partial<WeeklySchedule> = {}): WeeklySchedule {
  return {
    id: 1,
    week_start_on: "2026-04-13",
    status: "optimized",
    total_drive_minutes: 120,
    baseline_drive_minutes: 200,
    drive_minutes_saved: 80,
    optimization_summary: {},
    visits: [
      {
        id: 1,
        patient_id: 1,
        patient_name: "Test Patient",
        starts_at: "2026-04-13T14:00:00.000Z",
        ends_at: "2026-04-13T15:00:00.000Z",
        duration_minutes: 60,
        status: "confirmed",
        position_in_day: 0,
        drive_from_previous_minutes: 15,
        clinician_override: false,
        soft_constraint_override: false,
        source: "optimizer",
      },
    ],
    ...overrides,
  } as WeeklySchedule;
}

describe("WeekStatsPopover", () => {
  it("renders the Stats trigger when a schedule has visits", () => {
    render(<WeekStatsPopover schedule={buildSchedule()} />);
    expect(screen.getByRole("button", { name: /stats/i })).toBeInTheDocument();
  });

  it("renders nothing when schedule is null", () => {
    const { container } = render(<WeekStatsPopover schedule={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there are no visits", () => {
    const { container } = render(
      <WeekStatsPopover schedule={buildSchedule({ visits: [] })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

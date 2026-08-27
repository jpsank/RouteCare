import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PatientEditorPanel } from "../app/frontend/components/calendar/PatientEditorPanel";
import type { Patient } from "../app/frontend/types";
import type { PatientSavePayload } from "../app/frontend/components/calendar/utils";

function buildPatient(overrides: Partial<Patient> = {}): Patient {
  return {
    id: 1,
    first_name: "Jane",
    last_name: "Doe",
    full_name: "Jane Doe",
    phone: "555-0100",
    email: "",
    address_line1: "100 Main St",
    address_line2: "",
    city: "Boston",
    state: "MA",
    postal_code: "02110",
    address: "100 Main St, Boston, MA, 02110",
    required_visits_per_week: 1,
    visit_duration_minutes: 45,
    active: true,
    notes: "",
    latitude: 42.36,
    longitude: -71.06,
    min_days_between_visits: 1,
    max_days_between_visits: 7,
    priority: 0,
    availability_windows: [],
    ...overrides,
  };
}

describe("PatientEditorPanel availability windows", () => {
  it("shows an empty state when the patient has no windows", () => {
    render(<PatientEditorPanel patient={buildPatient()} onSave={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(/no windows set/i)).toBeInTheDocument();
  });

  it("renders existing windows with their available/unavailable state", () => {
    const patient = buildPatient({
      availability_windows: [
        { id: 1, day_of_week: 1, start_minute: 540, end_minute: 720, available: true },
        { id: 2, day_of_week: 2, start_minute: 600, end_minute: 660, available: false },
      ],
    });
    render(<PatientEditorPanel patient={patient} onSave={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Available" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unavailable" })).toBeInTheDocument();
  });

  it("adds a new available window and includes it in the save payload", async () => {
    const onSave = vi.fn().mockResolvedValue(true);
    const patient = buildPatient();
    render(<PatientEditorPanel patient={patient} onSave={onSave} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /add window/i }));
    expect(screen.getByRole("button", { name: "Available" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /save patient/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const payload = onSave.mock.calls[0][1] as PatientSavePayload;
    expect(payload.availability_windows).toHaveLength(1);
    expect(payload.availability_windows?.[0]).toMatchObject({
      day_of_week: 1,
      start_minute: 9 * 60,
      end_minute: 17 * 60,
      available: true,
    });
  });

  it("toggles a window between available and unavailable", () => {
    const patient = buildPatient({
      availability_windows: [
        { id: 1, day_of_week: 1, start_minute: 540, end_minute: 720, available: true },
      ],
    });
    render(<PatientEditorPanel patient={patient} onSave={vi.fn()} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Available" }));
    expect(screen.getByRole("button", { name: "Unavailable" })).toBeInTheDocument();
  });

  it("removes a window", () => {
    const patient = buildPatient({
      availability_windows: [
        { id: 1, day_of_week: 1, start_minute: 540, end_minute: 720, available: true },
      ],
    });
    render(<PatientEditorPanel patient={patient} onSave={vi.fn()} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /remove/i }));
    expect(screen.getByText(/no windows set/i)).toBeInTheDocument();
  });

  it("disables save when a window's end time is not after its start time", () => {
    const patient = buildPatient({
      availability_windows: [
        { id: 1, day_of_week: 1, start_minute: 720, end_minute: 540, available: true },
      ],
    });
    render(<PatientEditorPanel patient={patient} onSave={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByText(/end time must be after start time/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save patient/i })).toBeDisabled();
  });
});

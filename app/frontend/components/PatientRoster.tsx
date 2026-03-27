import type { Patient } from "../types";

type Props = {
  patients: Patient[];
  onCreate: (patient: Partial<Patient>) => Promise<void>;
  onRefresh: () => Promise<void>;
};

export function PatientRoster({ patients, onCreate, onRefresh }: Props) {
  async function createQuickPatient() {
    await onCreate({
      full_name: "New Patient",
      phone: "555-0100",
      address_line1: "123 Main St",
      city: "Springfield",
      state: "NY",
      postal_code: "10001",
      required_visits_per_week: 3,
      visit_duration_minutes: 60
    });
    await onRefresh();
  }

  return (
    <section className="card">
      <h2>Patient Roster</h2>
      <div className="controls">
        <button onClick={createQuickPatient}>Add Quick Patient</button>
        <button onClick={onRefresh}>Refresh Roster</button>
      </div>
      {patients.length === 0 ? <p>No active patients yet.</p> : null}
      <div className="patient-grid">
        {patients.map((patient) => (
          <article key={patient.id} className="visit-card">
            <h3>{patient.full_name}</h3>
            <p>{patient.address}</p>
            <p>{patient.phone}</p>
            <p>
              {patient.required_visits_per_week}x/week · {patient.visit_duration_minutes} min
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}

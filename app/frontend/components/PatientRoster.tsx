import type { Patient } from "../types";

type Props = {
  patients: ReadonlyArray<Patient>;
  onCreate: (patient: Partial<Patient>) => Promise<void>;
  onRefresh: () => Promise<void>;
};

export function PatientRoster({ patients, onCreate, onRefresh }: Props) {
  const [creating, setCreating] = useState(false);

  async function createQuickPatient() {
    setCreating(true);
    try {
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
    } finally {
      setCreating(false);
    }
  }

  return (
    <section className="routecare-card">
      <div className="section-header">
        <div>
          <h2 className="section-title">Patient Roster</h2>
          <p className="section-subtitle">Maintain patient profiles and visit frequency preferences.</p>
        </div>
      </div>
      <div className="routecare-controls">
        <button className="routecare-btn routecare-btn--primary" onClick={createQuickPatient} disabled={creating}>
          {creating ? "Adding..." : "Add Quick Patient"}
        </button>
        <button className="routecare-btn" onClick={onRefresh} disabled={creating}>Refresh Roster</button>
      </div>
      {patients.length === 0 ? <p className="muted">No active patients yet.</p> : null}
      <div className="patient-grid">
        {patients.map((patient) => (
          <article key={patient.id} className="patient-card">
            <div className="patient-card-top">
              <h3>{patient.full_name}</h3>
              <span className={`badge ${patient.active ? "badge-confirmed" : "badge-declined"}`}>
                {patient.active ? "Active" : "Inactive"}
              </span>
            </div>
            <p className="muted">{patient.address}</p>
            <p className="muted">{patient.phone}</p>
            <div className="patient-meta">
              <span>{patient.required_visits_per_week}x/week</span>
              <span>{patient.visit_duration_minutes} min visits</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

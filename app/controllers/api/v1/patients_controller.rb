class Api::V1::PatientsController < Api::V1::BaseController
  def index
    render json: {
      patients: current_clinician_profile.patients.active.includes(:patient_availability_windows).map { |patient| PatientSerializer.as_json(patient) }
    }
  end

  def show
    patient = current_clinician_profile.patients.includes(:patient_availability_windows).find(params[:id])
    render json: { patient: PatientSerializer.as_json(patient) }
  end

  def create
    patient = current_clinician_profile.patients.new(patient_params)
    replace_availability_windows(patient)

    if patient.save
      render json: { patient: PatientSerializer.as_json(patient.reload) }, status: :created
    else
      render_unprocessable(patient.errors.full_messages)
    end
  end

  def update
    patient = current_clinician_profile.patients.find(params[:id])
    patient.assign_attributes(patient_params)
    replace_availability_windows(patient)

    if patient.save
      render json: { patient: PatientSerializer.as_json(patient.reload) }
    else
      render_unprocessable(patient.errors.full_messages)
    end
  end

  def deactivate
    patient = current_clinician_profile.patients.find(params[:id])
    patient.update!(active: false)
    render json: { patient: PatientSerializer.as_json(patient) }
  end

  def seed_demo
    profile = current_clinician_profile
    patient_ids = profile.patients.pluck(:id)

    ActiveRecord::Base.transaction do
      Visit.where(patient_id: patient_ids).delete_all
      PatientMessage.where(patient_id: patient_ids).delete_all
      PatientAvailabilityWindow.where(patient_id: patient_ids).delete_all
      Patient.where(id: patient_ids).delete_all

      sample_patients.each do |attrs|
        profile.patients.create!(attrs)
      end
    end

    render json: {
      patients: profile.patients.active.includes(:patient_availability_windows).map { |patient| PatientSerializer.as_json(patient) }
    }
  end

  private

  def patient_params
    params.require(:patient).permit(
      :full_name, :phone, :email, :address_line1, :address_line2, :city, :state, :postal_code,
      :required_visits_per_week, :visit_duration_minutes, :notes, :latitude, :longitude
    )
  end

  def availability_windows_params
    params.fetch(:availability_windows, []).map do |window|
      ActionController::Parameters.new(window).permit(:day_of_week, :start_minute, :end_minute)
    end
  end

  def replace_availability_windows(patient)
    return if availability_windows_params.empty?

    patient.patient_availability_windows.destroy_all
    availability_windows_params.each do |window|
      patient.patient_availability_windows.build(window)
    end
  end

  def sample_patients
    [
      {
        full_name: "Maria Thompson",
        phone: "555-302-1944",
        email: "maria.thompson@example.com",
        address_line1: "2515 W Mount Comfort Rd",
        city: "Fayetteville",
        state: "AR",
        postal_code: "72704",
        required_visits_per_week: 2,
        visit_duration_minutes: 60,
        notes: "Post-op knee rehab; prefers morning visits.",
        latitude: 36.092241,
        longitude: -94.194121
      },
      {
        full_name: "James O'Neil",
        phone: "555-111-7820",
        email: "j.oneil@example.com",
        address_line1: "4207 SW I St",
        city: "Bentonville",
        state: "AR",
        postal_code: "72713",
        required_visits_per_week: 3,
        visit_duration_minutes: 45,
        notes: "Stroke follow-up; caregiver available after 1pm.",
        latitude: 36.318184,
        longitude: -94.219807
      },
      {
        full_name: "Linda Nguyen",
        phone: "555-974-3321",
        email: "linda.nguyen@example.com",
        address_line1: "2710 W Olive St",
        city: "Rogers",
        state: "AR",
        postal_code: "72756",
        required_visits_per_week: 2,
        visit_duration_minutes: 30,
        notes: "COPD management; avoid late afternoon due to fatigue.",
        latitude: 36.332451,
        longitude: -94.158984
      },
      {
        full_name: "Robert Ellis",
        phone: "555-606-9101",
        email: "rellis@example.com",
        address_line1: "1700 W Emma Ave",
        city: "Springdale",
        state: "AR",
        postal_code: "72762",
        required_visits_per_week: 1,
        visit_duration_minutes: 60,
        notes: "Diabetes education; spouse attends visits.",
        latitude: 36.181839,
        longitude: -94.148858
      },
      {
        full_name: "Patricia Gomez",
        phone: "555-288-4456",
        email: "patricia.gomez@example.com",
        address_line1: "1107 S Bloomington St",
        city: "Lowell",
        state: "AR",
        postal_code: "72745",
        required_visits_per_week: 2,
        visit_duration_minutes: 50,
        notes: "CHF monitoring; prefers Tue/Thu mornings.",
        latitude: 36.255173,
        longitude: -94.130785
      }
    ]
  end
end

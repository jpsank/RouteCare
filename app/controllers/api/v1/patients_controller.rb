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
end

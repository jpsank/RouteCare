class Api::V1::MessagesController < Api::V1::BaseController
  before_action :set_visit, only: %i[create]

  def index
    messages = current_user.patient_messages.includes(:patient, :visit).order(created_at: :desc).limit(200)
    render json: { messages: messages.map { |message| message_payload(message) } }
  end

  def create
    message = current_user.patient_messages.create!(
      visit: @visit,
      patient: @visit.patient,
      direction: :outbound,
      channel: message_params[:channel],
      status: :draft,
      body: message_params[:body].presence || Messaging::PatientMessageBuilder.new(visit: @visit, channel: message_params[:channel]).confirmation_text,
      proposed_starts_at: @visit.starts_at,
      proposed_ends_at: @visit.ends_at,
      requires_approval: !current_user.clinician_profile&.auto_send_enabled?
    )

    Messaging::OutboundDispatcher.new(message).call if ActiveModel::Type::Boolean.new.cast(message_params[:send_immediately])

    render json: { message: message_payload(message) }, status: :created
  end

  def create_inbound
    patient = current_user.clinician_profile.patients.find(inbound_params[:patient_id])
    parsed = Messaging::ReplyParser.parse(inbound_params[:body])
    visit = find_visit_for_inbound(patient, inbound_params[:visit_id])

    message = current_user.patient_messages.create!(
      visit:,
      patient:,
      direction: :inbound,
      channel: inbound_params[:channel],
      status: :received,
      body: inbound_params[:body],
      metadata: { parsed_intent: parsed.intent }
    )

    apply_reply_intent!(visit, parsed.intent) if visit

    render json: { message: message_payload(message) }, status: :created
  end

  private

  def message_params
    params.require(:message).permit(:visit_id, :channel, :body, :send_immediately)
  end

  def inbound_params
    params.require(:message).permit(:visit_id, :patient_id, :channel, :body)
  end

  def message_payload(message)
    {
      id: message.id,
      visit_id: message.visit_id,
      patient_id: message.patient_id,
      direction: message.direction,
      channel: message.channel,
      status: message.status,
      body: message.body,
      requires_approval: message.requires_approval,
      approved_at: message.approved_at,
      proposed_starts_at: message.proposed_starts_at,
      proposed_ends_at: message.proposed_ends_at,
      created_at: message.created_at
    }
  end

  def set_visit
    visit_id = message_params[:visit_id]
    @visit = Visit.joins(:weekly_schedule).find_by(id: visit_id, weekly_schedules: { user_id: current_user.id })
    return if @visit

    render_not_found("visit")
    nil
  end

  def find_visit_for_inbound(patient, visit_id)
    return patient.visits.order(starts_at: :desc).first if visit_id.blank?

    patient.visits.find_by(id: visit_id)
  end

  def apply_reply_intent!(visit, intent)
    return if visit.blank?

    case intent
    when :confirm
      visit.update!(status: :confirmed)
    when :decline
      visit.update!(status: :declined)
    when :reschedule
      visit.update!(status: :pending_patient_confirmation)
    end
  end
end

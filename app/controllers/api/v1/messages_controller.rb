class Api::V1::MessagesController < Api::V1::BaseController
  before_action :set_visit, only: %i[create]
  before_action :set_message, only: %i[approve select_suggestion]

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
      body: message_params[:body].presence || Messaging::PatientMessageBuilder.new(visit: @visit, channel: message_params[:channel]).call,
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

    unless visit
      message = current_user.patient_messages.create!(
        visit: nil,
        patient: patient,
        direction: :inbound,
        channel: inbound_params[:channel],
        status: :received,
        body: inbound_params[:body],
        metadata: {
          parsed_intent: parsed.intent.to_s,
          proposed_windows: parsed.proposed_windows
        }
      )

      return render json: { message: message_payload(message) }, status: :created
    end

    result = Messaging::InboundReplyProcessor.new(
      user: current_user,
      visit: visit,
      channel: inbound_params[:channel],
      body: inbound_params[:body]
    ).call

    render json: {
      message: message_payload(result.message),
      follow_up_message: result.follow_up_message.present? ? message_payload(result.follow_up_message) : nil
    }, status: :created
  end

  def approve
    return render_error("Only outbound messages can be approved", :unprocessable_entity) unless @message.direction_outbound?

    unless @message.status_draft? || @message.status_pending_approval?
      return render_error("Message is not awaiting approval", :unprocessable_entity)
    end

    @message.update!(approved_at: Time.current)
    Messaging::OutboundDispatcher.new(@message).call

    render json: { message: message_payload(@message.reload) }
  end

  def select_suggestion
    return render_error("Only outbound messages can use suggestions", :unprocessable_entity) unless @message.direction_outbound?

    suggestions = Array(@message.metadata["suggested_visit_times"])
    suggestion = suggestions[params.require(:suggestion_index).to_i]
    return render_error("Suggested time not found", :unprocessable_entity) if suggestion.blank?

    starts_at = Time.zone.parse(suggestion.fetch("starts_at"))
    ends_at = Time.zone.parse(suggestion.fetch("ends_at"))

    @message.update!(
      proposed_starts_at: starts_at,
      proposed_ends_at: ends_at,
      body: Messaging::PatientMessageBuilder.new(
        visit: @message.visit,
        channel: @message.channel,
        kind: :reschedule_proposal,
        proposed_starts_at: starts_at
      ).call
    )

    render json: { message: message_payload(@message.reload) }
  rescue KeyError, ArgumentError
    render_error("Suggested time not found", :unprocessable_entity)
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
      metadata: message.metadata,
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

  def set_message
    @message = current_user.patient_messages.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found("message")
  end

  def find_visit_for_inbound(patient, visit_id)
    return patient.visits.order(starts_at: :desc).first if visit_id.blank?

    patient.visits.find_by(id: visit_id)
  end
end

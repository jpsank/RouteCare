class Api::V1::AlertsController < Api::V1::BaseController
  def index
    alerts = current_user.alerts.order(due_at: :asc, created_at: :desc).limit(100)
    render json: { alerts: alerts.map { |alert| AlertSerializer.as_json(alert) } }
  end

  def update
    alert = current_user.alerts.find(params[:id])

    if alert.update(alert_params)
      render json: { alert: AlertSerializer.as_json(alert) }
    else
      render_unprocessable(alert.errors.full_messages)
    end
  end

  def execute_action
    alert = current_user.alerts.find(params[:id])
    visit_id = alert.metadata&.dig("visit_id")
    visit = visit_id ? Visit.joins(:weekly_schedule).find_by(id: visit_id, weekly_schedules: { user_id: current_user.id }) : nil

    result = case alert.category
    when "unconfirmed_visit"
      return render_error("Visit not found for this alert", :unprocessable_entity) unless visit

      channel = visit.patient.preferred_message_channel
      message = current_user.patient_messages.create!(
        visit: visit, patient: visit.patient, direction: :outbound,
        channel: channel, status: :draft,
        body: Messaging::PatientMessageBuilder.new(visit: visit, channel: channel, kind: :reminder).call,
        proposed_starts_at: visit.starts_at, proposed_ends_at: visit.ends_at,
        requires_approval: !current_user.clinician_profile&.auto_send_enabled?
      )
      Messaging::OutboundDispatcher.new(message).call
      alert.update!(status: :acknowledged)
      { alert: AlertSerializer.as_json(alert.reload), message: message_payload(message) }
    else
      return render_error("No action available for this alert type", :unprocessable_entity)
    end

    render json: result
  end

  private

  def message_payload(message)
    {
      id: message.id, visit_id: message.visit_id, patient_id: message.patient_id,
      patient_name: message.patient&.full_name, direction: message.direction,
      channel: message.channel, status: message.status, body: message.body,
      requires_approval: message.requires_approval, approved_at: message.approved_at,
      proposed_starts_at: message.proposed_starts_at, proposed_ends_at: message.proposed_ends_at,
      metadata: message.metadata, created_at: message.created_at
    }
  end

  def alert_params
    params.fetch(:alert, {}).permit(:status, :read_at)
  end
end

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

  private

  def alert_params
    params.fetch(:alert, {}).permit(:status, :read_at)
  end
end

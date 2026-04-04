class Api::V1::CalendarConnectionsController < Api::V1::BaseController
  def index
    connections = current_user.calendar_connections.order(:provider)
    render json: { calendar_connections: connections.map { |connection| serialize_connection(connection) } }
  end

  def create
    attrs = calendar_connection_params
    if attrs[:provider] == "google"
      return render_error("Use /auth/google/start for secure Google OAuth connection.", :unprocessable_entity)
    end

    if attrs[:provider] == "apple" && attrs.dig(:metadata, :ics_url).blank?
      return render_error("Apple calendar connection requires metadata.ics_url", :unprocessable_entity)
    end

    connection = current_user.calendar_connections.find_or_initialize_by(provider: attrs[:provider])
    connection.assign_attributes(attrs)
    connection.status = "active"
    connection.save!

    render json: { calendar_connection: serialize_connection(connection) }, status: :created
  rescue ActiveRecord::RecordInvalid => e
    render_unprocessable(e.record.errors.full_messages)
  end

  def destroy
    connection = current_user.calendar_connections.find(params[:id])
    connection.update!(status: "disconnected")
    head :no_content
  end

  def sync
    connection = current_user.calendar_connections.find(params[:id])
    range_start, range_end = parse_range
    client = Integrations::Calendar::ClientFactory.build(connection)
    events = client.blocked_events(connection: connection, range_start: range_start, range_end: range_end)

    imported = 0
    events.each do |event|
      block = current_user.calendar_blocks.find_or_initialize_by(
        source: "external_calendar",
        external_event_id: "#{connection.provider}:#{event[:external_event_id]}"
      )
      block.title = event[:title]
      block.starts_at = event[:starts_at]
      block.ends_at = event[:ends_at]
      block.metadata = (block.metadata || {}).merge(provider: connection.provider)
      imported += 1 if block.new_record?
      block.save!
    end

    render json: { imported_events: imported, total_events: events.size }
  rescue ActiveRecord::RecordInvalid => e
    render_unprocessable(e.record.errors.full_messages)
  rescue ArgumentError => e
    render_error(e.message, :unprocessable_entity)
  end

  def available_calendars
    connection = current_user.calendar_connections.find(params[:id])
    if connection.provider != "google"
      return render_error("Calendar listing is currently supported for Google only.", :unprocessable_entity)
    end

    calendars = Integrations::Calendar::GoogleCalendarClient.new.list_calendars(connection: connection)
    render json: { calendars: calendars }
  rescue ArgumentError => e
    render_error(e.message, :unprocessable_entity)
  end

  def select_calendar
    connection = current_user.calendar_connections.find(params[:id])
    if connection.provider != "google"
      return render_error("Calendar selection is currently supported for Google only.", :unprocessable_entity)
    end

    external_calendar_id = params.require(:external_calendar_id)
    connection.update!(external_calendar_id: external_calendar_id)
    render json: { calendar_connection: serialize_connection(connection) }
  rescue ActionController::ParameterMissing
    render_error("external_calendar_id is required", :unprocessable_entity)
  rescue ActiveRecord::RecordInvalid => e
    render_unprocessable(e.record.errors.full_messages)
  end

  def push_visits
    connection = current_user.calendar_connections.find(params[:id])
    week_start_on = parsed_week_start(params[:week_start_on])
    schedule = current_user.weekly_schedules.includes(visits: :patient).find_by!(week_start_on: week_start_on)
    client = Integrations::Calendar::ClientFactory.build(connection)

    pushed = 0
    schedule.visits.each do |visit|
      external_event_id = client.upsert_visit_event(connection: connection, visit: visit)
      visit.update!(external_calendar_event_id: external_event_id)
      pushed += 1
    end

    render json: { pushed_visits: pushed }
  rescue ActiveRecord::RecordNotFound
    render_not_found("Schedule")
  rescue ArgumentError => e
    render_error(e.message, :unprocessable_entity)
  end

  private

  def calendar_connection_params
    params.require(:calendar_connection)
          .permit(:provider, :external_calendar_id, :access_token, :refresh_token, :token_expires_at, metadata: {})
  end

  def serialize_connection(connection)
    {
      id: connection.id,
      provider: connection.provider,
      external_calendar_id: connection.external_calendar_id,
      status: connection.status,
      token_expires_at: connection.token_expires_at,
      metadata: connection.metadata
    }
  end

  def parse_range
    start_value = params[:week_start_on].presence || Date.current.beginning_of_week(:monday).iso8601
    end_value = params[:week_end_on].presence || (Date.iso8601(start_value) + 7.days).iso8601

    [ Time.zone.parse(start_value).beginning_of_day, Time.zone.parse(end_value).end_of_day ]
  rescue ArgumentError
    raise ArgumentError, "Invalid sync date range"
  end

  def parsed_week_start(input)
    return Date.current.beginning_of_week(:monday) if input.blank?

    Date.iso8601(input).beginning_of_week(:monday)
  rescue ArgumentError
    raise ActionController::BadRequest, "Invalid week_start_on"
  end
end

class Api::V1::CalendarFeedsController < Api::V1::BaseController
  def show
    week_start_on = parsed_week_start(params[:week_start_on])
    schedule = current_user.weekly_schedules.includes(visits: :patient).find_by!(week_start_on:)
    timezone = current_user.clinician_profile&.timezone.presence || "UTC"

    lines = [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//RouteCare//Schedule//EN",
      "CALSCALE:GREGORIAN",
      "METHOD:PUBLISH",
      "X-WR-TIMEZONE:#{timezone}"
    ]

    schedule.visits.order(:starts_at).each do |visit|
      lines.concat(
        [
          "BEGIN:VEVENT",
          "UID:routecare-visit-#{visit.id}@routecare",
          "DTSTAMP:#{Time.current.utc.strftime('%Y%m%dT%H%M%SZ')}",
          "DTSTART:#{visit.starts_at.utc.strftime('%Y%m%dT%H%M%SZ')}",
          "DTEND:#{visit.ends_at.utc.strftime('%Y%m%dT%H%M%SZ')}",
          "SUMMARY:RouteCare Visit - #{escape_ics(visit.patient.full_name)}",
          "DESCRIPTION:Patient visit scheduled via RouteCare",
          "LOCATION:#{escape_ics(visit.patient.address)}",
          "END:VEVENT"
        ]
      )
    end

    lines << "END:VCALENDAR"

    send_data(lines.join("\r\n"), type: "text/calendar", disposition: "attachment", filename: "routecare-schedule-#{week_start_on}.ics")
  rescue ActiveRecord::RecordNotFound
    render_not_found("Schedule")
  end

  private

  def parsed_week_start(input)
    return Date.current.beginning_of_week(:monday) if input.blank?

    Date.iso8601(input).beginning_of_week(:monday)
  rescue ArgumentError
    raise ActionController::BadRequest, "Invalid week_start_on"
  end

  def escape_ics(value)
    value.to_s.gsub("\\", "\\\\").gsub(";", "\\;").gsub(",", "\\,").gsub("\n", "\\n")
  end
end

require "cgi"
require "uri"

module Integrations
  module Calendar
    class GoogleCalendarClient < Integrations::BaseClient
      GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3".freeze
      GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token".freeze

      def blocked_events(connection:, range_start:, range_end:)
        token = valid_access_token_for(connection)
        response = HTTParty.get(
          "#{GOOGLE_CALENDAR_BASE_URL}/calendars/#{CGI.escape(connection.external_calendar_id)}/events",
          headers: auth_headers(token),
          query: {
            singleEvents: true,
            orderBy: "startTime",
            timeMin: range_start.utc.iso8601,
            timeMax: range_end.utc.iso8601
          }
        )

        return [] unless response.success?

        Array(response.parsed_response["items"]).filter_map do |item|
          starts_at = parse_google_time(item.dig("start", "dateTime"), item.dig("start", "date"))
          ends_at = parse_google_time(item.dig("end", "dateTime"), item.dig("end", "date"))
          next if starts_at.blank? || ends_at.blank?
          next if item["status"] == "cancelled"

          {
            external_event_id: item["id"],
            title: item["summary"].presence || "Busy",
            starts_at: starts_at,
            ends_at: ends_at
          }
        end
      end

      def list_calendars(connection:)
        token = valid_access_token_for(connection)
        response = HTTParty.get(
          "#{GOOGLE_CALENDAR_BASE_URL}/users/me/calendarList",
          headers: auth_headers(token)
        )
        return [] unless response.success?

        Array(response.parsed_response["items"]).map do |item|
          {
            id: item["id"],
            summary: item["summary"],
            primary: item["primary"] || false,
            access_role: item["accessRole"]
          }
        end
      end

      def upsert_visit_event(connection:, visit:)
        token = valid_access_token_for(connection)
        external_id = visit.external_calendar_event_id.presence || "routecare-visit-#{visit.id}"
        payload = {
          summary: "RouteCare Visit - #{visit.patient.full_name}",
          description: "Scheduled via RouteCare",
          start: { dateTime: visit.starts_at.utc.iso8601, timeZone: "UTC" },
          end: { dateTime: visit.ends_at.utc.iso8601, timeZone: "UTC" },
          location: visit.patient.address
        }

        response = HTTParty.put(
          "#{GOOGLE_CALENDAR_BASE_URL}/calendars/#{CGI.escape(connection.external_calendar_id)}/events/#{CGI.escape(external_id)}",
          headers: auth_headers(token).merge("Content-Type" => "application/json"),
          body: payload.to_json
        )

        return external_id if response.success?

        create_response = HTTParty.post(
          "#{GOOGLE_CALENDAR_BASE_URL}/calendars/#{CGI.escape(connection.external_calendar_id)}/events",
          headers: auth_headers(token).merge("Content-Type" => "application/json"),
          body: payload.merge(id: external_id).to_json
        )
        return external_id unless create_response.success?

        create_response.parsed_response["id"] || external_id
      end

      private

      def auth_headers(token)
        { "Authorization" => "Bearer #{token}" }
      end

      def parse_google_time(date_time, date_only)
        return Time.zone.parse(date_time) if date_time.present?
        return Time.zone.parse("#{date_only} 00:00:00") if date_only.present?

        nil
      end

      def valid_access_token_for(connection)
        expires_at = connection.token_expires_at
        return connection.access_token if expires_at.blank? || expires_at > 2.minutes.from_now

        refresh_access_token!(connection)
      end

      def refresh_access_token!(connection)
        raise ArgumentError, "Google refresh token is missing." if connection.refresh_token.blank?

        response = HTTParty.post(
          GOOGLE_TOKEN_URL,
          headers: { "Content-Type" => "application/x-www-form-urlencoded" },
          body: URI.encode_www_form(
            client_id: ENV.fetch("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret: ENV.fetch("GOOGLE_OAUTH_CLIENT_SECRET"),
            grant_type: "refresh_token",
            refresh_token: connection.refresh_token
          )
        )
        raise "Unable to refresh Google access token." unless response.success?

        payload = response.parsed_response
        connection.access_token = payload["access_token"]
        expires_in = payload["expires_in"].to_i
        connection.token_expires_at = expires_in.positive? ? Time.current + expires_in.seconds : nil
        connection.save!
        connection.access_token
      end
    end
  end
end

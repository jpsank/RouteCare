require "cgi"
require "securerandom"

class CalendarOauthController < ApplicationController
  before_action :authenticate_user!

  GOOGLE_AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth".freeze
  GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token".freeze
  GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events"
  ].freeze

  def google_start
    state = SecureRandom.hex(24)
    session[:google_calendar_oauth_state] = state

    redirect_to(
      "#{GOOGLE_AUTH_BASE_URL}?#{google_auth_query(state)}",
      allow_other_host: true
    )
  end

  def google_callback
    if params[:error].present?
      return redirect_to(root_path, alert: "Google authorization failed: #{params[:error]}")
    end

    expected_state = session.delete(:google_calendar_oauth_state)
    return redirect_to(root_path, alert: "Invalid Google OAuth state.") if expected_state.blank? || expected_state != params[:state]

    token_payload = exchange_google_code_for_token!(params[:code])
    calendar_id = fetch_google_calendar_id!(token_payload.fetch("access_token"))
    upsert_google_connection!(token_payload: token_payload, external_calendar_id: calendar_id)

    redirect_to(root_path(google_connected: "1"), notice: "Google Calendar connected.")
  rescue StandardError => e
    redirect_to(root_path, alert: "Google Calendar connection failed: #{e.message}")
  end

  private

  def google_auth_query(state)
    URI.encode_www_form(
      client_id: google_client_id,
      redirect_uri: google_calendar_oauth_callback_url,
      response_type: "code",
      access_type: "offline",
      include_granted_scopes: "true",
      prompt: "consent",
      scope: GOOGLE_SCOPES.join(" "),
      state: state
    )
  end

  def exchange_google_code_for_token!(code)
    raise ArgumentError, "Missing authorization code." if code.blank?

    response = HTTParty.post(
      GOOGLE_TOKEN_URL,
      headers: { "Content-Type" => "application/x-www-form-urlencoded" },
      body: URI.encode_www_form(
        client_id: google_client_id,
        client_secret: google_client_secret,
        code: code,
        grant_type: "authorization_code",
        redirect_uri: google_calendar_oauth_callback_url
      )
    )
    raise "Unable to exchange Google authorization code." unless response.success?

    response.parsed_response
  end

  def fetch_google_calendar_id!(access_token)
    response = HTTParty.get(
      "https://www.googleapis.com/calendar/v3/users/me/calendarList",
      headers: { "Authorization" => "Bearer #{access_token}" }
    )
    raise "Unable to fetch Google calendars." unless response.success?

    calendars = Array(response.parsed_response["items"])
    primary = calendars.find { |item| item["primary"] }
    (primary || calendars.first || {})["id"].presence || "primary"
  end

  def upsert_google_connection!(token_payload:, external_calendar_id:)
    connection = current_user.calendar_connections.find_or_initialize_by(provider: "google")
    connection.external_calendar_id = external_calendar_id
    connection.access_token = token_payload["access_token"]
    connection.refresh_token = token_payload["refresh_token"].presence || connection.refresh_token
    expires_in = token_payload["expires_in"].to_i
    connection.token_expires_at = expires_in.positive? ? Time.current + expires_in.seconds : nil
    connection.status = "active"
    connection.save!
  end

  def google_client_id
    ENV.fetch("GOOGLE_OAUTH_CLIENT_ID", "")
  end

  def google_client_secret
    ENV.fetch("GOOGLE_OAUTH_CLIENT_SECRET", "")
  end
end

module Integrations
  class TwilioSmsClient < BaseClient
    DEFAULT_BASE_URL = "https://api.twilio.com/2010-04-01".freeze

    def initialize(
      account_sid: ENV["ROUTECARE_TWILIO_ACCOUNT_SID"],
      auth_token: ENV["ROUTECARE_TWILIO_AUTH_TOKEN"],
      from_number: ENV["ROUTECARE_TWILIO_FROM_NUMBER"],
      base_url: ENV.fetch("ROUTECARE_TWILIO_BASE_URL", DEFAULT_BASE_URL)
    )
      super(api_key: auth_token)
      @account_sid = account_sid
      @from_number = from_number
      @base_url = base_url.to_s.sub(%r{/+$}, "")
    end

    def deliver(to:, body:)
      return { ok: true, provider_message_id: "test-sms-#{SecureRandom.hex(6)}" } if Rails.env.test?
      return { ok: false, error: "Twilio is not configured" } unless configured?
      return { ok: false, error: "Recipient phone is missing" } if to.blank?

      response = HTTParty.post(
        "#{base_url}/Accounts/#{account_sid}/Messages.json",
        basic_auth: { username: account_sid, password: api_key },
        body: {
          From: from_number,
          To: to,
          Body: body
        },
        timeout: 8
      )

      if response.code.between?(200, 299)
        { ok: true, provider_message_id: response.parsed_response["sid"] }
      else
        { ok: false, error: response.parsed_response["message"] || "Twilio request failed", status: response.code }
      end
    rescue StandardError => e
      { ok: false, error: e.message }
    end

    private

    attr_reader :account_sid, :from_number, :base_url

    def configured?
      account_sid.present? && api_key.present? && from_number.present?
    end
  end
end

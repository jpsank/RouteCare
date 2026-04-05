module Integrations
  class TelnyxSmsClient < BaseClient
    DEFAULT_BASE_URL = "https://api.telnyx.com/v2".freeze

    def initialize(
      api_key: ENV["ROUTECARE_TELNYX_API_KEY"],
      from_number: ENV["ROUTECARE_TELNYX_FROM_NUMBER"],
      base_url: ENV.fetch("ROUTECARE_TELNYX_BASE_URL", DEFAULT_BASE_URL)
    )
      super(api_key: api_key)
      @from_number = from_number
      @base_url = base_url.to_s.sub(%r{/+$}, "")
    end

    def deliver(to:, body:)
      return { ok: true, provider_message_id: "test-sms-#{SecureRandom.hex(6)}" } if Rails.env.test?
      return { ok: false, error: "Telnyx is not configured" } unless configured?
      return { ok: false, error: "Recipient phone is missing" } if to.blank?

      response = HTTParty.post(
        "#{base_url}/messages",
        headers: {
          "Authorization" => "Bearer #{api_key}",
          "Content-Type" => "application/json"
        },
        body: {
          from: from_number,
          to: to,
          text: body
        }.to_json,
        timeout: 8
      )

      if response.code.between?(200, 299)
        msg_id = response.parsed_response.dig("data", "id")
        { ok: true, provider_message_id: msg_id }
      else
        error_msg = response.parsed_response.dig("errors", 0, "detail") || "Telnyx request failed"
        { ok: false, error: error_msg, status: response.code }
      end
    rescue StandardError => e
      { ok: false, error: e.message }
    end

    private

    attr_reader :from_number, :base_url

    def configured?
      api_key.present? && from_number.present?
    end
  end
end

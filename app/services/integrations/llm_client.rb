module Integrations
  class LlmClient < BaseClient
    DEFAULT_BASE_URL = "https://api.openai.com/v1".freeze
    DEFAULT_MODEL = "gpt-4o-mini".freeze

    def initialize(
      api_key: ENV["ROUTECARE_LLM_API_KEY"],
      base_url: ENV.fetch("ROUTECARE_LLM_BASE_URL", DEFAULT_BASE_URL),
      model: ENV.fetch("ROUTECARE_LLM_MODEL", DEFAULT_MODEL)
    )
      super(api_key: api_key)
      @base_url = base_url.to_s.sub(%r{/+$}, "")
      @model = model
    end

    def chat_json(system_prompt:, user_prompt:, temperature: 0.2)
      return nil unless configured?

      response = HTTParty.post(
        "#{base_url}/chat/completions",
        headers: {
          "Authorization" => "Bearer #{api_key}",
          "Content-Type" => "application/json"
        },
        body: {
          model: model,
          temperature: temperature,
          response_format: { type: "json_object" },
          messages: [
            { role: "system", content: system_prompt },
            { role: "user", content: user_prompt }
          ]
        }.to_json,
        timeout: 8
      )

      return nil unless response.code.between?(200, 299)

      content = response.parsed_response.dig("choices", 0, "message", "content")
      return nil if content.blank?

      JSON.parse(content)
    rescue JSON::ParserError, StandardError
      nil
    end

    private

    attr_reader :base_url, :model
  end
end

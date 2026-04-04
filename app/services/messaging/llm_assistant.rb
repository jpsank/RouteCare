module Messaging
  class LlmAssistant
    def self.parse_reply(body:)
      payload = client.chat_json(
        system_prompt: <<~PROMPT,
          You classify patient reply messages for healthcare home-visit scheduling.
          Return only JSON with keys:
          - intent: one of confirm, decline, reschedule, unknown
          - proposed_windows: array of objects, each optionally including day, time_of_day, qualifier, time, time_range
          Keep extraction lightweight and literal.
        PROMPT
        user_prompt: "Reply text: #{body}"
      )
      return nil if payload.blank?

      {
        intent: payload["intent"].to_s,
        proposed_windows: Array(payload["proposed_windows"]).map { |window| window.is_a?(Hash) ? window : {} }
      }
    end

    def self.draft_message(context:)
      payload = client.chat_json(
        system_prompt: <<~PROMPT,
          You write warm, concise patient scheduling SMS/email messages for a field-clinician practice.
          Keep language natural and friendly.
          Do not force rigid YES/NO wording.
          Return JSON only with key "message".
        PROMPT
        user_prompt: context.to_json
      )
      return nil if payload.blank?

      payload["message"].to_s.strip.presence
    end

    def self.client
      @client ||= Integrations::LlmClient.new
    end
    private_class_method :client
  end
end

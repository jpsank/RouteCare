module Messaging
  class ReplyParser
    CONFIRM_PATTERNS = /\b(yes|yep|confirm|confirmed|ok|sounds good)\b/i
    DECLINE_PATTERNS = /\b(no|decline|can't|cannot|not available|nope)\b/i
    RESCHEDULE_PATTERNS = /\b(reschedule|different time|another time|later|earlier)\b/i
    DAY_NAMES = %w[monday tuesday wednesday thursday friday saturday sunday].freeze
    TIME_OF_DAY_PATTERNS = {
      "morning" => /\bmorning\b/i,
      "afternoon" => /\bafternoon\b/i,
      "evening" => /\bevening\b/i
    }.freeze
    TIME_RANGE_PATTERN = /\b(?<start>\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*(?:-|to)\s*(?<end>\d{1,2}(?::\d{2})?\s*(?:am|pm))\b/i
    TIME_POINT_PATTERN = /\b(?:(?<qualifier>after|before)\s+)?(?<time>\d{1,2}(?::\d{2})?\s*(?:am|pm))\b/i

    Result = Struct.new(:intent, :proposed_windows, :raw_text, keyword_init: true)

    def self.parse(text)
      body = text.to_s.strip
      llm_result = parse_with_llm(body)
      return llm_result if llm_result.present?

      intent =
        if body.match?(CONFIRM_PATTERNS)
          :confirm
        elsif body.match?(DECLINE_PATTERNS)
          :decline
        elsif body.match?(RESCHEDULE_PATTERNS)
          :reschedule
        else
          :unknown
        end

      Result.new(intent:, proposed_windows: extract_proposed_windows(body), raw_text: body)
    end

    def self.parse_with_llm(body)
      parsed = Messaging::LlmAssistant.parse_reply(body: body)
      return nil if parsed.blank?

      intent = parsed.fetch(:intent, "").to_s.downcase.to_sym
      intent = :unknown unless %i[confirm decline reschedule unknown].include?(intent)
      proposed_windows = Array(parsed[:proposed_windows]).map { |window| window.is_a?(Hash) ? window : {} }

      Result.new(intent: intent, proposed_windows: proposed_windows, raw_text: body)
    end
    private_class_method :parse_with_llm

    def self.extract_proposed_windows(body)
      normalized = body.to_s.downcase
      day_mentions = DAY_NAMES.filter { |day| normalized.match?(/\b#{day}\b/i) }

      time_details = []
      normalized.scan(TIME_RANGE_PATTERN) do |start_time, end_time|
        time_details << { "time_range" => { "start" => normalize_time(start_time), "end" => normalize_time(end_time) } }
      end

      normalized.scan(TIME_POINT_PATTERN) do |qualifier, time|
        next if qualifier.blank? && time_details.any? { |detail| detail["time_range"].present? && detail["time_range"]["start"] == normalize_time(time) }

        detail = { "time" => normalize_time(time) }
        detail["qualifier"] = qualifier.downcase if qualifier.present?
        time_details << detail
      end

      TIME_OF_DAY_PATTERNS.each do |label, pattern|
        time_details << { "time_of_day" => label } if normalized.match?(pattern)
      end

      windows = if day_mentions.any?
        day_mentions.map do |day|
          merge_window_parts(day, time_details)
        end
      elsif time_details.any?
        [ merge_window_parts(nil, time_details) ]
      else
        []
      end

      windows.uniq
    end

    def self.merge_window_parts(day, time_details)
      window = {}
      window["day"] = day if day.present?
      time_details.each do |detail|
        window.merge!(detail)
      end
      window
    end

    def self.normalize_time(value)
      value.to_s.squish.downcase
    end
  end
end

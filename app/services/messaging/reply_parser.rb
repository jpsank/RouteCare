module Messaging
  class ReplyParser
    CONFIRM_PATTERNS = /\b(yes|yep|confirm|confirmed|ok|sounds good)\b/i
    DECLINE_PATTERNS = /\b(no|decline|can't|cannot|not available|nope)\b/i
    RESCHEDULE_PATTERNS = /\b(reschedule|different time|another time|later|earlier)\b/i

    Result = Struct.new(:intent, :proposed_time, :raw_text, keyword_init: true)

    def self.parse(text)
      body = text.to_s.strip
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

      Result.new(intent:, proposed_time: nil, raw_text: body)
    end
  end
end

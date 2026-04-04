module Integrations
  module Calendar
    class AppleCalendarClient < Integrations::BaseClient
      def blocked_events(connection:, range_start:, range_end:)
        ics_url = connection.metadata["ics_url"]
        return [] if ics_url.blank?

        response = HTTParty.get(ics_url)
        return [] unless response.success?

        parse_ics_events(response.body, range_start:, range_end:)
      end

      def upsert_visit_event(connection:, visit:)
        feed_url = connection.metadata["ics_url"]
        raise ArgumentError, "Apple integration supports read-only ICS import. Use calendar_feed export to publish visits." if feed_url.present?

        "apple_visit_#{visit.id}"
      end

      private

      def parse_ics_events(body, range_start:, range_end:)
        events = []
        current = nil

        body.each_line do |line|
          stripped = line.strip
          if stripped == "BEGIN:VEVENT"
            current = {}
            next
          end
          if stripped == "END:VEVENT"
            if current.present?
              starts_at = parse_ics_datetime(current[:starts_at])
              ends_at = parse_ics_datetime(current[:ends_at])
              if starts_at.present? && ends_at.present? && starts_at < range_end && ends_at > range_start
                events << {
                  external_event_id: current[:uid].presence || "apple-#{starts_at.to_i}",
                  title: current[:summary].presence || "Busy",
                  starts_at:,
                  ends_at:
                }
              end
            end
            current = nil
            next
          end
          next if current.nil?

          current[:uid] = value_from_ics_line(stripped, "UID") if stripped.start_with?("UID")
          current[:summary] = value_from_ics_line(stripped, "SUMMARY") if stripped.start_with?("SUMMARY")
          current[:starts_at] = value_from_ics_line(stripped, "DTSTART") if stripped.start_with?("DTSTART")
          current[:ends_at] = value_from_ics_line(stripped, "DTEND") if stripped.start_with?("DTEND")
        end

        events
      end

      def value_from_ics_line(line, key)
        line.delete_prefix("#{key}:").sub(/\A#{key};[^:]*:/, "")
      end

      def parse_ics_datetime(value)
        return if value.blank?

        if value.match?(/\A\d{8}\z/)
          Time.zone.parse("#{value[0..3]}-#{value[4..5]}-#{value[6..7]} 00:00:00")
        elsif value.end_with?("Z")
          Time.zone.parse(value)
        else
          formatted = "#{value[0..3]}-#{value[4..5]}-#{value[6..7]} #{value[9..10]}:#{value[11..12]}:#{value[13..14]}"
          Time.zone.parse(formatted)
        end
      rescue StandardError
        nil
      end
    end
  end
end

module Messaging
  class AvailabilityWindowUpdater
    DAY_NAME_TO_WDAY = {
      "sunday" => 0, "monday" => 1, "tuesday" => 2, "wednesday" => 3,
      "thursday" => 4, "friday" => 5, "saturday" => 6
    }.freeze

    TIME_OF_DAY_RANGES = {
      "morning" => { start_minute: 8 * 60, end_minute: 12 * 60 },
      "afternoon" => { start_minute: 12 * 60, end_minute: 17 * 60 },
      "evening" => { start_minute: 17 * 60, end_minute: 20 * 60 }
    }.freeze

    DEFAULT_START_MINUTE = 8 * 60
    DEFAULT_END_MINUTE = 18 * 60

    def initialize(patient:, proposed_windows:)
      @patient = patient
      @proposed_windows = Array(proposed_windows).select { |w| w.is_a?(Hash) && w.present? }
    end

    def call
      return if @proposed_windows.empty?

      records = @proposed_windows.flat_map { |window| build_records(window) }.compact
      return if records.empty?

      @patient.patient_availability_windows.destroy_all
      records.each do |attrs|
        @patient.patient_availability_windows.create!(attrs)
      end
    end

    private

    def build_records(window)
      days = resolve_days(window)
      start_minute, end_minute = resolve_time_range(window)
      return [] if start_minute.nil? || end_minute.nil?

      days.map do |wday|
        { day_of_week: wday, start_minute: start_minute, end_minute: end_minute }
      end
    end

    def resolve_days(window)
      day_name = window["day"].to_s.downcase.strip
      if DAY_NAME_TO_WDAY.key?(day_name)
        [ DAY_NAME_TO_WDAY[day_name] ]
      else
        # No specific day — apply to all weekdays
        (1..5).to_a
      end
    end

    def resolve_time_range(window)
      if window["time_range"].is_a?(Hash)
        start_min = parse_time_string(window["time_range"]["start"])
        end_min = parse_time_string(window["time_range"]["end"])
        return [ start_min, end_min ] if start_min && end_min && end_min > start_min
      end

      if window["time_of_day"].present? && TIME_OF_DAY_RANGES.key?(window["time_of_day"].downcase)
        range = TIME_OF_DAY_RANGES[window["time_of_day"].downcase]
        return [ range[:start_minute], range[:end_minute] ]
      end

      if window["time"].present?
        time_min = parse_time_string(window["time"])
        if time_min
          qualifier = window["qualifier"].to_s.downcase
          case qualifier
          when "after"
            return [ time_min, DEFAULT_END_MINUTE ]
          when "before"
            return [ DEFAULT_START_MINUTE, time_min ]
          else
            # Single time — create a 2-hour window around it
            return [ [ time_min - 60, DEFAULT_START_MINUTE ].max, [ time_min + 60, DEFAULT_END_MINUTE ].min ]
          end
        end
      end

      [ nil, nil ]
    end

    def parse_time_string(str)
      return nil if str.blank?

      cleaned = str.to_s.strip.downcase
      match = cleaned.match(/\A(?<h>\d{1,2})(?::(?<m>\d{2}))?\s*(?<p>am|pm)\z/)
      return nil unless match

      hour = match[:h].to_i
      minute = (match[:m] || "0").to_i
      period = match[:p]
      hour = 0 if hour == 12 && period == "am"
      hour += 12 if hour != 12 && period == "pm"
      total = hour * 60 + minute
      total.between?(0, 1440) ? total : nil
    end
  end
end

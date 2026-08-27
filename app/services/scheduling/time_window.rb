module Scheduling
  class TimeWindow
    attr_reader :start_minute, :end_minute

    def initialize(start_minute, end_minute)
      @start_minute = start_minute
      @end_minute = end_minute
    end

    # Subtract a set of exclusion ranges (blackout/unavailable windows) from a
    # set of windows, splitting windows around any exclusion that falls inside
    # them. `windows` is [TimeWindow], `exclusions` is an array of TimeWindow or
    # { start_minute:, end_minute: } hashes. Returns [TimeWindow].
    def self.subtract(windows, exclusions)
      return windows if exclusions.blank?

      windows.flat_map { |window| subtract_from_one(window, exclusions) }
    end

    def self.subtract_from_one(window, exclusions)
      segments = [ window ]

      exclusions.each do |exclusion|
        ex_start = exclusion.respond_to?(:start_minute) ? exclusion.start_minute : exclusion[:start_minute]
        ex_end = exclusion.respond_to?(:end_minute) ? exclusion.end_minute : exclusion[:end_minute]

        segments = segments.flat_map do |seg|
          if ex_end <= seg.start_minute || ex_start >= seg.end_minute
            [ seg ]
          else
            pieces = []
            pieces << TimeWindow.new(seg.start_minute, ex_start) if ex_start > seg.start_minute
            pieces << TimeWindow.new(ex_end, seg.end_minute) if ex_end < seg.end_minute
            pieces
          end
        end
      end

      segments
    end
  end
end

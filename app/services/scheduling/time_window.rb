module Scheduling
  class TimeWindow
    attr_reader :start_minute, :end_minute

    def initialize(start_minute, end_minute)
      @start_minute = start_minute
      @end_minute = end_minute
    end
  end
end

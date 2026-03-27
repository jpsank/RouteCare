module Scheduling
  class CalendarConstraints
    def initialize(user:, week_start_on:)
      @user = user
      @week_start_on = week_start_on.to_date
      @week_end_on = @week_start_on + 6.days
    end

    def blocked_windows_by_day
      @blocked_windows_by_day ||= begin
        blocks = user.calendar_blocks.where(starts_at: week_start_on.beginning_of_day..week_end_on.end_of_day)
        grouped = Hash.new { |h, k| h[k] = [] }

        blocks.find_each do |block|
          day = block.starts_at.to_date
          grouped[day] << (block.starts_at...block.ends_at)
        end

        grouped.each_value { |windows| windows.sort_by!(&:first) }
        grouped
      end
    end

    alias blocked_ranges_by_day blocked_windows_by_day

    private

    attr_reader :user, :week_start_on, :week_end_on
  end
end

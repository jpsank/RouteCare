module Messaging
  class SuggestedVisitTimesBuilder
    SLOT_STEP_MINUTES = 15
    TRANSIT_BUFFER_MINUTES = 5
    DAY_INDEX = {
      "monday" => 0,
      "tuesday" => 1,
      "wednesday" => 2,
      "thursday" => 3,
      "friday" => 4,
      "saturday" => 5,
      "sunday" => 6
    }.freeze
    TIME_OF_DAY_MINUTES = {
      "morning" => 9 * 60,
      "afternoon" => 13 * 60,
      "evening" => 17 * 60
    }.freeze
    TIME_OF_DAY_WINDOWS = {
      "morning" => [ 9 * 60, 12 * 60 ],
      "afternoon" => [ 13 * 60, 17 * 60 ],
      "evening" => [ 17 * 60, 20 * 60 ]
    }.freeze

    def initialize(visit:, proposed_windows:)
      @visit = visit
      @proposed_windows = Array(proposed_windows)
      @routing_client = Integrations::RoutingClient.new
    end

    def call
      proposed_windows.flat_map do |window|
        build_suggestions(window)
      end
        .sort_by { |candidate| [ candidate.fetch(:score), candidate.fetch(:starts_at) ] }
        .uniq { |candidate| candidate.fetch(:starts_at) }
        .first(3)
        .map { |candidate| format_suggestion(candidate) }
    end

    private

    attr_reader :visit, :proposed_windows, :routing_client

    def build_suggestions(window)
      date = candidate_date(window["day"])
      return [] if date.blank?

      start_minute, end_minute = candidate_minute_range(window)
      return [] if start_minute.blank? || end_minute.blank?

      each_possible_start_minute(start_minute, end_minute).filter_map do |minute|
        starts_at = Time.use_zone(timezone_name) do
          Time.zone.local(date.year, date.month, date.day) + minute.minutes
        end
        ends_at = starts_at + visit.duration_minutes.minutes
        next unless within_workday?(starts_at, ends_at)
        next if blocked?(starts_at, ends_at)
        next unless feasible_with_neighbors?(starts_at, ends_at)

        {
          starts_at: starts_at,
          ends_at: ends_at,
          score: candidate_score(starts_at: starts_at, ends_at: ends_at, requested_window: window, requested_start_minute: minute)
        }
      end
    end

    def format_suggestion(candidate)
      {
        "starts_at" => candidate.fetch(:starts_at).iso8601,
        "ends_at" => candidate.fetch(:ends_at).iso8601,
        "label" => candidate.fetch(:starts_at).strftime("%A %b %-d at %-I:%M %p")
      }
    end

    def candidate_date(day_name)
      return visit.starts_at.to_date if day_name.blank?

      wday_index = DAY_INDEX[day_name.to_s.downcase]
      return if wday_index.nil?

      date = visit.weekly_schedule.week_start_on + wday_index.days
      date < visit.starts_at.to_date ? date + 7.days : date
    end

    def candidate_minute_range(window)
      if window["time_range"].present?
        [ parse_minutes(window.dig("time_range", "start")), parse_minutes(window.dig("time_range", "end")) ]
      elsif window["time"].present?
        base_minutes = parse_minutes(window["time"])
        return [ nil, nil ] if base_minutes.blank?

        case window["qualifier"]
        when "before"
          [ workday_start_minute, base_minutes ]
        when "after"
          [ base_minutes, workday_end_minute ]
        else
          [ base_minutes, base_minutes + visit.duration_minutes ]
        end
      elsif window["time_of_day"].present?
        TIME_OF_DAY_WINDOWS[window["time_of_day"]] || [ nil, nil ]
      else
        [ workday_start_minute, workday_end_minute ]
      end
    end

    def each_possible_start_minute(start_minute, end_minute)
      latest_start = [ end_minute - visit.duration_minutes, workday_end_minute - visit.duration_minutes ].min
      start_minute = [ start_minute, workday_start_minute ].max
      return [] if latest_start < start_minute

      minute = round_up_to_interval(start_minute)
      starts = []
      while minute <= latest_start
        starts << minute
        minute += SLOT_STEP_MINUTES
      end
      starts
    end

    def parse_minutes(value)
      return if value.blank?

      time = Time.zone.parse(value.to_s)
      return if time.blank?

      (time.hour * 60) + time.min
    rescue ArgumentError
      nil
    end

    def within_workday?(starts_at, ends_at)
      start_minute = (starts_at.hour * 60) + starts_at.min
      end_minute = (ends_at.hour * 60) + ends_at.min
      start_minute >= workday_start_minute && end_minute <= workday_end_minute
    end

    def blocked?(starts_at, ends_at)
      CalendarBlock.where(user: visit.weekly_schedule.user)
        .where("starts_at < ? AND ends_at > ?", ends_at, starts_at)
        .exists?
    end

    def feasible_with_neighbors?(starts_at, ends_at)
      previous_visit, next_visit = neighbor_visits(starts_at)

      return false if previous_visit.present? && previous_visit.ends_at + travel_from(previous_visit.patient) + TRANSIT_BUFFER_MINUTES.minutes > starts_at
      return false if next_visit.present? && ends_at + travel_to(next_visit.patient) + TRANSIT_BUFFER_MINUTES.minutes > next_visit.starts_at

      overlapping_visit = same_day_visits(starts_at.to_date)
        .where("starts_at < ? AND ends_at > ?", ends_at, starts_at)
        .exists?
      !overlapping_visit
    end

    def candidate_score(starts_at:, ends_at:, requested_window:, requested_start_minute:)
      previous_visit, next_visit = neighbor_visits(starts_at)
      route_penalty = insertion_route_penalty(previous_visit:, next_visit:)
      requested_penalty = requested_window_penalty(requested_window, requested_start_minute)
      gap_penalty = idle_gap_penalty(previous_visit:, next_visit:, starts_at:, ends_at:)
      (route_penalty * 10) + requested_penalty + gap_penalty
    end

    def insertion_route_penalty(previous_visit:, next_visit:)
      origin_point = previous_visit.present? ? point_for(previous_visit.patient) : start_point
      candidate_point = point_for(visit.patient)
      next_point = next_visit.present? ? point_for(next_visit.patient) : nil

      penalty = 0
      penalty += routing_client.travel_minutes(origin: origin_point, destination: candidate_point) if origin_point.present?
      penalty += routing_client.travel_minutes(origin: candidate_point, destination: next_point) if next_point.present?
      if origin_point.present? && next_point.present?
        penalty -= routing_client.travel_minutes(origin: origin_point, destination: next_point)
      end
      penalty
    end

    def requested_window_penalty(window, candidate_minute)
      if window["time_range"].present?
        start_minute = parse_minutes(window.dig("time_range", "start"))
        return 0 if start_minute.blank?
        (candidate_minute - start_minute).abs
      elsif window["time"].present?
        time_minute = parse_minutes(window["time"])
        return 0 if time_minute.blank?
        (candidate_minute - time_minute).abs
      elsif window["time_of_day"].present?
        midpoint = TIME_OF_DAY_MINUTES[window["time_of_day"]] || candidate_minute
        (candidate_minute - midpoint).abs
      else
        0
      end
    end

    def idle_gap_penalty(previous_visit:, next_visit:, starts_at:, ends_at:)
      penalty = 0
      penalty += (((starts_at - previous_visit.ends_at) / 60).to_i - 45).abs if previous_visit.present?
      penalty += (((next_visit.starts_at - ends_at) / 60).to_i - 45).abs if next_visit.present?
      penalty
    end

    def neighbor_visits(starts_at)
      day_visits = same_day_visits(starts_at.to_date).to_a
      previous_visit = day_visits.select { |existing| existing.starts_at <= starts_at }.max_by(&:starts_at)
      next_visit = day_visits.select { |existing| existing.starts_at > starts_at }.min_by(&:starts_at)
      [ previous_visit, next_visit ]
    end

    def same_day_visits(date)
      Visit.joins(:weekly_schedule)
        .where(weekly_schedules: { user_id: visit.weekly_schedule.user_id })
        .where.not(id: visit.id)
        .where(starts_at: date.beginning_of_day..date.end_of_day)
        .order(:starts_at)
    end

    def travel_from(patient)
      routing_client.travel_minutes(origin: point_for(patient), destination: point_for(visit.patient)).minutes
    end

    def travel_to(patient)
      routing_client.travel_minutes(origin: point_for(visit.patient), destination: point_for(patient)).minutes
    end

    def point_for(patient)
      { lat: patient.latitude, lng: patient.longitude }
    end

    def round_up_to_interval(minute)
      remainder = minute % SLOT_STEP_MINUTES
      remainder.zero? ? minute : minute + (SLOT_STEP_MINUTES - remainder)
    end

    def workday_start_minute
      visit.weekly_schedule.user.clinician_profile&.workday_start_minute || 8 * 60
    end

    def workday_end_minute
      visit.weekly_schedule.user.clinician_profile&.workday_end_minute || 18 * 60
    end

    def start_point
      profile = visit.weekly_schedule.user.clinician_profile
      return if profile.blank? || profile.home_latitude.blank? || profile.home_longitude.blank?

      { lat: profile.home_latitude, lng: profile.home_longitude }
    end

    def timezone_name
      visit.weekly_schedule.user.clinician_profile&.timezone.presence || Time.zone.name
    end
  end
end

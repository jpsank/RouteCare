module Scheduling
  class Retimer
    SLOT_STEP_MINUTES = 15
    TRANSIT_BUFFER_MINUTES = 5

    def initialize(
      travel_matrix:,
      locked_visits: [],
      lunch_config: nil,
      day_start_minute:,
      day_end_minute:,
      start_point: nil,
      routing_client: Integrations::RoutingClient.new,
      max_continuous_work_minutes: nil,
      required_break_minutes: 15,
      charting_buffer_minutes: 0
    )
      @travel_matrix = travel_matrix
      @locked_visits = locked_visits
      @lunch_config = lunch_config
      @day_start_minute = day_start_minute
      @day_end_minute = day_end_minute
      @start_point = start_point
      @routing_client = routing_client
      @max_continuous_work_minutes = max_continuous_work_minutes
      @required_break_minutes = required_break_minutes
      @charting_buffer_minutes = charting_buffer_minutes
    end

    # Assigns concrete start/end times to an ordered list of slots for a single day.
    # Returns { slots: [...], lunch: {start_minute, end_minute} | nil, feasible: true/false }
    def call(ordered_slots, date)
      return { slots: ordered_slots, lunch: nil, feasible: true } if ordered_slots.empty?

      locked_ranges = @locked_visits.map { |v| (v.starts_at...v.ends_at) }

      current_minute = @day_start_minute
      previous_patient_id = nil
      retimed = []
      lunch_taken = false
      lunch_placement = nil
      accumulated_work = 0

      ordered_slots.each do |slot|
        patient = slot[:patient]
        duration = slot[:duration] || patient.visit_duration_minutes
        slot_footprint = duration + @charting_buffer_minutes

        transit = compute_transit(previous_patient_id, patient)
        raw_start = current_minute + transit + (previous_patient_id.nil? ? 0 : TRANSIT_BUFFER_MINUTES)

        # Mandatory break: if drive + completing this visit would exceed max continuous work,
        # take a break before starting the visit (same duty window as slot_footprint below).
        if @max_continuous_work_minutes && accumulated_work + transit + slot_footprint > @max_continuous_work_minutes
          raw_start += @required_break_minutes
          accumulated_work = 0
        end

        earliest_start = round_up_to_interval(raw_start)

        # Lunch insertion
        if @lunch_config && !lunch_taken && earliest_start >= @lunch_config[:earliest_start]
          lunch_start = [ earliest_start, @lunch_config[:earliest_start] ].max
          lunch_start = [ lunch_start, @lunch_config[:latest_start] ].min
          lunch_end = lunch_start + @lunch_config[:duration]
          lunch_placement = { start_minute: lunch_start, end_minute: lunch_end }
          if earliest_start < lunch_end
            earliest_start = round_up_to_interval(lunch_end)
          end
          lunch_taken = true
          accumulated_work = 0
        end

        # Skip past locked visit time ranges
        locked_ranges.each do |range|
          proposed_start = Time.zone.parse("#{date} #{minute_to_hhmm(earliest_start)}")
          proposed_end = proposed_start + duration.minutes
          if proposed_start < range.end && proposed_end > range.begin
            locked_end_minute = (range.end - range.begin.beginning_of_day) / 60
            earliest_start = round_up_to_interval(locked_end_minute.to_i + TRANSIT_BUFFER_MINUTES)
          end
        end

        if earliest_start + duration > @day_end_minute
          retimed << slot
          return { slots: retimed, lunch: lunch_placement, feasible: false }
        else
          starts_at = Time.zone.parse("#{date} #{minute_to_hhmm(earliest_start)}")
          ends_at = starts_at + duration.minutes
          retimed << slot.merge(starts_at: starts_at, ends_at: ends_at)
          # Advance past visit + charting buffer so the next visit is spaced correctly
          accumulated_work += slot_footprint + transit
          current_minute = earliest_start + slot_footprint
        end

        previous_patient_id = patient.id
      end

      if @lunch_config && !lunch_taken
        lunch_placement = { start_minute: @lunch_config[:earliest_start], end_minute: @lunch_config[:earliest_start] + @lunch_config[:duration] }
      end

      { slots: retimed, lunch: lunch_placement, feasible: true }
    end

    private

    def compute_transit(previous_patient_id, patient)
      if previous_patient_id.nil?
        return 0 if @start_point.blank?

        @routing_client.travel_minutes(
          origin: @start_point,
          destination: { lat: patient.latitude, lng: patient.longitude }
        )
      else
        @travel_matrix.dig(previous_patient_id, patient.id) || 0
      end
    end

    def round_up_to_interval(minute)
      remainder = minute % SLOT_STEP_MINUTES
      remainder.zero? ? minute : minute + (SLOT_STEP_MINUTES - remainder)
    end

    def minute_to_hhmm(minute)
      "%<hour>02d:%<minute>02d" % { hour: minute / 60, minute: minute % 60 }
    end
  end
end

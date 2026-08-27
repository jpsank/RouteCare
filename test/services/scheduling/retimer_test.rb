require "test_helper"

class Scheduling::RetimerTest < ActiveSupport::TestCase
  setup do
    user = User.create!(email: "retimer-test@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    @patient_a = profile.patients.create!(
      full_name: "Patient A", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )
    @patient_b = profile.patients.create!(
      full_name: "Patient B", phone: "5550002", address_line1: "200 Oak Ave",
      city: "Cambridge", state: "MA", postal_code: "02139",
      latitude: 42.3736, longitude: -71.1097,
      required_visits_per_week: 1, visit_duration_minutes: 60
    )
    @travel_matrix = {
      @patient_a.id => { @patient_a.id => 0, @patient_b.id => 20 },
      @patient_b.id => { @patient_a.id => 20, @patient_b.id => 0 }
    }
    @date = Date.new(2026, 4, 6)
  end

  test "assigns start times respecting transit between visits" do
    Time.use_zone("America/New_York") do
      slots = [
        { patient: @patient_a, duration: 45 },
        { patient: @patient_b, duration: 60 }
      ]
      result = build_retimer.call(slots, @date)

      assert result[:feasible]
      assert_equal 2, result[:slots].size
      gap = (result[:slots][1][:starts_at] - result[:slots][0][:ends_at]) / 60.0
      assert gap >= 20, "Gap #{gap}min should be >= 20min transit"
    end
  end

  test "picks the traffic bucket matching the leg's actual departure time" do
    Time.use_zone("America/New_York") do
      bucketed_matrix = Scheduling::BucketedTravelMatrix.new(
        morning_rush: {
          @patient_a.id => { @patient_a.id => 0, @patient_b.id => 5 },
          @patient_b.id => { @patient_a.id => 5, @patient_b.id => 0 }
        },
        off_peak: {
          @patient_a.id => { @patient_a.id => 0, @patient_b.id => 50 },
          @patient_b.id => { @patient_a.id => 50, @patient_b.id => 0 }
        }
      )

      # Day starts at 7:00am (420) — patient A's visit finishes well inside
      # the morning-rush window (420-570), so the second leg should use the
      # morning_rush bucket (5min), not off-peak (50min).
      retimer = Scheduling::Retimer.new(
        travel_matrix: bucketed_matrix,
        locked_visits: [],
        day_start_minute: 420,
        day_end_minute: 1080,
        start_point: nil
      )

      slots = [
        { patient: @patient_a, duration: 45 },
        { patient: @patient_b, duration: 60 }
      ]
      result = retimer.call(slots, @date)

      assert result[:feasible]
      # Gap = 5min transit + 5min transit buffer, rounded up to the next
      # 15min slot = 15min. If the off-peak bucket (50min) had been used
      # instead, the gap would round up to 60min.
      gap = (result[:slots][1][:starts_at] - result[:slots][0][:ends_at]) / 60.0
      assert_in_delta 15, gap, 0.01, "Expected the morning-rush bucket's travel time to drive the gap, got #{gap}min"
    end
  end

  test "returns feasible false when visits exceed workday" do
    Time.use_zone("America/New_York") do
      slots = Array.new(8) { { patient: @patient_a, duration: 90 } }
      result = build_retimer(day_end_minute: 600).call(slots, @date)
      refute result[:feasible]
    end
  end

  test "inserts lunch break" do
    Time.use_zone("America/New_York") do
      slots = [
        { patient: @patient_a, duration: 45 },
        { patient: @patient_b, duration: 60 }
      ]
      lunch_config = { earliest_start: 720, latest_start: 780, duration: 30 }
      result = build_retimer(lunch_config: lunch_config).call(slots, @date)

      assert result[:lunch]
      assert result[:lunch][:start_minute] >= 720
    end
  end

  test "works with empty slots" do
    Time.use_zone("America/New_York") do
      result = build_retimer.call([], @date)
      assert result[:feasible]
      assert_empty result[:slots]
    end
  end

  test "mandatory break before visit when drive plus that visit exceeds max continuous work" do
    Time.use_zone("America/New_York") do
      slots = [
        { patient: @patient_a, duration: 45 },
        { patient: @patient_b, duration: 60 }
      ]
      # After A: accumulated = 45 + 0 transit. B: 45 + 20 transit + 60 visit = 125 > 100.
      # Without counting the upcoming visit, 45 + 20 = 65 would not trigger a break.
      result = build_retimer(
        max_continuous_work_minutes: 100,
        required_break_minutes: 15
      ).call(slots, @date)

      assert result[:feasible]
      gap_min = (result[:slots][1][:starts_at] - result[:slots][0][:ends_at]) / 60.0
      assert gap_min >= 40.0, "Expected break + transit + buffer (>= 40m), gap was #{gap_min}m"
    end
  end

  test "skips past a patient's unavailable window" do
    Time.use_zone("America/New_York") do
      @patient_a.patient_availability_windows.create!(
        day_of_week: @date.wday, start_minute: 480, end_minute: 600, available: false
      )
      slots = [ { patient: @patient_a, duration: 45 } ]
      result = build_retimer.call(slots, @date)

      assert result[:feasible]
      start_minute = (result[:slots][0][:starts_at] - result[:slots][0][:starts_at].beginning_of_day) / 60
      assert start_minute >= 600, "Visit should start at/after the blackout window ends (600), got #{start_minute}"
    end
  end

  test "unavailable window does not affect an unrelated patient" do
    Time.use_zone("America/New_York") do
      @patient_a.patient_availability_windows.create!(
        day_of_week: @date.wday, start_minute: 480, end_minute: 600, available: false
      )
      slots = [ { patient: @patient_b, duration: 60 } ]
      result = build_retimer.call(slots, @date)

      assert result[:feasible]
      start_minute = (result[:slots][0][:starts_at] - result[:slots][0][:starts_at].beginning_of_day) / 60
      assert_equal 480, start_minute
    end
  end

  private

  def build_retimer(
    day_start_minute: 480,
    day_end_minute: 1080,
    lunch_config: nil,
    max_continuous_work_minutes: nil,
    required_break_minutes: 15,
    charting_buffer_minutes: 0
  )
    Scheduling::Retimer.new(
      travel_matrix: @travel_matrix,
      locked_visits: [],
      lunch_config: lunch_config,
      day_start_minute: day_start_minute,
      day_end_minute: day_end_minute,
      start_point: nil,
      max_continuous_work_minutes: max_continuous_work_minutes,
      required_break_minutes: required_break_minutes,
      charting_buffer_minutes: charting_buffer_minutes
    )
  end
end

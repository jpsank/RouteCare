module Scheduling
  # Coarse time-of-day traffic buckets used for travel-time lookups.
  #
  # Google's Routes "Compute Route Matrix" endpoint only accepts ONE
  # departureTime per batch call — you can't give individual legs their own
  # departure time in one request. Computing one matrix per exact leg time
  # would be leg_count× the API cost, which is prohibitively expensive (and
  # this app has already had a provider account suspended once from excess
  # test traffic this session).
  #
  # Instead we compute a small, FIXED number of matrices per optimization
  # run — one per bucket below — so total API cost is bucket_count× the
  # flat-matrix cost, not leg_count×. Each leg then picks whichever bucket's
  # matrix best matches its estimated/actual time of day.
  #
  # IMPORTANT: keep boundaries in sync with
  # solver_service/solver/traffic_buckets.py (mirrored on the Python side).
  module TrafficBuckets
    MORNING_RUSH = { name: :morning_rush, start_minute: 420, end_minute: 570, departure_minute: 480 }.freeze
    LUNCH = { name: :lunch, start_minute: 690, end_minute: 810, departure_minute: 720 }.freeze
    AFTER_WORK_RUSH = { name: :after_work_rush, start_minute: 960, end_minute: 1110, departure_minute: 1020 }.freeze
    # Catch-all bucket for everything outside the three rush/lunch windows above.
    OFF_PEAK = { name: :off_peak, start_minute: nil, end_minute: nil, departure_minute: 600 }.freeze

    RUSH_BUCKETS = [ MORNING_RUSH, LUNCH, AFTER_WORK_RUSH ].freeze
    ALL = (RUSH_BUCKETS + [ OFF_PEAK ]).freeze

    # Returns the bucket Hash (see constants above) whose
    # [start_minute, end_minute) window covers `minute`, or OFF_PEAK if
    # none does (including when `minute` is nil, i.e. unknown).
    def self.bucket_for_minute(minute)
      return OFF_PEAK if minute.nil?

      RUSH_BUCKETS.find { |b| minute >= b[:start_minute] && minute < b[:end_minute] } || OFF_PEAK
    end

    def self.name_for_minute(minute)
      bucket_for_minute(minute)[:name]
    end
  end
end

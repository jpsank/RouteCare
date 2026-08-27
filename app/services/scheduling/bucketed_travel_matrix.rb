module Scheduling
  # Wraps one travel-time matrix per traffic bucket (see TrafficBuckets).
  #
  # Presents a Hash-like #dig(from, to) so call sites that don't know (or
  # can't cheaply estimate) a leg's time-of-day can keep calling
  # `matrix.dig(from, to)` unchanged and transparently get the off-peak
  # matrix — the closest analog to the old single-flat-matrix behavior.
  # Call sites that DO know (or can estimate) when a leg happens should
  # pass `minute:` to get the bucket that actually matches.
  class BucketedTravelMatrix
    def initialize(matrices)
      @matrices = matrices # { bucket_name_sym => { id => { id => minutes } } }
    end

    def dig(from_id, to_id, minute: nil)
      matrix_for(minute).dig(from_id, to_id)
    end

    def matrix_for(minute)
      @matrices.fetch(Scheduling::TrafficBuckets.name_for_minute(minute), {})
    end

    def off_peak
      @matrices[Scheduling::TrafficBuckets::OFF_PEAK[:name]] || {}
    end

    # Raw { bucket_name => matrix } — used for serializing to the Python
    # solver, and for aggregate stats across all pairs.
    def to_h
      @matrices
    end

    def each_bucket(&block)
      @matrices.each(&block)
    end

    def empty?
      @matrices.values.all?(&:blank?)
    end
  end
end

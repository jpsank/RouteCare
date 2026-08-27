# frozen_string_literal: true

require "digest"

module Scheduling
  # Stable digest over the solver-relevant inputs that instance-id matching alone
  # can't see: clinician working hours/breaks, calendar blocks, and per-patient
  # location/priority/availability/spacing. Two SolverInputData built from the
  # same instance-id set can still describe a materially different routing
  # problem (patient moved, working hours changed, a calendar block was added) —
  # this fingerprint lets WarmStartOutput tell the two apart so a warm-started
  # re-solve only reuses a prior plan when the underlying problem hasn't changed.
  class SolverInputFingerprint
    def self.compute(input)
      clinician = input.clinician

      patients = input.patients.sort_by(&:id).map do |p|
        [
          p.id, p.location, p.visit_duration_minutes, p.required_visits_per_week,
          p.min_days_between_visits, p.max_days_between_visits, p.priority,
          p.availability_windows
        ]
      end

      calendar_blocks = input.calendar_blocks
        .map { |b| [ b.date, b.starts_at, b.ends_at ] }
        .sort_by { |d, s, e| [ d.to_s, s.to_s, e.to_s ] }

      Digest::SHA256.hexdigest([ clinician, patients, calendar_blocks ].inspect)
    end
  end
end

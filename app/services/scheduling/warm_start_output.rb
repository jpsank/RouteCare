# frozen_string_literal: true

module Scheduling
  # Builds a SolverOutputData from an existing WeeklySchedule so the CP-SAT service can warm-start.
  #
  # Maps each +patient_{id}_visit_{i}+ instance to the +i+th floating (non-locked) visit for that
  # patient when visits are ordered by +starts_at+. If visit order and instance indices disagree,
  # warm-start is skipped or CP-SAT may reject the prior plan.
  class WarmStartOutput
    # Returns nil if the schedule cannot be aligned to +input.instances+ (missing visits, etc.).
    def self.from_schedule(user:, week_start_on:, input:)
      schedule = user.weekly_schedules.find_by(week_start_on: week_start_on)
      return nil unless schedule

      locked = schedule.visits.where(status: %w[confirmed completed])
      floating = schedule.visits.where.not(id: locked.select(:id)).order(:starts_at)
      by_patient = floating.group_by(&:patient_id).transform_values { |vs| vs.sort_by(&:starts_at) }

      planned = []
      input.instances.each do |inst|
        return nil unless inst.id.match?(/\Apatient_\d+_visit_\d+\z/)

        idx = inst.id.split("_").last.to_i
        visits = by_patient[inst.patient_id]
        return nil unless visits && visits[idx]

        v = visits[idx]
        planned << PlannedVisit.new(
          instance_id: inst.id,
          patient_id: inst.patient_id,
          date: v.starts_at.to_date,
          starts_at: v.starts_at,
          ends_at: v.ends_at,
          soft_constraint_override: v.soft_constraint_override
        )
      end

      return nil unless planned.size == input.instances.size

      summary = schedule.optimization_summary || {}
      lunch = summary["lunch_breaks"] || {}
      lunch = lunch.transform_keys(&:to_s) if lunch.respond_to?(:transform_keys)

      meta = summary.except("lunch_breaks", "generated_at")
      meta = meta.stringify_keys
      meta["optimizer_type"] ||= "prior_schedule"

      SolverOutputData.new(
        planned_visits: planned,
        lunch_placements: lunch,
        fitness: schedule.total_drive_minutes.to_f,
        metadata: meta.symbolize_keys
      )
    end
  end
end

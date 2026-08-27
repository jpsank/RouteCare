# frozen_string_literal: true

module Scheduling
  # Builds a SolverOutputData from an existing WeeklySchedule so the CP-SAT service can warm-start.
  #
  # Prefer matching by persisted +Visit#instance_id+ when every floating visit has one and IDs align
  # with +input.instances+. Otherwise falls back to ordering +patient_{id}_visit_{i}+ by +starts_at+
  # per patient (legacy).
  #
  # Matching the *set* of instance ids is necessary but not sufficient: two weeks can have the exact
  # same instance ids (same patients, same required_visits_per_week) while describing a materially
  # different routing problem — a patient moved, a clinician's working hours changed, a calendar
  # block was added. When the prior schedule carries a +solver_input_fingerprint+ (see
  # SolverInputFingerprint), we also require that to match before treating the plan as reusable.
  class WarmStartOutput
    # Returns nil if the schedule cannot be aligned to +input.instances+ (missing visits, instance
    # identity mismatch, or the underlying routing problem has materially changed).
    def self.from_schedule(user:, week_start_on:, input:)
      schedule = user.weekly_schedules.find_by(week_start_on: week_start_on)
      return nil unless schedule

      stored_fingerprint = schedule.optimization_summary&.dig("solver_input_fingerprint")
      if stored_fingerprint.present?
        return nil unless stored_fingerprint == SolverInputFingerprint.compute(input)
      end

      locked = schedule.visits.where(status: %w[confirmed completed])
      floating = schedule.visits.where.not(id: locked.select(:id))

      planned = match_by_instance_id(floating, input)
      planned ||= match_by_visit_index(floating, input)

      return nil unless planned

      # Defense in depth: match_by_instance_id/match_by_visit_index only ever append a
      # PlannedVisit for an id that exists in input.instances, so this checks the actual
      # SET of instance ids lines up (not merely the count) — a prior version of this
      # method compared sizes only, which can't detect a same-count patient swap (one
      # patient deactivated, a different one added, net instance count unchanged).
      return nil unless planned.map(&:instance_id).sort == input.instances.map(&:id).sort

      build_output(schedule, planned)
    end

    def self.match_by_instance_id(floating, input)
      list = floating.to_a
      return nil unless list.size == input.instances.size
      return nil unless list.all? { |v| v.instance_id.present? }

      by_instance = list.index_by(&:instance_id)
      return nil unless by_instance.size == input.instances.size

      planned = []
      input.instances.each do |inst|
        v = by_instance[inst.id]
        return nil unless v && v.patient_id == inst.patient_id

        planned << planned_visit_from_record(inst, v)
      end

      planned
    end
    private_class_method :match_by_instance_id

    def self.match_by_visit_index(floating, input)
      by_patient = floating.order(:starts_at).group_by(&:patient_id).transform_values do |vs|
        vs.sort_by(&:starts_at)
      end

      planned = []
      input.instances.each do |inst|
        return nil unless inst.id.match?(/\Apatient_\d+_visit_\d+\z/)

        idx = inst.id.split("_").last.to_i
        visits = by_patient[inst.patient_id]
        return nil unless visits && visits[idx]

        v = visits[idx]
        planned << planned_visit_from_record(inst, v)
      end

      planned
    end
    private_class_method :match_by_visit_index

    def self.planned_visit_from_record(inst, v)
      PlannedVisit.new(
        instance_id: inst.id,
        patient_id: inst.patient_id,
        date: v.starts_at.to_date,
        starts_at: v.starts_at,
        ends_at: v.ends_at,
        soft_constraint_override: v.soft_constraint_override
      )
    end
    private_class_method :planned_visit_from_record

    def self.build_output(schedule, planned)
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
    private_class_method :build_output
  end
end

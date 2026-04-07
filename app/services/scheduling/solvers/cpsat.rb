# frozen_string_literal: true

module Scheduling
  module Solvers
    # Python microservice with backend=cpsat (OR-Tools CP-SAT + day routing).
    class Cpsat < Hgs
      def initialize(input, time_budget: 30, upper_bound: nil, **_options)
        super(input, time_budget: time_budget, **_options)
        @upper_bound = upper_bound
      end

      def solve
        response = solver_post(
          query: { backend: "cpsat", time_budget: @time_budget },
          timeout: @time_budget + 30,
          body: request_body
        )
        deserialize_output(response.parsed_response)
      end

      def request_body
        base = JSON.parse(super())
        base["upper_bound"] = serialize_upper_bound(@upper_bound) if @upper_bound
        base.to_json
      end

      private

      def serialize_upper_bound(out)
        {
          "planned_visits" => out.planned_visits.map { |v| serialize_planned_visit(v) },
          "lunch_placements" => serialize_lunch(out.lunch_placements),
          "fitness" => out.fitness.to_f,
          "metadata" => out.metadata.stringify_keys
        }
      end

      def serialize_planned_visit(v)
        {
          "instance_id" => v.instance_id,
          "patient_id" => v.patient_id,
          "date" => v.date.to_s,
          "starts_at" => v.starts_at.iso8601,
          "ends_at" => v.ends_at.iso8601,
          "soft_constraint_override" => v.soft_constraint_override
        }
      end

      def serialize_lunch(lunch)
        lunch.to_h.transform_keys(&:to_s).transform_values do |slot|
          slot.is_a?(Hash) ? slot.stringify_keys : slot
        end
      end
    end
  end
end

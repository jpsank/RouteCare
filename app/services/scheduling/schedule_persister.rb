module Scheduling
  class SchedulePersister
    TRANSIT_BUFFER_MINUTES = 5

    def initialize(user:, week_start_on:, start_point: nil, routing_client: Integrations::RoutingClient.new)
      @user = user
      @week_start_on = week_start_on
      @start_point = start_point
      @routing_client = routing_client
    end

    # Persists day_routes and metadata into a WeeklySchedule, preserving locked visits.
    # day_routes: { Date => [{ patient:, starts_at:, ends_at:, duration:, soft_constraint_override:, instance_id: }] }
    # lunch_placements: { "date_string" => { start_minute:, end_minute: } }
    # locked_visits: [Visit] — confirmed/completed visits to preserve
    def persist(day_routes:, lunch_placements: {}, locked_visits: [], metadata: {}, travel_matrix: {})
      schedule = @user.weekly_schedules.find_or_initialize_by(week_start_on: @week_start_on)
      schedule.status = :optimized

      ActiveRecord::Base.transaction do
        schedule.save! if schedule.new_record?

        unlocked_visit_ids = schedule.visit_ids - locked_visits.map(&:id)
        archive_messages_for_visits!(unlocked_visit_ids)
        schedule.visits.where(id: unlocked_visit_ids).delete_all

        create_visits!(schedule, day_routes, locked_visits, travel_matrix)
        calculate_drive_metrics!(schedule)

        # Ensure lunch data covers all working days
        lunch_data = lunch_placements.transform_keys(&:to_s)
        schedule.optimization_summary = {
          generated_at: Time.current,
          lunch_breaks: lunch_data
        }.merge(metadata)
        schedule.save!
      end

      schedule
    end

    private

    def create_visits!(schedule, day_routes, locked_visits, travel_matrix)
      locked_by_date = locked_visits.group_by { |v| v.starts_at.to_date }

      day_routes.each do |date, slots|
        locked = locked_by_date[date] || []

        all_day_items = []
        locked.each { |v| all_day_items << { type: :locked, visit: v, starts_at: v.starts_at, patient: v.patient } }
        slots.each { |s| all_day_items << { type: :new, slot: s, starts_at: s[:starts_at], patient: s[:patient] } }
        all_day_items.sort_by! { |item| item[:starts_at] }

        current_point = start_point_for_day
        previous_patient_id = nil

        all_day_items.each_with_index do |item, index|
          drive_minutes =
            if previous_patient_id.nil?
              travel_from_start(item[:patient])
            else
              (travel_matrix.dig(previous_patient_id, item[:patient].id) || 0)
            end

          if item[:type] == :locked
            item[:visit].update!(position_in_day: index, drive_from_previous_minutes: drive_minutes)
          else
            slot = item[:slot]
            schedule.visits.create!(
              patient: slot[:patient],
              starts_at: slot[:starts_at],
              ends_at: slot[:ends_at],
              duration_minutes: ((slot[:ends_at] - slot[:starts_at]) / 60).to_i,
              status: :pending_patient_confirmation,
              position_in_day: index,
              drive_from_previous_minutes: drive_minutes,
              soft_constraint_override: slot[:soft_constraint_override] || false,
              source: "optimizer",
              instance_id: slot[:instance_id].presence
            )
          end

          previous_patient_id = item[:patient].id
          current_point = { lat: item[:patient].latitude, lng: item[:patient].longitude }
        end
      end

      # Update locked visits on days with no new visits
      (locked_by_date.keys - day_routes.keys).each do |date|
        locked_by_date[date].each_with_index do |visit, index|
          visit.update!(position_in_day: index)
        end
      end
    end

    def calculate_drive_metrics!(schedule)
      total_drive = schedule.visits.sum(:drive_from_previous_minutes)
      schedule.total_drive_minutes = total_drive
      schedule.baseline_drive_minutes = (total_drive * 1.35).ceil
    end

    def archive_messages_for_visits!(visit_ids)
      return if visit_ids.empty?

      PatientMessage.where(visit_id: visit_ids).find_each do |message|
        message.update!(
          visit_id: nil,
          metadata: (message.metadata || {}).merge(
            archived_from_visit_id: message.visit_id,
            archived_at: Time.current.iso8601,
            archive_reason: "schedule_re_optimized"
          )
        )
      end
    end

    def start_point_for_day
      return @start_point if @start_point.present?

      profile = @user.clinician_profile
      return if profile.blank? || profile.home_latitude.blank?

      { lat: profile.home_latitude, lng: profile.home_longitude }
    end

    def travel_from_start(patient)
      origin = start_point_for_day
      return 0 if origin.blank?

      @routing_client.travel_minutes(
        origin: origin,
        destination: { lat: patient.latitude, lng: patient.longitude }
      )
    end
  end
end

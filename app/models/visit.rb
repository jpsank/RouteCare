class Visit < ApplicationRecord
  belongs_to :weekly_schedule
  belongs_to :patient

  enum :status, {
    confirmed: "confirmed",
    pending_patient_confirmation: "pending_patient_confirmation",
    declined: "declined",
    unscheduled: "unscheduled",
    completed: "completed"
  }, validate: true

  validates :starts_at, :ends_at, :duration_minutes, :position_in_day, presence: true
  validates :duration_minutes, numericality: { greater_than: 0 }
  validates :position_in_day, :drive_from_previous_minutes, numericality: { greater_than_or_equal_to: 0 }
  validates :source, inclusion: { in: %w[optimizer manual reschedule] }
  validates :instance_id,
    format: { with: /\Apatient_\d+_visit_\d+\z/, message: "must look like patient_123_visit_0" },
    allow_nil: true,
    allow_blank: true
  validate :end_after_start

  scope :for_day, ->(date) { where(starts_at: date.beginning_of_day..date.end_of_day).order(:starts_at) }

  private

  def end_after_start
    return if starts_at.blank? || ends_at.blank?
    return if ends_at > starts_at

    errors.add(:ends_at, "must be after start time")
  end
end

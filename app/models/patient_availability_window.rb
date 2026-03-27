class PatientAvailabilityWindow < ApplicationRecord
  belongs_to :patient

  validates :day_of_week, inclusion: { in: 0..6 }
  validates :start_minute, :end_minute, presence: true
  validate :end_after_start

  private

  def end_after_start
    return if start_minute.blank? || end_minute.blank?
    return if end_minute > start_minute

    errors.add(:end_minute, "must be after start_minute")
  end
end

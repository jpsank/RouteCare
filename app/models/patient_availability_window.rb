class PatientAvailabilityWindow < ApplicationRecord
  belongs_to :patient

  # available: true  => an "available" window — a visit must fall entirely within
  #                      one of a patient's available windows (when any are defined).
  # available: false => an "unavailable"/blackout window — a visit must never
  #                      overlap this range, regardless of available windows.
  validates :day_of_week, inclusion: { in: 0..6 }
  validates :start_minute, :end_minute, presence: true
  validate :end_after_start

  scope :available, -> { where(available: true) }
  scope :unavailable, -> { where(available: false) }

  def to_h
    { start_minute: start_minute, end_minute: end_minute }
  end

  private

  def end_after_start
    return if start_minute.blank? || end_minute.blank?
    return if end_minute > start_minute

    errors.add(:end_minute, "must be after start_minute")
  end
end

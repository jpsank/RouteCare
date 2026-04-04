class CalendarBlock < ApplicationRecord
  belongs_to :user

  validates :source, presence: true, inclusion: { in: %w[external_calendar manual_block generated_visit] }
  validates :starts_at, :ends_at, presence: true
  validate :ends_after_starts

  scope :between, ->(start_time, end_time) { where("starts_at < ? AND ends_at > ?", end_time, start_time) }

  private

  def ends_after_starts
    return if starts_at.blank? || ends_at.blank?
    return if ends_at > starts_at

    errors.add(:ends_at, "must be after starts_at")
  end
end

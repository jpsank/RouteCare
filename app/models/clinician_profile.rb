class ClinicianProfile < ApplicationRecord
  DEFAULT_WORKING_DAY_WDAYS = [ 1, 2, 3, 4, 5 ].freeze
  DEFAULT_WORKING_DAYS_MASK = DEFAULT_WORKING_DAY_WDAYS.sum { |wday| (1 << wday) }.freeze

  belongs_to :user
  has_many :patients, dependent: :destroy

  validates :discipline, presence: true
  validates :timezone, presence: true
  validates :workday_start_minute, numericality: { greater_than_or_equal_to: 0, less_than_or_equal_to: 1439 }
  validates :workday_end_minute, numericality: { greater_than_or_equal_to: 1, less_than_or_equal_to: 1440 }
  validates :working_days_mask, numericality: { greater_than_or_equal_to: 1, less_than_or_equal_to: 127 }
  validates :lunch_start_minute, numericality: { greater_than_or_equal_to: 0, less_than_or_equal_to: 1439 }
  validates :lunch_duration_minutes, numericality: { greater_than_or_equal_to: 15, less_than_or_equal_to: 60 }
  validates :lunch_window_minutes, numericality: { greater_than_or_equal_to: 0, less_than_or_equal_to: 180 }
  validate :workday_end_after_start

  def home_point
    return if home_latitude.blank? || home_longitude.blank?

    { lat: home_latitude, lng: home_longitude }
  end

  def working_day_wdays
    days = (0..6).select { |wday| (working_days_mask & (1 << wday)).positive? }
    days.presence || DEFAULT_WORKING_DAY_WDAYS
  end

  def working_day_wdays=(wdays)
    normalized = Array(wdays).map(&:to_i).select { |wday| (0..6).cover?(wday) }.uniq
    self.working_days_mask =
      if normalized.empty?
        DEFAULT_WORKING_DAYS_MASK
      else
        normalized.sum { |wday| (1 << wday) }
      end
  end

  # Returns {earliest_start_minute, latest_start_minute, duration_minutes}
  # representing the flexible lunch window for the optimizer.
  def lunch_range
    half_window = lunch_window_minutes / 2
    earliest = [ lunch_start_minute - half_window, workday_start_minute ].max
    latest   = [ lunch_start_minute + half_window, workday_end_minute - lunch_duration_minutes ].min
    {
      earliest_start_minute: earliest,
      latest_start_minute:   latest,
      duration_minutes:      lunch_duration_minutes
    }
  end

  private

  def workday_end_after_start
    return if workday_start_minute.blank? || workday_end_minute.blank?
    return if workday_end_minute > workday_start_minute

    errors.add(:workday_end_minute, "must be after workday_start_minute")
  end
end

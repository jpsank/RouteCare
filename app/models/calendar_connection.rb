class CalendarConnection < ApplicationRecord
  belongs_to :user

  PROVIDERS = %w[google outlook].freeze
  STATUSES = %w[active disconnected expired].freeze

  validates :provider, inclusion: { in: PROVIDERS }
  validates :status, inclusion: { in: STATUSES }
  validates :external_calendar_id, presence: true
  validates :access_token, :refresh_token, presence: true
end

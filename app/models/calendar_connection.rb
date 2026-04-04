class CalendarConnection < ApplicationRecord
  belongs_to :user

  PROVIDERS = %w[google outlook apple].freeze
  STATUSES = %w[active disconnected expired].freeze

  encrypts :access_token, :refresh_token

  validates :provider, inclusion: { in: PROVIDERS }
  validates :status, inclusion: { in: STATUSES }
  validates :external_calendar_id, presence: true
  validates :access_token, :refresh_token, presence: true, unless: :apple?
  validates :metadata, presence: true, if: :apple?

  def apple?
    provider == "apple"
  end
end

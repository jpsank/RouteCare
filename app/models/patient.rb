class Patient < ApplicationRecord
  belongs_to :clinician_profile
  has_many :patient_availability_windows, dependent: :destroy
  has_many :visits, dependent: :restrict_with_error
  has_many :patient_messages, dependent: :nullify

  validates :full_name, :phone, :address_line1, :city, :state, :postal_code, presence: true
  validates :required_visits_per_week, numericality: { greater_than: 0, less_than_or_equal_to: 7 }
  validates :visit_duration_minutes, numericality: { greater_than_or_equal_to: 15, less_than_or_equal_to: 240 }
  validates :min_days_between_visits, numericality: { greater_than_or_equal_to: 1, less_than_or_equal_to: 6 }
  validates :max_days_between_visits, numericality: { greater_than_or_equal_to: 1, less_than_or_equal_to: 7 }
  validates :priority, numericality: { greater_than_or_equal_to: 0 }
  validates :email, format: { with: URI::MailTo::EMAIL_REGEXP }, allow_blank: true
  validates :latitude, :longitude, numericality: true, allow_nil: true
  validate :min_days_not_greater_than_max_days

  before_save :geocode_address, if: :address_changed?

  scope :active, -> { where(active: true) }

  def address
    [ address_line1, address_line2, city, state, postal_code ].compact_blank.join(", ")
  end

  def preferred_message_channel
    phone.present? ? :sms : :email
  end

  private

  def address_changed?
    address_line1_changed? || city_changed? || state_changed? || postal_code_changed?
  end

  def geocode_address
    result = Integrations::GeocodingClient.new.geocode(address)
    return unless result

    self.latitude = result[:lat]
    self.longitude = result[:lng]
  end

  def min_days_not_greater_than_max_days
    return if min_days_between_visits.blank? || max_days_between_visits.blank?
    return if min_days_between_visits <= max_days_between_visits

    errors.add(:min_days_between_visits, "must be less than or equal to max_days_between_visits")
  end
end

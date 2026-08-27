class Patient < ApplicationRecord
  belongs_to :clinician_profile
  has_many :patient_availability_windows, dependent: :destroy
  has_many :visits, dependent: :restrict_with_error
  has_many :patient_messages, dependent: :nullify

  validates :first_name, :last_name, :phone, :address_line1, :city, :state, :postal_code, presence: true
  validates :required_visits_per_week, numericality: { greater_than: 0, less_than_or_equal_to: 7 }
  validates :visit_duration_minutes, numericality: { greater_than_or_equal_to: 15, less_than_or_equal_to: 240 }
  validates :min_days_between_visits, numericality: { greater_than_or_equal_to: 0, less_than_or_equal_to: 6 }
  validates :max_days_between_visits, numericality: { greater_than_or_equal_to: 1, less_than_or_equal_to: 7 }
  validates :priority, numericality: { greater_than_or_equal_to: 0 }
  validates :email, format: { with: URI::MailTo::EMAIL_REGEXP }, allow_blank: true
  validates :latitude, :longitude, numericality: true, allow_nil: true
  validate :min_days_not_greater_than_max_days

  attr_accessor :skip_geocoding

  before_save :geocode_address, if: :should_geocode?

  scope :active, -> { where(active: true) }

  def address
    [ address_line1, address_line2, city, state, postal_code ].compact_blank.join(", ")
  end

  # Kept for backward compatibility with older callers (message templates,
  # PDF export, CSV import of a single combined "Name" column, etc.) that
  # only need a display string. The stored, queryable/sortable data lives in
  # first_name/last_name.
  def full_name
    [ first_name, last_name ].compact_blank.join(" ")
  end

  # Splits a combined name on assignment so anything still constructing a
  # Patient with `full_name:` (tests, the CSV importer, older API callers)
  # keeps working. Uses the same last-whitespace-token heuristic as the
  # first_name/last_name backfill migration.
  def full_name=(value)
    parts = value.to_s.strip.split(/\s+/)
    if parts.length > 1
      self.first_name = parts[0..-2].join(" ")
      self.last_name = parts[-1]
    else
      self.first_name = ""
      self.last_name = parts.first.to_s
    end
  end

  def preferred_message_channel
    phone.present? ? :sms : :email
  end

  # Blackout windows for a given day-of-week (0=Sunday..6=Saturday) — visits
  # must never be scheduled to overlap these, regardless of available windows.
  def unavailable_windows_for_wday(wday)
    patient_availability_windows.reject(&:available).select { |w| w.day_of_week == wday }.map(&:to_h)
  end

  private

  def should_geocode?
    return false if skip_geocoding
    return false if latitude.present? && longitude.present? && (coordinates_explicitly_set? || !address_changed?)
    address_changed?
  end

  def address_changed?
    address_line1_changed? || city_changed? || state_changed? || postal_code_changed?
  end

  # True when the caller explicitly assigned lat/lng in this save (create or
  # update) rather than them being carried over from a prior geocode. Guards
  # against clobbering deliberately-provided coordinates even when the
  # address is also changing in the same write.
  def coordinates_explicitly_set?
    latitude_changed? || longitude_changed?
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

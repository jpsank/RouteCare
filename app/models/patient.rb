class Patient < ApplicationRecord
  belongs_to :clinician_profile
  has_many :patient_availability_windows, dependent: :destroy
  has_many :visits, dependent: :restrict_with_error
  has_many :patient_messages, dependent: :nullify

  validates :full_name, :phone, :address_line1, :city, :state, :postal_code, presence: true
  validates :required_visits_per_week, numericality: { greater_than: 0, less_than_or_equal_to: 7 }
  validates :visit_duration_minutes, numericality: { greater_than_or_equal_to: 15, less_than_or_equal_to: 240 }
  validates :email, format: { with: URI::MailTo::EMAIL_REGEXP }, allow_blank: true
  validates :latitude, :longitude, numericality: true, allow_nil: true

  scope :active, -> { where(active: true) }

  def address
    [ address_line1, address_line2, city, state, postal_code ].compact_blank.join(", ")
  end
end

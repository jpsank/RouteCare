class ClinicianProfile < ApplicationRecord
  belongs_to :user
  has_many :patients, dependent: :destroy

  validates :discipline, presence: true
  validates :timezone, presence: true
end

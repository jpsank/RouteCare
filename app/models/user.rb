class User < ApplicationRecord
  # Include default devise modules. Others available are:
  # :confirmable, :lockable, :timeoutable, :trackable and :omniauthable
  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable

  has_one :clinician_profile, dependent: :destroy
  has_many :calendar_connections, dependent: :destroy
  has_many :calendar_blocks, dependent: :destroy
  has_many :weekly_schedules, dependent: :destroy
  has_many :alerts, dependent: :destroy
  has_many :audit_logs, dependent: :destroy
  has_many :patient_messages, dependent: :destroy
end

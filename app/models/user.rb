class User < ApplicationRecord
  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable,
         :timeoutable, :lockable,
         :omniauthable, omniauth_providers: %i[google_oauth2 github]

  def self.from_omniauth(auth)
    email = auth.info.email.to_s.strip.downcase
    return nil if email.blank? || !email.match?(Devise.email_regexp)

    where(provider: auth.provider, uid: auth.uid).first_or_create do |user|
      user.email = email
      user.password = Devise.friendly_token(24)
    end
  end

  has_one :clinician_profile, dependent: :destroy
  has_many :calendar_connections, dependent: :destroy
  has_many :calendar_blocks, dependent: :destroy
  has_many :weekly_schedules, dependent: :destroy
  has_many :alerts, dependent: :destroy
  has_many :audit_logs, dependent: :destroy
  has_many :patient_messages, dependent: :destroy
end

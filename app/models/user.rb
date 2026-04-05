class User < ApplicationRecord
  OMNIAUTH_PROVIDERS = [
    (:google_oauth2 if ENV["GOOGLE_OAUTH_CLIENT_ID"].present?),
    (:github if ENV["GITHUB_OAUTH_CLIENT_ID"].present?)
  ].compact.freeze

  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable,
         *(OMNIAUTH_PROVIDERS.any? ? [ :omniauthable, { omniauth_providers: OMNIAUTH_PROVIDERS } ] : [])

  def self.from_omniauth(auth)
    where(provider: auth.provider, uid: auth.uid).first_or_create do |user|
      user.email = auth.info.email
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

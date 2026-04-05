class User < ApplicationRecord
  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable,
         :timeoutable, :lockable,
         :omniauthable, omniauth_providers: %i[google_oauth2 github]

  def self.from_omniauth(auth)
    email = auth.info.email.to_s.strip.downcase
    return nil if email.blank? || !email.match?(Devise.email_regexp)

    # Find by OAuth identity, or link to existing account by email
    user = find_by(provider: auth.provider, uid: auth.uid)
    user ||= find_by(email: email)

    if user
      # Link OAuth identity to existing account
      user.update!(provider: auth.provider, uid: auth.uid) if user.provider.blank?
      user
    else
      create!(provider: auth.provider, uid: auth.uid, email: email, password: Devise.friendly_token(24))
    end
  rescue ActiveRecord::RecordInvalid
    nil
  end

  has_one :clinician_profile, dependent: :destroy
  has_many :calendar_connections, dependent: :destroy
  has_many :calendar_blocks, dependent: :destroy
  has_many :weekly_schedules, dependent: :destroy
  has_many :alerts, dependent: :destroy
  has_many :audit_logs, dependent: :destroy
  has_many :patient_messages, dependent: :destroy
end

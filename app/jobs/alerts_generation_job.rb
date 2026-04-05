class AlertsGenerationJob < ApplicationJob
  queue_as :default

  def perform
    User.joins(:clinician_profile).find_each do |user|
      Alerts::Generator.new(user: user).run!
    rescue StandardError => e
      Rails.logger.error("[AlertsGenerationJob] Failed for user #{user.id}: #{e.message}")
    end
  end
end

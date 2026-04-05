class CleanupResolvedAlertsJob < ApplicationJob
  queue_as :default

  RETENTION_DAYS = 30

  def perform
    cutoff = RETENTION_DAYS.days.ago
    count = Alert.where(status: "resolved").where("updated_at < ?", cutoff).delete_all
    Rails.logger.info("[CleanupResolvedAlertsJob] Deleted #{count} resolved alert(s) older than #{RETENTION_DAYS} days")
  end
end

class Alert < ApplicationRecord
  belongs_to :user

  enum :severity, {
    low: "low",
    medium: "medium",
    high: "high"
  }, prefix: true

  enum :status, {
    open: "open",
    acknowledged: "acknowledged",
    resolved: "resolved"
  }, prefix: true

  validates :category, :message, presence: true
end

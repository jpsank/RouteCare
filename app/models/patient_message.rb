class PatientMessage < ApplicationRecord
  belongs_to :visit
  belongs_to :patient
  belongs_to :user

  enum :direction, { outbound: "outbound", inbound: "inbound" }, prefix: true
  enum :channel, { sms: "sms", email: "email" }, prefix: true
  enum :status, {
    draft: "draft",
    pending_approval: "pending_approval",
    queued: "queued",
    sent: "sent",
    received: "received",
    failed: "failed"
  }, prefix: true

  validates :body, presence: true
  validates :status, :direction, :channel, presence: true
end

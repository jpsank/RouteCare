class AuditLog < ApplicationRecord
  belongs_to :user

  validates :auditable_type, :auditable_id, :action, presence: true
end

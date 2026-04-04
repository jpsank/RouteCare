class WeeklySchedule < ApplicationRecord
  belongs_to :user
  has_many :visits, dependent: :destroy

  enum :status, {
    draft: "draft",
    optimized: "optimized",
    approved: "approved",
    archived: "archived"
  }, validate: true

  validates :week_start_on, presence: true
  validates :total_drive_minutes, numericality: { greater_than_or_equal_to: 0 }
  validates :baseline_drive_minutes, numericality: { greater_than_or_equal_to: 0 }
  validates :week_start_on, uniqueness: { scope: :user_id }
end

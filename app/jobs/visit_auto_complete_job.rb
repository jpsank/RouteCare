class VisitAutoCompleteJob < ApplicationJob
  queue_as :default

  def perform
    Visit.where(status: :confirmed)
         .where("ends_at < ?", Time.current)
         .update_all(status: "completed")
  end
end

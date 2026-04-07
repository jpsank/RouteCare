# frozen_string_literal: true

class AddInstanceIdToVisits < ActiveRecord::Migration[8.1]
  def change
    add_column :visits, :instance_id, :string
    add_index :visits, [ :weekly_schedule_id, :instance_id ],
      unique: true,
      where: "instance_id IS NOT NULL",
      name: "index_visits_on_schedule_and_instance_id_unique"
  end
end

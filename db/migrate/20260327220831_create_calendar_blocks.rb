class CreateCalendarBlocks < ActiveRecord::Migration[8.1]
  def change
    create_table :calendar_blocks do |t|
      t.references :user, null: false, foreign_key: true
      t.string :source, null: false, default: "external_calendar"
      t.string :external_event_id
      t.string :title
      t.datetime :starts_at, null: false
      t.datetime :ends_at, null: false
      t.jsonb :metadata, null: false, default: {}

      t.timestamps
    end

    add_index :calendar_blocks, %i[user_id starts_at ends_at]
    add_index :calendar_blocks, %i[user_id source external_event_id], unique: true
  end
end

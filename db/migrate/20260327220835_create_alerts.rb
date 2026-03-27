class CreateAlerts < ActiveRecord::Migration[8.1]
  def change
    create_table :alerts do |t|
      t.references :user, null: false, foreign_key: true
      t.string :category, null: false
      t.string :severity, null: false, default: "medium"
      t.string :status, null: false, default: "open"
      t.text :message, null: false
      t.datetime :due_at
      t.datetime :read_at
      t.jsonb :metadata, null: false, default: {}

      t.timestamps
    end

    add_index :alerts, %i[user_id status due_at]
    add_index :alerts, :category
  end
end

class CreatePatientMessages < ActiveRecord::Migration[8.1]
  def change
    create_table :patient_messages do |t|
      t.references :visit, foreign_key: true
      t.references :patient, null: false, foreign_key: true
      t.references :user, null: false, foreign_key: true
      t.string :direction, null: false
      t.string :channel, null: false
      t.string :status, null: false, default: "draft"
      t.text :body, null: false
      t.datetime :proposed_starts_at
      t.datetime :proposed_ends_at
      t.boolean :requires_approval, null: false, default: true
      t.datetime :approved_at
      t.jsonb :metadata, null: false, default: {}

      t.timestamps
    end

    add_index :patient_messages, :status
    add_index :patient_messages, :channel
    add_index :patient_messages, [ :patient_id, :created_at ]
  end
end

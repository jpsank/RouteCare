class CreateClinicianProfiles < ActiveRecord::Migration[8.1]
  def change
    create_table :clinician_profiles do |t|
      t.references :user, null: false, foreign_key: true
      t.string :discipline, null: false
      t.string :phone
      t.string :timezone, null: false, default: "America/New_York"
      t.boolean :auto_send_enabled, null: false, default: false

      t.timestamps
    end

    add_index :clinician_profiles, :phone
  end
end

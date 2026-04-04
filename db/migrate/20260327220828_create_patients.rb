class CreatePatients < ActiveRecord::Migration[8.1]
  def change
    create_table :patients do |t|
      t.references :clinician_profile, null: false, foreign_key: true
      t.string :full_name, null: false
      t.string :phone, null: false
      t.string :email
      t.string :address_line1, null: false
      t.string :address_line2
      t.string :city, null: false
      t.string :state, null: false
      t.string :postal_code, null: false
      t.decimal :latitude, precision: 10, scale: 6
      t.decimal :longitude, precision: 10, scale: 6
      t.integer :required_visits_per_week, null: false, default: 1
      t.integer :visit_duration_minutes, null: false, default: 60
      t.boolean :active, null: false, default: true
      t.text :notes

      t.timestamps
    end

    add_index :patients, [ :clinician_profile_id, :active ]
    add_check_constraint :patients, "required_visits_per_week > 0", name: "patients_required_visits_positive"
    add_check_constraint :patients, "visit_duration_minutes > 0", name: "patients_duration_positive"
  end
end

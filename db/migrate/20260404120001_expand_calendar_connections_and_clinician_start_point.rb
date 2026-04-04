class ExpandCalendarConnectionsAndClinicianStartPoint < ActiveRecord::Migration[8.1]
  def change
    add_column :clinician_profiles, :home_address_line1, :string
    add_column :clinician_profiles, :home_address_line2, :string
    add_column :clinician_profiles, :home_city, :string
    add_column :clinician_profiles, :home_state, :string
    add_column :clinician_profiles, :home_postal_code, :string
    add_column :clinician_profiles, :home_latitude, :decimal, precision: 10, scale: 6
    add_column :clinician_profiles, :home_longitude, :decimal, precision: 10, scale: 6

    remove_check_constraint :calendar_connections, name: "calendar_connections_provider_check"
    add_check_constraint :calendar_connections, "provider IN ('google', 'outlook', 'apple')", name: "calendar_connections_provider_check"
  end
end

class CreateCalendarConnections < ActiveRecord::Migration[8.1]
  def change
    create_table :calendar_connections do |t|
      t.references :user, null: false, foreign_key: true
      t.string :provider, null: false
      t.string :external_calendar_id, null: false
      t.string :status, null: false, default: "active"
      t.text :access_token, null: false
      t.text :refresh_token, null: false
      t.datetime :token_expires_at
      t.jsonb :metadata, null: false, default: {}

      t.timestamps
    end

    add_index :calendar_connections, [ :user_id, :provider ], unique: true
    add_index :calendar_connections, [ :provider, :external_calendar_id ], unique: true
    add_check_constraint :calendar_connections, "provider IN ('google', 'outlook')", name: "calendar_connections_provider_check"
    add_check_constraint :calendar_connections, "status IN ('active', 'disconnected', 'expired')", name: "calendar_connections_status_check"
    add_check_constraint :calendar_connections, "char_length(access_token) > 0", name: "calendar_connections_access_token_presence"
    add_check_constraint :calendar_connections, "char_length(refresh_token) > 0", name: "calendar_connections_refresh_token_presence"
  end
end

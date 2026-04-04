# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# This file is the source Rails uses to define your schema when running `bin/rails
# db:schema:load`. When creating a new database, `bin/rails db:schema:load` tends to
# be faster and is potentially less error prone than running all of your
# migrations from scratch. Old migrations may fail to apply correctly if those
# migrations use external dependencies or application code.
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema[8.1].define(version: 2026_04_04_180001) do
  # These are extensions that must be enabled in order to support this database
  enable_extension "pg_catalog.plpgsql"

  create_table "alerts", force: :cascade do |t|
    t.string "category", null: false
    t.datetime "created_at", null: false
    t.datetime "due_at"
    t.text "message", null: false
    t.jsonb "metadata", default: {}, null: false
    t.datetime "read_at"
    t.string "severity", default: "medium", null: false
    t.string "status", default: "open", null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.index ["category"], name: "index_alerts_on_category"
    t.index ["user_id", "status", "due_at"], name: "index_alerts_on_user_id_and_status_and_due_at"
    t.index ["user_id"], name: "index_alerts_on_user_id"
  end

  create_table "audit_logs", force: :cascade do |t|
    t.string "action", null: false
    t.bigint "auditable_id", null: false
    t.string "auditable_type", null: false
    t.datetime "created_at", null: false
    t.string "ip_address"
    t.jsonb "metadata", default: {}, null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.index ["action"], name: "index_audit_logs_on_action"
    t.index ["auditable_type", "auditable_id"], name: "index_audit_logs_on_auditable_type_and_auditable_id"
    t.index ["user_id"], name: "index_audit_logs_on_user_id"
  end

  create_table "calendar_blocks", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.datetime "ends_at", null: false
    t.string "external_event_id"
    t.jsonb "metadata", default: {}, null: false
    t.string "source", default: "external_calendar", null: false
    t.datetime "starts_at", null: false
    t.string "title"
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.index ["user_id", "source", "external_event_id"], name: "idx_on_user_id_source_external_event_id_c469e9ded3", unique: true
    t.index ["user_id", "starts_at", "ends_at"], name: "index_calendar_blocks_on_user_id_and_starts_at_and_ends_at"
    t.index ["user_id"], name: "index_calendar_blocks_on_user_id"
  end

  create_table "calendar_connections", force: :cascade do |t|
    t.text "access_token", null: false
    t.datetime "created_at", null: false
    t.string "external_calendar_id", null: false
    t.jsonb "metadata", default: {}, null: false
    t.string "provider", null: false
    t.text "refresh_token", null: false
    t.string "status", default: "active", null: false
    t.datetime "token_expires_at"
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.index ["provider", "external_calendar_id"], name: "idx_on_provider_external_calendar_id_694277f116", unique: true
    t.index ["user_id", "provider"], name: "index_calendar_connections_on_user_id_and_provider", unique: true
    t.index ["user_id"], name: "index_calendar_connections_on_user_id"
    t.check_constraint "char_length(access_token) > 0", name: "calendar_connections_access_token_presence"
    t.check_constraint "char_length(refresh_token) > 0", name: "calendar_connections_refresh_token_presence"
    t.check_constraint "provider::text = ANY (ARRAY['google'::character varying, 'outlook'::character varying, 'apple'::character varying]::text[])", name: "calendar_connections_provider_check"
    t.check_constraint "status::text = ANY (ARRAY['active'::character varying::text, 'disconnected'::character varying::text, 'expired'::character varying::text])", name: "calendar_connections_status_check"
  end

  create_table "clinician_profiles", force: :cascade do |t|
    t.boolean "auto_send_enabled", default: false, null: false
    t.datetime "created_at", null: false
    t.string "discipline", null: false
    t.string "home_address_line1"
    t.string "home_address_line2"
    t.string "home_city"
    t.decimal "home_latitude", precision: 10, scale: 6
    t.decimal "home_longitude", precision: 10, scale: 6
    t.string "home_postal_code"
    t.string "home_state"
    t.integer "lunch_duration_minutes", default: 30, null: false
    t.integer "lunch_start_minute", default: 720, null: false
    t.integer "lunch_window_minutes", default: 90, null: false
    t.string "phone"
    t.string "timezone", default: "America/New_York", null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.integer "workday_end_minute", default: 1080, null: false
    t.integer "workday_start_minute", default: 480, null: false
    t.integer "working_days_mask", default: 62, null: false
    t.index ["phone"], name: "index_clinician_profiles_on_phone"
    t.index ["user_id"], name: "index_clinician_profiles_on_user_id"
    t.check_constraint "workday_end_minute > workday_start_minute", name: "clinician_profiles_workday_end_after_start"
    t.check_constraint "workday_end_minute >= 1 AND workday_end_minute <= 1440", name: "clinician_profiles_valid_workday_end"
    t.check_constraint "workday_start_minute >= 0 AND workday_start_minute <= 1439", name: "clinician_profiles_valid_workday_start"
    t.check_constraint "working_days_mask >= 1 AND working_days_mask <= 127", name: "clinician_profiles_valid_working_days_mask"
  end

  create_table "patient_availability_windows", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.integer "day_of_week", null: false
    t.integer "end_minute", null: false
    t.bigint "patient_id", null: false
    t.integer "start_minute", null: false
    t.datetime "updated_at", null: false
    t.index ["patient_id", "day_of_week", "start_minute", "end_minute"], name: "idx_patient_windows_uniqueness", unique: true
    t.index ["patient_id"], name: "index_patient_availability_windows_on_patient_id"
    t.check_constraint "day_of_week >= 0 AND day_of_week <= 6", name: "patient_windows_valid_day"
    t.check_constraint "end_minute <= 1440", name: "patient_windows_end_within_day"
    t.check_constraint "end_minute > start_minute", name: "patient_windows_end_after_start"
    t.check_constraint "start_minute >= 0", name: "patient_windows_start_non_negative"
  end

  create_table "patient_messages", force: :cascade do |t|
    t.datetime "approved_at"
    t.text "body", null: false
    t.string "channel", null: false
    t.datetime "created_at", null: false
    t.string "direction", null: false
    t.jsonb "metadata", default: {}, null: false
    t.bigint "patient_id", null: false
    t.datetime "proposed_ends_at"
    t.datetime "proposed_starts_at"
    t.boolean "requires_approval", default: true, null: false
    t.string "status", default: "draft", null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.bigint "visit_id"
    t.index ["channel"], name: "index_patient_messages_on_channel"
    t.index ["patient_id", "created_at"], name: "index_patient_messages_on_patient_id_and_created_at"
    t.index ["patient_id"], name: "index_patient_messages_on_patient_id"
    t.index ["status"], name: "index_patient_messages_on_status"
    t.index ["user_id"], name: "index_patient_messages_on_user_id"
    t.index ["visit_id"], name: "index_patient_messages_on_visit_id"
  end

  create_table "patients", force: :cascade do |t|
    t.boolean "active", default: true, null: false
    t.string "address_line1", null: false
    t.string "address_line2"
    t.string "city", null: false
    t.bigint "clinician_profile_id", null: false
    t.datetime "created_at", null: false
    t.string "email"
    t.string "full_name", null: false
    t.decimal "latitude", precision: 10, scale: 6
    t.decimal "longitude", precision: 10, scale: 6
    t.text "notes"
    t.string "phone", null: false
    t.string "postal_code", null: false
    t.integer "required_visits_per_week", default: 1, null: false
    t.string "state", null: false
    t.datetime "updated_at", null: false
    t.integer "visit_duration_minutes", default: 60, null: false
    t.index ["clinician_profile_id", "active"], name: "index_patients_on_clinician_profile_id_and_active"
    t.index ["clinician_profile_id"], name: "index_patients_on_clinician_profile_id"
    t.check_constraint "required_visits_per_week > 0", name: "patients_required_visits_positive"
    t.check_constraint "visit_duration_minutes > 0", name: "patients_duration_positive"
  end

  create_table "users", force: :cascade do |t|
    t.datetime "created_at", null: false
    t.string "email", default: "", null: false
    t.string "encrypted_password", default: "", null: false
    t.datetime "remember_created_at"
    t.datetime "reset_password_sent_at"
    t.string "reset_password_token"
    t.datetime "updated_at", null: false
    t.index ["email"], name: "index_users_on_email", unique: true
    t.index ["reset_password_token"], name: "index_users_on_reset_password_token", unique: true
  end

  create_table "visits", force: :cascade do |t|
    t.boolean "clinician_override", default: false, null: false
    t.datetime "created_at", null: false
    t.integer "drive_from_previous_minutes", default: 0, null: false
    t.integer "duration_minutes", null: false
    t.datetime "ends_at", null: false
    t.string "external_calendar_event_id"
    t.bigint "patient_id", null: false
    t.integer "position_in_day", null: false
    t.boolean "soft_constraint_override", default: false, null: false
    t.string "source", default: "optimizer", null: false
    t.datetime "starts_at", null: false
    t.string "status", default: "pending_patient_confirmation", null: false
    t.datetime "updated_at", null: false
    t.bigint "weekly_schedule_id", null: false
    t.index ["patient_id", "starts_at"], name: "index_visits_on_patient_id_and_starts_at"
    t.index ["patient_id"], name: "index_visits_on_patient_id"
    t.index ["weekly_schedule_id", "starts_at", "patient_id"], name: "idx_on_weekly_schedule_id_starts_at_patient_id_b8c791696f", unique: true
    t.index ["weekly_schedule_id", "starts_at"], name: "index_visits_on_weekly_schedule_id_and_starts_at"
    t.index ["weekly_schedule_id"], name: "index_visits_on_weekly_schedule_id"
    t.check_constraint "drive_from_previous_minutes >= 0", name: "visits_non_negative_drive"
    t.check_constraint "duration_minutes > 0", name: "visits_positive_duration"
    t.check_constraint "ends_at > starts_at", name: "visits_end_after_start"
    t.check_constraint "position_in_day >= 0", name: "visits_non_negative_position"
  end

  create_table "weekly_schedules", force: :cascade do |t|
    t.integer "baseline_drive_minutes", default: 0, null: false
    t.datetime "created_at", null: false
    t.jsonb "optimization_summary", default: {}, null: false
    t.string "status", default: "draft", null: false
    t.integer "total_drive_minutes", default: 0, null: false
    t.datetime "updated_at", null: false
    t.bigint "user_id", null: false
    t.date "week_start_on", null: false
    t.index ["user_id", "week_start_on"], name: "index_weekly_schedules_on_user_id_and_week_start_on", unique: true
    t.index ["user_id"], name: "index_weekly_schedules_on_user_id"
    t.check_constraint "baseline_drive_minutes >= 0", name: "weekly_schedules_non_negative_baseline_drive"
    t.check_constraint "status::text = ANY (ARRAY['draft'::character varying::text, 'optimized'::character varying::text, 'approved'::character varying::text, 'archived'::character varying::text])", name: "weekly_schedules_status_check"
    t.check_constraint "total_drive_minutes >= 0", name: "weekly_schedules_non_negative_total_drive"
  end

  add_foreign_key "alerts", "users"
  add_foreign_key "audit_logs", "users"
  add_foreign_key "calendar_blocks", "users"
  add_foreign_key "calendar_connections", "users"
  add_foreign_key "clinician_profiles", "users"
  add_foreign_key "patient_availability_windows", "patients"
  add_foreign_key "patient_messages", "patients"
  add_foreign_key "patient_messages", "users"
  add_foreign_key "patient_messages", "visits"
  add_foreign_key "patients", "clinician_profiles"
  add_foreign_key "visits", "patients"
  add_foreign_key "visits", "weekly_schedules"
  add_foreign_key "weekly_schedules", "users"
end

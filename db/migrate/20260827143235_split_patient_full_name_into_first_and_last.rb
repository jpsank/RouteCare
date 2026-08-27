class SplitPatientFullNameIntoFirstAndLast < ActiveRecord::Migration[8.1]
  # Scoped to this migration so it keeps working even if app/models/patient.rb
  # changes shape later.
  class MigrationPatient < ActiveRecord::Base
    self.table_name = "patients"
  end

  def up
    add_column :patients, :first_name, :string
    add_column :patients, :last_name, :string

    backfill_names

    change_column_null :patients, :first_name, false, ""
    change_column_null :patients, :last_name, false, ""
    remove_column :patients, :full_name
  end

  def down
    add_column :patients, :full_name, :string

    MigrationPatient.reset_column_information
    MigrationPatient.find_each do |patient|
      combined = [ patient.first_name, patient.last_name ].reject(&:blank?).join(" ")
      MigrationPatient.where(id: patient.id).update_all(full_name: combined)
    end

    change_column_null :patients, :full_name, false, ""
    remove_column :patients, :first_name
    remove_column :patients, :last_name
  end

  private

  # Best-effort backfill: splits each existing full_name on the LAST
  # whitespace-separated token, treating that token as last_name and
  # everything before it as first_name. This is a heuristic, not a general
  # name-parser:
  #   - Multi-word last names ("Maria Van Der Berg") mis-split: first_name
  #     becomes "Maria Van Der", last_name becomes "Berg".
  #   - Suffixes ("John Smith Jr.") are treated as the last name token.
  #   - Single-word names ("Madonna", "Cher") end up entirely in last_name
  #     with first_name left blank.
  # This is judged an acceptable one-time migration trade-off given the
  # existing data is a single free-text column with no structure to rely on.
  # Any resulting records with an unexpected split should be reviewed/fixed
  # manually after this migration runs.
  def backfill_names
    MigrationPatient.reset_column_information
    MigrationPatient.find_each do |patient|
      parts = patient.full_name.to_s.strip.split(/\s+/)
      first_name, last_name =
        if parts.length > 1
          [ parts[0..-2].join(" "), parts[-1] ]
        else
          [ "", parts.first.to_s ]
        end

      MigrationPatient.where(id: patient.id).update_all(first_name: first_name, last_name: last_name)
    end
  end
end

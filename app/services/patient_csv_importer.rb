require "csv"
require "roo"
require "tempfile"

class PatientCsvImporter
  MAX_ROWS = 500

  REQUIRED_FIELDS = %w[full_name phone address_line1 city state postal_code].freeze

  # Canonical field => list of normalized aliases (lowercased, alphanumeric only).
  # Matching is normalized both ways so "Full Name", "full-name", "FULLNAME" all hit the same bucket.
  HEADER_ALIASES = {
    "full_name" => %w[fullname name patientname patient client clientname],
    "phone" => %w[phone phonenumber mobile mobilenumber cell cellphone tel telephone contactnumber],
    "email" => %w[email emailaddress mail],
    "address_line1" => %w[address addressline1 address1 street streetaddress line1 addr],
    "address_line2" => %w[addressline2 address2 line2 apt apartment unit suite suitenumber],
    "city" => %w[city town locality],
    "state" => %w[state province region stateprovince],
    "postal_code" => %w[postalcode postcode zip zipcode postal postalzip],
    "required_visits_per_week" => %w[visitsperweek requiredvisitsperweek frequency visitfrequency weeklyvisits visitsweek],
    "visit_duration_minutes" => %w[visitduration duration visitlength durationminutes sessionlength],
    "min_days_between_visits" => %w[mindaysbetweenvisits mindaysbetween mindays mingap],
    "max_days_between_visits" => %w[maxdaysbetweenvisits maxdaysbetween maxdays maxgap],
    "priority" => %w[priority importance urgency],
    "notes" => %w[notes note comment comments memo remarks]
  }.freeze

  INTEGER_FIELDS = %w[
    required_visits_per_week visit_duration_minutes
    min_days_between_visits max_days_between_visits priority
  ].freeze

  STRING_FIELDS = (HEADER_ALIASES.keys - INTEGER_FIELDS).freeze

  INTEGER_DEFAULTS = {
    "required_visits_per_week" => 2,
    "visit_duration_minutes" => 60,
    "min_days_between_visits" => 1,
    "max_days_between_visits" => 7,
    "priority" => 0
  }.freeze

  Result = Struct.new(:imported, :errors, :patients, :header_map, keyword_init: true)

  def self.call(profile:, content:, filename: nil)
    new(profile: profile, content: content, filename: filename).call
  end

  def initialize(profile:, content:, filename: nil)
    @profile = profile
    @content = content.to_s
    @filename = filename.to_s
  end

  def call
    raise ArgumentError, "Empty file" if @content.bytesize.zero?

    table = excel? ? parse_excel : parse_csv
    raise ArgumentError, "File is empty" if table.headers.blank?
    raise ArgumentError, "Too many rows (max #{MAX_ROWS})" if table.size > MAX_ROWS

    header_map = build_header_map(table.headers)
    missing = REQUIRED_FIELDS - header_map.values.uniq
    if missing.any?
      raise ArgumentError, "Missing required column(s): #{missing.join(', ')}"
    end

    imported, errors = import_rows(table, header_map)
    Result.new(
      imported: imported.size,
      errors: errors,
      patients: @profile.patients.active.includes(:patient_availability_windows).to_a,
      header_map: header_map
    )
  end

  private

  def parse_csv
    CSV.parse(@content, headers: true)
  rescue CSV::MalformedCSVError => e
    raise ArgumentError, "Invalid CSV: #{e.message}"
  end

  def excel?
    @filename.downcase.end_with?(".xlsx", ".xls")
  end

  def parse_excel
    ext = File.extname(@filename).downcase.delete(".").to_sym
    Tempfile.create([ "patient_import", ".#{ext}" ]) do |tmp|
      tmp.binmode
      tmp.write(@content)
      tmp.flush
      book = Roo::Spreadsheet.open(tmp.path, extension: ext)
      sheet = book.sheet(0)
      csv_string = (1..sheet.last_row.to_i).map do |r|
        CSV.generate_line((1..sheet.last_column.to_i).map { |c| stringify_cell(sheet.cell(r, c)) })
      end.join
      CSV.parse(csv_string, headers: true)
    end
  rescue ArgumentError
    raise
  rescue StandardError => e
    raise ArgumentError, "Invalid spreadsheet: #{e.message}"
  end

  def stringify_cell(value)
    case value
    when nil then ""
    when Float then value == value.to_i ? value.to_i.to_s : value.to_s
    when Date, DateTime, Time then value.to_s
    else value.to_s
    end
  end

  # Returns { original_header => canonical_field }. Only headers that map to a
  # known canonical field are included.
  def build_header_map(headers)
    alias_lookup = build_alias_lookup
    headers.each_with_object({}) do |header, acc|
      next if header.blank?

      canonical = resolve_header(header, alias_lookup)
      acc[header] = canonical if canonical
    end
  end

  def build_alias_lookup
    HEADER_ALIASES.each_with_object({}) do |(canonical, aliases), acc|
      acc[normalize(canonical)] = canonical
      aliases.each { |a| acc[normalize(a)] = canonical }
    end
  end

  def resolve_header(header, alias_lookup)
    normalized = normalize(header)
    return alias_lookup[normalized] if alias_lookup.key?(normalized)

    # Soft contains match — "patient full name" → "fullname"
    best = alias_lookup.find { |key, _| normalized.include?(key) && key.length >= 4 }
    best&.last
  end

  def normalize(value)
    value.to_s.downcase.gsub(/[^a-z0-9]/, "")
  end

  def import_rows(table, header_map)
    imported = []
    errors = []

    ActiveRecord::Base.transaction do
      table.each_with_index do |row, idx|
        row_num = idx + 2  # account for header row
        attrs = extract_attrs(row, header_map)
        next if attrs["full_name"].blank?

        patient = @profile.patients.new(attrs.slice(*STRING_FIELDS))
        patient.skip_geocoding = true
        assign_integer_fields!(patient, attrs)

        if patient.save
          imported << patient
        else
          errors << "Row #{row_num}: #{patient.errors.full_messages.join('; ')}"
        end
      rescue ArgumentError => e
        errors << "Row #{row_num}: #{e.message}"
      end

      raise ActiveRecord::Rollback if errors.any? && imported.empty?
    end

    [ imported, errors ]
  end

  def extract_attrs(row, header_map)
    attrs = {}
    header_map.each do |original_header, canonical|
      value = row[original_header]
      attrs[canonical] = value.to_s.strip if value
    end
    attrs
  end

  def assign_integer_fields!(patient, attrs)
    INTEGER_FIELDS.each do |field|
      raw = attrs[field]
      patient.public_send("#{field}=", raw.present? ? Integer(raw) : INTEGER_DEFAULTS[field])
    end
  end
end

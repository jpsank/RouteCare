require "prawn"
require "prawn/table"

class SchedulePdfRenderer
  HEADER_COLOR = "4F46E5"
  ROW_HEADER_COLOR = "F3F4F6"

  def initialize(schedule:, clinician_profile: nil, timezone: Time.zone.name)
    @schedule = schedule
    @clinician_profile = clinician_profile
    @timezone = ActiveSupport::TimeZone[timezone] || Time.zone
  end

  def render
    Prawn::Document.new(page_size: "LETTER", margin: 40) do |pdf|
      build_header(pdf)
      build_summary(pdf)
      pdf.move_down 12
      build_days(pdf)
      build_footer(pdf)
    end.render
  end

  private

  def build_header(pdf)
    pdf.fill_color HEADER_COLOR
    pdf.font("Helvetica", style: :bold) do
      pdf.text "RouteCare Weekly Schedule", size: 18
    end
    pdf.fill_color "000000"
    pdf.font("Helvetica") do
      pdf.text "Week of #{@schedule.week_start_on.strftime('%B %-d, %Y')}", size: 11
      if @clinician_profile&.display_name.present?
        pdf.text "Clinician: #{@clinician_profile.display_name}", size: 10
      end
    end
    pdf.stroke_horizontal_rule
    pdf.move_down 8
  end

  def build_summary(pdf)
    saved = [ @schedule.baseline_drive_minutes - @schedule.total_drive_minutes, 0 ].max
    rows = [
      [ "Total drive", "#{@schedule.total_drive_minutes} min" ],
      [ "Baseline drive", "#{@schedule.baseline_drive_minutes} min" ],
      [ "Saved", "#{saved} min" ],
      [ "Visits", visits_by_day.values.flatten.size.to_s ],
      [ "Status", @schedule.status.to_s.humanize ]
    ]
    pdf.table(rows, cell_style: { size: 9, borders: [], padding: [ 2, 6 ] }, column_widths: { 0 => 110 })
  end

  def build_days(pdf)
    visits_by_day.each do |date, day_visits|
      pdf.move_down 10
      pdf.font("Helvetica", style: :bold) do
        pdf.fill_color HEADER_COLOR
        pdf.text date.strftime("%A, %B %-d"), size: 12
        pdf.fill_color "000000"
      end
      pdf.move_down 4

      if day_visits.empty?
        pdf.font("Helvetica", style: :italic) { pdf.text "No visits scheduled", size: 9, color: "9CA3AF" }
        next
      end

      rows = [ [ "Time", "Patient", "Address", "Phone", "Drive" ] ]
      day_visits.each do |visit|
        patient = visit.patient
        rows << [
          format_time(visit.starts_at),
          patient.full_name.to_s,
          patient.address.to_s,
          patient.phone.to_s,
          "#{visit.drive_from_previous_minutes.to_i} min"
        ]
      end

      pdf.table(
        rows,
        header: true,
        width: pdf.bounds.width,
        cell_style: { size: 8, padding: [ 4, 6 ], borders: [ :bottom ], border_color: "E5E7EB" },
        column_widths: { 0 => 60, 3 => 80, 4 => 50 }
      ) do |t|
        t.row(0).background_color = ROW_HEADER_COLOR
        t.row(0).font_style = :bold
      end
    end
  end

  def build_footer(pdf)
    pdf.move_down 16
    pdf.stroke_horizontal_rule
    pdf.move_down 4
    pdf.font("Helvetica") do
      pdf.text "Generated #{Time.current.in_time_zone(@timezone).strftime('%Y-%m-%d %H:%M %Z')}", size: 8, color: "9CA3AF"
    end
  end

  def visits_by_day
    @visits_by_day ||= begin
      grouped = @schedule.visits.includes(:patient).order(:starts_at).group_by do |v|
        v.starts_at.in_time_zone(@timezone).to_date
      end
      (0..6).each_with_object({}) do |offset, acc|
        date = @schedule.week_start_on + offset
        acc[date] = grouped[date] || []
      end
    end
  end

  def format_time(time)
    time.in_time_zone(@timezone).strftime("%-l:%M %p")
  end
end

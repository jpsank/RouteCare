module Integrations
  module Calendar
    class GoogleCalendarClient < Integrations::BaseClient
      def blocked_events(_connection:, range_start:, range_end:)
        []
      end

      def upsert_visit_event(_connection:, visit:)
        "google_visit_#{visit.id}"
      end
    end
  end
end

module Integrations
  module Calendar
    class OutlookCalendarClient < Integrations::BaseClient
      def blocked_events(_connection:, range_start:, range_end:)
        _range = range_start..range_end
        []
      end

      def upsert_visit_event(_connection:, visit:)
        "outlook_visit_#{visit.id}"
      end
    end
  end
end

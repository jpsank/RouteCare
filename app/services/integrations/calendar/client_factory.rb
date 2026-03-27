module Integrations
  module Calendar
    class ClientFactory
      PROVIDER_MAP = {
        "google" => GoogleCalendarClient,
        "outlook" => OutlookCalendarClient
      }.freeze

      def self.build(connection)
        klass = PROVIDER_MAP.fetch(connection.provider) do
          raise ArgumentError, "Unsupported provider: #{connection.provider}"
        end
        klass.new(api_key: connection.access_token)
      end
    end
  end
end

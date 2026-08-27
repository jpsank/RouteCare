ENV["RAILS_ENV"] ||= "test"
require_relative "../config/environment"
require "rails/test_help"
require "webmock/minitest"
require_relative "support/maps_api_stubs"

# Block real outbound HTTP in tests. localhost is allowed for Capybara/Selenium's
# webdriver connections and system tests hitting the local Rails server.
WebMock.disable_net_connect!(allow_localhost: true)

module ActiveSupport
  class TestCase
    # Run tests in parallel with specified workers
    parallelize(workers: :number_of_processors)

    setup do
      MapsApiStubs.install!
    end

    # Add more helper methods to be used by all tests here...
  end
end

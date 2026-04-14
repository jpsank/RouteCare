ENV["RAILS_ENV"] ||= "test"
require_relative "../config/environment"
require "rails/test_help"

module ActiveSupport
  class TestCase
    # `Etc.nprocessors` reports 2 inside GitHub runner containers even though
    # ubuntu-latest has 4 vCPUs, so force a floor of 4 workers in CI.
    parallelize(workers: [ Etc.nprocessors, 4 ].max)

    # Add more helper methods to be used by all tests here...
  end
end

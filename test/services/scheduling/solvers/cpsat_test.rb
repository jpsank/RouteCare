require "test_helper"

module Scheduling
  module Solvers
    class CpsatTest < ActiveSupport::TestCase
      def build_solver
        Cpsat.allocate # skip the real initializer; we only need the private serialize_matrix method
      end

      test "serialize_matrix handles a BucketedTravelMatrix" do
        matrix = Scheduling::BucketedTravelMatrix.new(
          Scheduling::TrafficBuckets::OFF_PEAK[:name] => { "1" => { "2" => 10 } }
        )

        result = build_solver.send(:serialize_matrix, matrix)

        assert_equal({ Scheduling::TrafficBuckets::OFF_PEAK[:name].to_s => { "1" => { "2" => 10 } } }, result)
      end

      test "serialize_matrix accepts a legacy flat matrix without raising" do
        flat_matrix = { "1" => { "2" => 10 }, "2" => { "1" => 10 } }

        result = build_solver.send(:serialize_matrix, flat_matrix)

        assert_equal(
          { Scheduling::TrafficBuckets::OFF_PEAK[:name].to_s => { "1" => { "2" => 10 }, "2" => { "1" => 10 } } },
          result
        )
      end
    end
  end
end

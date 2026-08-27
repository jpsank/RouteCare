require "test_helper"

class Scheduling::TimeWindowTest < ActiveSupport::TestCase
  test "subtract returns windows unchanged when there are no exclusions" do
    windows = [ Scheduling::TimeWindow.new(480, 600) ]
    result = Scheduling::TimeWindow.subtract(windows, [])
    assert_equal windows, result
  end

  test "subtract splits a window around a fully-contained exclusion" do
    windows = [ Scheduling::TimeWindow.new(480, 600) ]
    exclusions = [ { start_minute: 500, end_minute: 520 } ]
    result = Scheduling::TimeWindow.subtract(windows, exclusions)

    assert_equal 2, result.size
    assert_equal [ 480, 500 ], [ result[0].start_minute, result[0].end_minute ]
    assert_equal [ 520, 600 ], [ result[1].start_minute, result[1].end_minute ]
  end

  test "subtract drops the window entirely when the exclusion covers it" do
    windows = [ Scheduling::TimeWindow.new(480, 600) ]
    exclusions = [ { start_minute: 400, end_minute: 700 } ]
    result = Scheduling::TimeWindow.subtract(windows, exclusions)

    assert_empty result
  end

  test "subtract leaves the window untouched when the exclusion doesn't overlap" do
    windows = [ Scheduling::TimeWindow.new(480, 600) ]
    exclusions = [ { start_minute: 700, end_minute: 800 } ]
    result = Scheduling::TimeWindow.subtract(windows, exclusions)

    assert_equal 1, result.size
    assert_equal [ 480, 600 ], [ result[0].start_minute, result[0].end_minute ]
  end

  test "subtract trims only the overlapping edge" do
    windows = [ Scheduling::TimeWindow.new(480, 600) ]
    exclusions = [ { start_minute: 550, end_minute: 650 } ]
    result = Scheduling::TimeWindow.subtract(windows, exclusions)

    assert_equal 1, result.size
    assert_equal [ 480, 550 ], [ result[0].start_minute, result[0].end_minute ]
  end

  test "subtract accepts TimeWindow objects as exclusions" do
    windows = [ Scheduling::TimeWindow.new(480, 600) ]
    exclusions = [ Scheduling::TimeWindow.new(500, 520) ]
    result = Scheduling::TimeWindow.subtract(windows, exclusions)

    assert_equal 2, result.size
  end

  test "subtract applies multiple exclusions across multiple windows" do
    windows = [ Scheduling::TimeWindow.new(0, 100), Scheduling::TimeWindow.new(200, 300) ]
    exclusions = [ { start_minute: 40, end_minute: 60 }, { start_minute: 250, end_minute: 260 } ]
    result = Scheduling::TimeWindow.subtract(windows, exclusions)

    assert_equal 4, result.size
    assert_equal [ [ 0, 40 ], [ 60, 100 ], [ 200, 250 ], [ 260, 300 ] ],
                 result.map { |w| [ w.start_minute, w.end_minute ] }
  end
end

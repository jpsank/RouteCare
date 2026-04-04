require "test_helper"

class Messaging::ReplyParserTest < ActiveSupport::TestCase
  test "uses llm parse when available" do
    llm_singleton = Messaging::LlmAssistant.singleton_class
    original = llm_singleton.instance_method(:parse_reply)
    llm_singleton.define_method(:parse_reply) do |body:|
      _ = body
      { intent: "reschedule", proposed_windows: [ { "day" => "friday" } ] }
    end

    begin
      result = Messaging::ReplyParser.parse("Could we do Friday instead?")

      assert_equal :reschedule, result.intent
      assert_equal [ { "day" => "friday" } ], result.proposed_windows
    ensure
      llm_singleton.define_method(:parse_reply, original)
    end
  end

  test "extracts proposed windows from reschedule replies" do
    result = Messaging::ReplyParser.parse("Can we reschedule to Tuesday afternoon or Thursday after 3pm?")

    assert_equal :reschedule, result.intent
    assert_equal [
      { "day" => "tuesday", "time_of_day" => "afternoon", "time" => "3pm", "qualifier" => "after" },
      { "day" => "thursday", "time_of_day" => "afternoon", "time" => "3pm", "qualifier" => "after" }
    ], result.proposed_windows
  end

  test "extracts time-only suggestions when no day is provided" do
    result = Messaging::ReplyParser.parse("A different time would work, maybe after 4:30 pm")

    assert_equal :reschedule, result.intent
    assert_equal [
      { "time" => "4:30 pm", "qualifier" => "after" }
    ], result.proposed_windows
  end
end

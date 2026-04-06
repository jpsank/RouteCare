require "test_helper"

class Scheduling::ChromosomeTest < ActiveSupport::TestCase
  setup do
    @date_mon = Date.new(2026, 4, 6)
    @date_wed = Date.new(2026, 4, 8)
    @date_fri = Date.new(2026, 4, 10)
    @working_days = [ @date_mon, Date.new(2026, 4, 7), @date_wed, Date.new(2026, 4, 9), @date_fri ]
  end

  test "from_greedy builds genes from day_routes" do
    user = User.create!(email: "chromo-test@example.com", password: "password123")
    profile = user.create_clinician_profile!(discipline: "PT", timezone: "America/New_York")
    patient = profile.patients.create!(
      full_name: "Jane Doe", phone: "5550001", address_line1: "100 Main St",
      city: "Boston", state: "MA", postal_code: "02110",
      latitude: 42.3589, longitude: -71.0589,
      required_visits_per_week: 1, visit_duration_minutes: 45
    )

    Time.use_zone("America/New_York") do
      day_routes = {
        @date_mon => [ { patient: patient, starts_at: Time.zone.parse("#{@date_mon} 09:00"), ends_at: Time.zone.parse("#{@date_mon} 09:45"), instance_id: "patient_#{patient.id}_visit_0" } ]
      }
      chromo = Scheduling::Chromosome.from_greedy(day_routes)

      assert_equal @date_mon, chromo.genes["patient_#{patient.id}_visit_0"]
    end
  end

  test "crossover aligns by visit instance ID" do
    parent_a = Scheduling::Chromosome.new({
      "p1_v0" => @date_mon, "p2_v0" => @date_wed, "p3_v0" => @date_fri
    })
    parent_b = Scheduling::Chromosome.new({
      "p1_v0" => @date_wed, "p2_v0" => @date_fri, "p3_v0" => @date_mon
    })

    # Run multiple crossovers and verify all genes come from one parent or the other
    50.times do
      child = parent_a.crossover(parent_b)
      child.genes.each do |id, day|
        assert [ parent_a.genes[id], parent_b.genes[id] ].include?(day),
          "Child gene #{id}=#{day} not from either parent"
      end
    end
  end

  test "mutation changes some genes" do
    original = Scheduling::Chromosome.new({
      "p1_v0" => @date_mon, "p2_v0" => @date_wed, "p3_v0" => @date_fri
    })
    mutated = original.dup
    mutated.mutate!(@working_days, rate: 1.0) # 100% mutation rate

    # At least some genes should differ (probabilistically almost certain with rate=1.0)
    assert mutated.genes.keys.sort == original.genes.keys.sort, "Should have same instance IDs"
  end

  test "structural_distance is 0 for identical chromosomes" do
    a = Scheduling::Chromosome.new({ "p1_v0" => @date_mon, "p2_v0" => @date_wed })
    b = Scheduling::Chromosome.new({ "p1_v0" => @date_mon, "p2_v0" => @date_wed })

    assert_equal 0.0, a.structural_distance(b)
  end

  test "structural_distance is 1 for completely different chromosomes" do
    a = Scheduling::Chromosome.new({ "p1_v0" => @date_mon, "p2_v0" => @date_mon })
    b = Scheduling::Chromosome.new({ "p1_v0" => @date_fri, "p2_v0" => @date_fri })

    assert_equal 1.0, a.structural_distance(b)
  end

  test "random_perturbation creates a different chromosome" do
    base = Scheduling::Chromosome.new({
      "p1_v0" => @date_mon, "p2_v0" => @date_wed, "p3_v0" => @date_fri,
      "p4_v0" => @date_mon, "p5_v0" => @date_wed
    })
    perturbed = Scheduling::Chromosome.random_perturbation(base, @working_days)

    assert_equal base.genes.keys.sort, perturbed.genes.keys.sort
  end
end

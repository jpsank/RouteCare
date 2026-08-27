module Scheduling
  VisitInstance = Data.define(:id, :patient_id, :patient, :location, :duration, :priority, :availability_windows, :unavailability_windows) do
    def initialize(unavailability_windows: {}, **rest)
      super(unavailability_windows: unavailability_windows, **rest)
    end
  end
end

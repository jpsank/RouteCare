module Scheduling
  VisitInstance = Data.define(:id, :patient_id, :patient, :location, :duration, :priority, :availability_windows)
end

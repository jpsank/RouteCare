class AlertSerializer
  class << self
    def as_json(alert)
      {
        id: alert.id,
        category: alert.category,
        severity: alert.severity,
        status: alert.status,
        message: alert.message,
        due_at: alert.due_at,
        read_at: alert.read_at,
        metadata: alert.metadata,
        created_at: alert.created_at
      }
    end
  end
end

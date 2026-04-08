class MessageSerializer
  class << self
    def as_json(message)
      {
        id: message.id,
        visit_id: message.visit_id,
        patient_id: message.patient_id,
        patient_name: message.patient&.full_name,
        direction: message.direction,
        channel: message.channel,
        status: message.status,
        body: message.body,
        requires_approval: message.requires_approval,
        approved_at: message.approved_at,
        proposed_starts_at: message.proposed_starts_at,
        proposed_ends_at: message.proposed_ends_at,
        metadata: message.metadata,
        created_at: message.created_at
      }
    end
  end
end

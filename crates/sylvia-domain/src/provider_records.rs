//! `domain/provider_records.py`: ingress identity for private provider output; never an
//! ordinary session message.

/// `ProviderPrivateRecord`. Python leaves `payload` out of the record's `repr`, so `Debug`
/// leaves it out too.
#[derive(Clone, PartialEq, Eq)]
pub struct ProviderPrivateRecord {
    pub session_id: String,
    pub attempt_id: String,
    pub response_id: String,
    pub turn_id: String,
    pub basis_message_id: String,
    pub source_order: i64,
    pub provider_id: String,
    pub model_id: String,
    pub kind: String,
    pub payload: String,
}

impl std::fmt::Debug for ProviderPrivateRecord {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProviderPrivateRecord")
            .field("session_id", &self.session_id)
            .field("attempt_id", &self.attempt_id)
            .field("response_id", &self.response_id)
            .field("turn_id", &self.turn_id)
            .field("basis_message_id", &self.basis_message_id)
            .field("source_order", &self.source_order)
            .field("provider_id", &self.provider_id)
            .field("model_id", &self.model_id)
            .field("kind", &self.kind)
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn debug_leaves_out_the_payload() {
        let record = ProviderPrivateRecord {
            session_id: "s".into(),
            attempt_id: "a".into(),
            response_id: "r".into(),
            turn_id: "t".into(),
            basis_message_id: "b".into(),
            source_order: 1,
            provider_id: "p".into(),
            model_id: "m".into(),
            kind: "reasoning".into(),
            payload: "secret thoughts".into(),
        };
        assert!(!format!("{record:?}").contains("secret thoughts"));
    }
}

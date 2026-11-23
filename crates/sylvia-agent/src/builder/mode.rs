//! `agent/_mode.py`: the operating mode of the agent runner.

/// `AgentMode`. The Rust runner runs only `BuilderFreeform`; `DmSession` is still representable
/// so construction can refuse it with Python's text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AgentMode {
    /// `BUILDER_INTERVIEW`: rigid system prompt, widget markers.
    BuilderInterview,
    /// `BUILDER_FREEFORM`: file tools, flexible prompt.
    BuilderFreeform,
    /// `DM_SESSION`: Ruleset tools and the prompt assembler.
    DmSession,
}

impl AgentMode {
    /// The enum value.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::BuilderInterview => "builder_interview",
            Self::BuilderFreeform => "builder_freeform",
            Self::DmSession => "dm_session",
        }
    }

    /// `AgentMode(value)`.
    pub fn parse(value: &str) -> Option<Self> {
        [Self::BuilderInterview, Self::BuilderFreeform, Self::DmSession]
            .into_iter()
            .find(|mode| mode.as_str() == value)
    }
}

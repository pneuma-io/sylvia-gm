//! `domain/interview_contribution.py`: restricted artifact-owned questions for the guided
//! Campaign Interview.

use crate::content::validate_content_shape;
use crate::error::{DomainError, DomainResult};
use crate::package_library::{parse_content_json_bytes, JsonShapeLimits};
use crate::pyops::{py_eq, py_str, py_truthy};
use serde_json::json;
use sylvia_base::pystr;
use sylvia_base::{Json, JsonMap};

/// `INTERVIEW_CONTRIBUTION_PATH`.
pub const INTERVIEW_CONTRIBUTION_PATH: &str = "interview-contribution.json";
/// `INTERVIEW_CONTRIBUTION_FORMAT_ID`.
pub const INTERVIEW_CONTRIBUTION_FORMAT_ID: &str = "sylvia-gm-interview-contribution";
/// `INTERVIEW_CONTRIBUTION_FORMAT_VERSION`.
pub const INTERVIEW_CONTRIBUTION_FORMAT_VERSION: i64 = 1;
/// `INTERVIEW_CONTRIBUTION_BYTE_LIMIT`.
pub const INTERVIEW_CONTRIBUTION_BYTE_LIMIT: usize = 32 * 1024;
const OWNER_KINDS: [&str; 3] = ["ruleset", "campaign_setting", "campaign_plan"];

/// `InterviewContribution`: one validated, data-only question group owned by an immutable
/// artifact.
#[derive(Debug, Clone, PartialEq)]
pub struct InterviewContribution {
    pub owner_kind: String,
    pub contribution_id: String,
    pub title: String,
    pub questions: Vec<JsonMap>,
}

impl InterviewContribution {
    /// `as_payload()`.
    pub fn as_payload(&self) -> JsonMap {
        let payload = json!({
            "format_id": INTERVIEW_CONTRIBUTION_FORMAT_ID,
            "format_version": INTERVIEW_CONTRIBUTION_FORMAT_VERSION,
            "owner_kind": self.owner_kind,
            "id": self.contribution_id,
            "title": self.title,
            "questions": self.questions,
        });
        payload.as_object().cloned().expect("an object")
    }

    /// `question_text()`: the title, then the numbered prompts.
    pub fn question_text(&self) -> String {
        let prompts: Vec<String> = self
            .questions
            .iter()
            .enumerate()
            .map(|(index, question)| format!("{}. {}", index + 1, py_str(&question["prompt"])))
            .collect();
        format!("{}\n{}", self.title, prompts.join("\n"))
    }
}

/// `_text(value, label, maximum=...)`: trimmed, nonempty text without NUL.
fn text(value: &Json, label: &str, maximum: usize) -> DomainResult<String> {
    match value {
        Json::String(text)
            if text == pystr::strip(text)
                && !text.is_empty()
                && pystr::len(text) <= maximum
                && !text.contains('\0') =>
        {
            Ok(text.clone())
        }
        _ => Err(DomainError::value(format!("{label} is invalid"))),
    }
}

/// `parse_interview_contribution_bytes(value, expected_owner_kind=...)`: the shared field
/// definition, then ownership and ordered question references.
pub fn parse_interview_contribution_bytes(
    value: &[u8],
    expected_owner_kind: &str,
) -> DomainResult<InterviewContribution> {
    if !OWNER_KINDS.contains(&expected_owner_kind) {
        return Err(DomainError::value("Interview Contribution owner kind is unsupported"));
    }
    if value.is_empty() || value.len() > INTERVIEW_CONTRIBUTION_BYTE_LIMIT {
        return Err(DomainError::value("Interview Contribution is empty or oversized"));
    }
    let raw = parse_content_json_bytes(value, "Interview Contribution", JsonShapeLimits::new(12, 10_000))?;
    validate_content_shape("interview_contribution", &raw, Some(INTERVIEW_CONTRIBUTION_PATH))?;
    // The schema has fixed every type read below.
    let contribution = raw.as_object().expect("the schema requires an object");
    if !py_eq(&contribution["owner_kind"], &Json::from(expected_owner_kind)) {
        return Err(DomainError::value("Interview Contribution owner is unsupported"));
    }
    let questions: Vec<JsonMap> = contribution["questions"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Json::as_object)
        .cloned()
        .collect();
    let mut prior_ids: Vec<&str> = Vec::new();
    for question in &questions {
        let question_id = question["id"].as_str().unwrap_or_default();
        if prior_ids.contains(&question_id) {
            return Err(DomainError::value("Interview question identities must be unique"));
        }
        let option_ids: Vec<&str> =
            question["options"].as_array().into_iter().flatten().filter_map(|o| o["id"].as_str()).collect();
        let mut unique = option_ids.clone();
        unique.sort_unstable();
        unique.dedup();
        if unique.len() != option_ids.len() {
            return Err(DomainError::value("Interview choice identities must be unique"));
        }
        if let Json::Object(condition) = &question["condition"] {
            if !prior_ids.contains(&condition["question_id"].as_str().unwrap_or_default()) {
                return Err(DomainError::value("Interview conditions may reference only prior questions"));
            }
        }
        prior_ids.push(question_id);
    }
    Ok(InterviewContribution {
        owner_kind: expected_owner_kind.to_owned(),
        contribution_id: contribution["id"].as_str().unwrap_or_default().to_owned(),
        title: contribution["title"].as_str().unwrap_or_default().to_owned(),
        questions,
    })
}

/// `validate_interview_contribution_answers(contribution, value)`: exact typed answers against
/// one already validated contribution.
pub fn validate_interview_contribution_answers(
    contribution: &InterviewContribution,
    value: &Json,
) -> DomainResult<JsonMap> {
    let Json::Object(answer) = value else {
        return Err(DomainError::value("Interview Contribution answer must be an object"));
    };
    if answer.len() != 2 || !answer.contains_key("contribution_id") || !answer.contains_key("answers") {
        return Err(DomainError::value("Interview Contribution answer fields are invalid"));
    }
    if !py_eq(&answer["contribution_id"], &Json::from(contribution.contribution_id.as_str())) {
        return Err(DomainError::value("Interview Contribution answer identity is stale"));
    }
    let Json::Object(answers) = &answer["answers"] else {
        return Err(DomainError::value("Interview Contribution answers must be an object"));
    };
    let known_ids: Vec<String> = contribution.questions.iter().map(|q| py_str(&q["id"])).collect();
    if answers.keys().any(|key| !known_ids.contains(key)) {
        return Err(DomainError::value("Interview Contribution contains unknown answers"));
    }
    let mut captured = JsonMap::new();
    for question in &contribution.questions {
        let question_id = py_str(&question["id"]);
        let active = match &question["condition"] {
            Json::Object(condition) => {
                let current = captured.get(&py_str(&condition["question_id"])).cloned().unwrap_or(Json::Null);
                py_eq(&current, &condition["equals"])
            }
            _ => true,
        };
        if !active {
            if answers.contains_key(&question_id) {
                return Err(DomainError::value("Inactive Interview question cannot be answered"));
            }
            continue;
        }
        let raw_value = answers.get(&question_id).filter(|value| !value.is_null());
        let Some(raw_value) = raw_value else {
            if py_truthy(&question["required"]) {
                return Err(DomainError::value("Required Interview question is unanswered"));
            }
            continue;
        };
        if py_eq(&question["type"], &Json::from("choice")) {
            let allowed: Vec<String> = question["options"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|option| py_str(&option["id"]))
                .collect();
            match raw_value {
                Json::String(choice) if allowed.contains(choice) => {
                    captured.insert(question_id, raw_value.clone());
                }
                _ => return Err(DomainError::value("Interview choice answer is invalid")),
            }
        } else {
            captured.insert(question_id, Json::String(text(raw_value, "Interview free-text answer", 2_000)?));
        }
    }
    Ok(json!({"contribution_id": contribution.contribution_id, "answers": captured})
        .as_object()
        .cloned()
        .expect("an object"))
}

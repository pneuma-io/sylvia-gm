//! `agent/_builder_question_answers.py`: genuine Builder form input bound to its immutable saved
//! question evidence.

use super::rejected;
use indexmap::IndexMap;
use sylvia_base::pystr::strip;
use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::pyops::{py_eq, py_truthy};
use sylvia_domain::session::{SessionContext, SessionMessageKind};

/// `isinstance(value, str) and value.strip()`.
fn genuine_text(value: Option<&Json>) -> bool {
    matches!(value, Some(Json::String(text)) if !strip(text).is_empty())
}

/// `isinstance(value, str) and value`.
fn nonempty_text(value: Option<&Json>) -> Option<&str> {
    match value {
        Some(Json::String(text)) if !text.is_empty() => Some(text),
        _ => None,
    }
}

/// `builder_question_details(content)`: the same answerable widget in display projections and
/// submission checks.
pub fn builder_question_details(content: &JsonMap) -> Option<JsonMap> {
    let is = |key: &str, text: &str| content.get(key).is_some_and(|value| py_eq(value, &Json::from(text)));
    if !is("kind", "builder_tool_result") || !is("tool_name", "ask_question") {
        return None;
    }
    let Some(Json::Object(result)) = content.get("result") else { return None };
    if result.get("success") != Some(&Json::Bool(true)) {
        return None;
    }
    let Some(Json::Object(widget)) = result.get("widget") else { return None };
    if !result.get("status").is_some_and(|status| py_eq(status, &Json::from("awaiting_answer"))) {
        return None;
    }
    let kind = match widget.get("type") {
        Some(Json::String(kind)) if matches!(kind.as_str(), "text" | "select" | "cascade" | "confirm") => {
            kind
        }
        _ => return None,
    };
    let Some(Json::Object(data)) = widget.get("data") else { return None };
    if !genuine_text(data.get("id")) || !genuine_text(data.get("question")) {
        return None;
    }
    let tool_call_id = nonempty_text(content.get("tool_call_id"))?;
    let mut options = Vec::new();
    if kind != "text" {
        let mut raw = data.get("options").cloned().unwrap_or(Json::Null);
        if kind == "confirm" && !py_truthy(&raw) {
            raw = Json::Array(vec![Json::from("Yes"), Json::from("No")]);
        }
        let Json::Array(raw) = raw else { return None };
        for option in &raw {
            match option {
                Json::String(text) if !text.is_empty() => {
                    options.push((text.clone(), text.clone()));
                }
                Json::Object(option) => {
                    if let (Some(value), Some(label)) =
                        (nonempty_text(option.get("value")), nonempty_text(option.get("label")))
                    {
                        options.push((value.to_owned(), label.to_owned()));
                    }
                }
                _ => {}
            }
        }
        if options.is_empty() {
            return None;
        }
    }
    let mut details = JsonMap::new();
    details.insert("tool_call_id".into(), Json::from(tool_call_id));
    details.insert("widget_id".into(), data["id"].clone());
    details.insert("type".into(), Json::from(kind.as_str()));
    details.insert("question".into(), data["question"].clone());
    let options = options
        .into_iter()
        .map(|(value, label)| {
            let mut option = JsonMap::new();
            option.insert("value".into(), Json::from(value));
            option.insert("label".into(), Json::from(label));
            Json::Object(option)
        })
        .collect();
    details.insert("options".into(), Json::Array(options));
    Some(details)
}

/// `builder_question_answer_content(context, answers, user_text)`: the whole active form
/// validated and its snapshots derived from saved evidence.
pub fn builder_question_answer_content(
    context: &SessionContext,
    answers: &Json,
    user_text: &str,
) -> CodedResult<JsonMap> {
    if context.session.role() != WriterRole::Builder {
        return Err(rejected("Question answers require a Builder session"));
    }
    let answers = match answers {
        Json::Array(answers) if !answers.is_empty() => answers,
        _ => return Err(rejected("Question answers must be a nonempty list")),
    };
    let latest_user = context
        .messages
        .iter()
        .filter(|row| row.kind() == SessionMessageKind::User)
        .map(|row| row.sequence())
        .max()
        .unwrap_or(0);
    let mut questions: IndexMap<String, JsonMap> = IndexMap::new();
    for row in &context.messages {
        if row.kind() == SessionMessageKind::Evidence && row.sequence() > latest_user {
            if let Some(question) = builder_question_details(row.content()) {
                questions.insert(row.message_id().to_owned(), question);
            }
        }
    }
    let mut snapshots: Vec<JsonMap> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for answer in answers {
        let Json::Object(answer) = answer else {
            return Err(rejected("Question answer fields are invalid"));
        };
        let mut keys: Vec<&str> = answer.keys().map(String::as_str).collect();
        keys.sort_unstable();
        if keys != ["custom", "question_message_id", "value"] {
            return Err(rejected("Question answer fields are invalid"));
        }
        let identity = match &answer["question_message_id"] {
            Json::String(identity) if questions.contains_key(identity) && !seen.contains(identity) => {
                identity
            }
            _ => return Err(rejected("Question is stale, duplicated, or belongs to another session")),
        };
        let (value, custom) = match (&answer["value"], &answer["custom"]) {
            (Json::String(value), Json::Bool(custom)) if !strip(value).is_empty() => (value, *custom),
            _ => return Err(rejected("Question answer must contain a genuine value")),
        };
        let question = &questions[identity];
        let kind = question["type"].as_str().unwrap_or_default();
        if kind == "text" && custom {
            return Err(rejected("Text questions do not accept a choice override"));
        }
        let mut label = value.clone();
        if kind != "text" && !custom {
            let options = question["options"].as_array().map(Vec::as_slice).unwrap_or_default();
            let Some(selected) =
                options.iter().find(|option| option["value"].as_str() == Some(value.as_str()))
            else {
                return Err(rejected("Question answer is not an offered choice"));
            };
            label = selected["label"].as_str().unwrap_or_default().to_owned();
            let same =
                options.iter().filter(|option| option["label"].as_str() == Some(label.as_str())).count();
            if same > 1 && *value != label {
                label = format!("{label} ({value})");
            }
        }
        seen.push(identity.clone());
        let mut snapshot = JsonMap::new();
        snapshot.insert("question_message_id".into(), Json::from(identity.as_str()));
        for (key, item) in question {
            if key != "options" {
                snapshot.insert(key.clone(), item.clone());
            }
        }
        snapshot.insert("value".into(), Json::from(value.as_str()));
        snapshot.insert("label".into(), Json::from(label));
        snapshot.insert("custom".into(), Json::Bool(custom));
        snapshots.push(snapshot);
    }
    if seen.len() != questions.len() {
        return Err(rejected("Answer every current question before submitting"));
    }
    if snapshots
        .iter()
        .map(|row| row["question_message_id"].as_str().unwrap_or_default())
        .ne(questions.keys().map(String::as_str))
    {
        return Err(rejected("Question answers must follow their saved order"));
    }
    let blocks: Vec<String> = snapshots
        .iter()
        .enumerate()
        .map(|(index, row)| {
            format!(
                "{}. {}\nAnswer: {}",
                index + 1,
                sylvia_domain::pyops::py_str(&row["question"]),
                row["label"].as_str().unwrap_or_default()
            )
        })
        .collect();
    let text = format!("Here are my answers:\n\n{}", blocks.join("\n\n"));
    if user_text != text {
        return Err(rejected("Question answer text does not match the saved form"));
    }
    let mut content = JsonMap::new();
    content.insert("kind".into(), Json::from("builder_question_answer"));
    content.insert("text".into(), Json::from(text));
    content.insert("answers".into(), Json::Array(snapshots.into_iter().map(Json::Object).collect()));
    Ok(content)
}

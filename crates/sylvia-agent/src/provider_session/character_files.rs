//! `ProviderSessionAdapter._relevant_character_files_message`: the exact Character Files the
//! current player wording names unambiguously, read through the provider input's reference
//! reader. Its texts are `gm_texts.json`'s `character_files_prefix` and
//! `character_files_interpretation`.

use super::context::{ProviderSessionAdapter, SessionProviderInput};
use super::texts::gm_text;
use serde_json::{json, Value as Json};
use std::collections::HashMap;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pystr, CodedError, CodedResult, JsonMap};

/// Python's `\w` for a `str` pattern: `str.isalnum()` or `_`.
fn is_word(character: char) -> bool {
    character == '_' || pystr::is_alnum(character)
}

/// A character and its simple case mappings, as `re.IGNORECASE` compares them.
fn variants(c: char) -> impl Iterator<Item = char> {
    let mut lower = c.to_lowercase();
    let mut upper = c.to_uppercase();
    let lower = if lower.len() == 1 { lower.next() } else { None };
    let upper = if upper.len() == 1 { upper.next() } else { None };
    [Some(c), lower, upper].into_iter().flatten()
}

fn same_ignoring_case(a: char, b: char) -> bool {
    a == b || variants(a).any(|x| variants(b).any(|y| x == y))
}

/// `re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, flags=re.IGNORECASE) is not None`.
fn mentions(text: &str, alias: &str) -> bool {
    let (text, alias): (Vec<char>, Vec<char>) = (text.chars().collect(), alias.chars().collect());
    (0..=text.len().saturating_sub(alias.len())).filter(|_| alias.len() <= text.len()).any(|start| {
        alias.iter().enumerate().all(|(offset, &c)| same_ignoring_case(text[start + offset], c))
            && !(start > 0 && is_word(text[start - 1]))
            && !text.get(start + alias.len()).is_some_and(|&c| is_word(c))
    })
}

/// `name.strip().split(maxsplit=1)[0]`; an empty result is Python's `IndexError`.
fn given_name(name: &str) -> CodedResult<&str> {
    pystr::strip(name).split(pystr::is_space).find(|part| !part.is_empty()).ok_or_else(|| {
        CodedError::new("internal_error", "list index out of range").with_error_type("IndexError")
    })
}

fn invalid() -> CodedError {
    CodedError::new("participant_reference_invalid", "The exact Character File retrieval returned invalid data")
        .with_error_type("CommandRejected")
}

impl ProviderSessionAdapter {
    /// `_relevant_character_files_message(provider_input, current_player_text=...)`.
    pub(crate) async fn relevant_character_files_message(
        &self,
        input: &SessionProviderInput,
        current_player_text: &str,
    ) -> CodedResult<Option<JsonMap>> {
        let Some(reader) = input.reference_reader.clone() else { return Ok(None) };
        let roster = self.participant_prompt_payloads(input).await?;
        let mut given_name_counts: HashMap<String, i64> = HashMap::new();
        for entry in &roster {
            let Some(name) = entry.get("name").and_then(Json::as_str) else { continue };
            if pystr::strip(name).is_empty() {
                continue;
            }
            *given_name_counts.entry(pystr::casefold(given_name(name)?)).or_insert(0) += 1;
        }
        let mut matched = Vec::new();
        for entry in &roster {
            let participant_id = entry.get("participant_id").and_then(Json::as_str).filter(|id| !id.is_empty());
            let name = entry.get("name").and_then(Json::as_str).filter(|name| !name.is_empty());
            let mut aliases: Vec<&str> = name.into_iter().collect();
            if let Some(name) = name {
                let given = given_name(name)?;
                if pystr::casefold(given) != pystr::casefold(name)
                    && given_name_counts.get(&pystr::casefold(given)) == Some(&1)
                {
                    aliases.push(given);
                }
            }
            let directly_referenced = aliases.iter().copied().find(|alias| mentions(current_player_text, alias));
            let (Some(participant_id), Some(_), Some(directly_referenced)) = (participant_id, name, directly_referenced)
            else {
                continue;
            };
            let reference = reader("participant".to_owned(), participant_id.to_owned()).await?;
            let content = match (reference.get("source"), reference.get("path"), reference.get("content")) {
                (Some(Json::String(source)), Some(Json::String(path)), Some(Json::String(content)))
                    if source == "participant" && path == participant_id =>
                {
                    content
                }
                _ => return Err(invalid()),
            };
            let Ok(Json::Object(character_file)) = pyjson::loads(content) else { return Err(invalid()) };
            matched.push(json!({
                "directly_referenced_by_player": true,
                "matched_name": directly_referenced,
                "participant_id": participant_id,
                "source": "participant",
                "path": participant_id,
                "campaign_id": reference.get("campaign_id").cloned().unwrap_or(Json::Null),
                "character_file": character_file,
                "precedence": reference.get("precedence").cloned().unwrap_or(Json::Null),
            }));
        }
        if matched.is_empty() {
            return Ok(None);
        }
        let facts = json!({"interpretation": gm_text("character_files_interpretation"), "references": matched});
        let content = format!("{}{}", gm_text("character_files_prefix"), pyjson::dumps(&facts, Dump::CANONICAL));
        let mut message = JsonMap::new();
        message.insert("role".into(), "system".into());
        message.insert("content".into(), content.into());
        Ok(Some(message))
    }
}

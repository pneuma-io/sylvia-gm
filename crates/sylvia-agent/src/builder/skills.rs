//! `agent/_skills.py`: the tracked construction skills and their packaged references, as
//! exported into `builder_texts.json`.

use super::{texts, value_error};
use sylvia_base::{CodedResult, Json};
use sylvia_domain::session::BUILDER_SKILLS;

/// `LoadedSkill`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadedSkill {
    pub name: String,
    pub text: String,
}

/// `builder_skills()`: the frozen Builder construction surface.
pub fn builder_skills() -> &'static [&'static str] {
    BUILDER_SKILLS
}

fn skill(name: &str) -> CodedResult<&'static Json> {
    if !BUILDER_SKILLS.contains(&name) {
        return Err(value_error("Unknown Builder construction skill"));
    }
    Ok(&texts()["skills"][name])
}

fn references(skill: &Json) -> impl Iterator<Item = (&str, &str)> {
    skill["references"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|pair| Some((pair.get(0)?.as_str()?, pair.get(1)?.as_str()?)))
}

/// `builder_skill_references(name)`: the packaged example and reference files of a tracked skill.
pub fn builder_skill_references(name: &str) -> CodedResult<Vec<String>> {
    Ok(references(skill(name)?).map(|(path, _)| path.to_owned()).collect())
}

/// `load_builder_skill(name, reference=...)`: a tracked skill or one exact member of its
/// reference catalog.
pub fn load_builder_skill(name: &str, reference: Option<&str>) -> CodedResult<LoadedSkill> {
    let skill = skill(name)?;
    let text = match reference {
        None => skill["text"].as_str().unwrap_or_default(),
        Some(reference) => references(skill)
            .find(|(path, _)| *path == reference)
            .map(|(_, text)| text)
            .ok_or_else(|| value_error("Unknown Builder skill reference"))?,
    };
    Ok(LoadedSkill { name: name.to_owned(), text: text.to_owned() })
}

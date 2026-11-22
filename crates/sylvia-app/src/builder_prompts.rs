//! `builder/_prompts.py`: the default Builder system prompt retained for legacy Builder callers.
//!
//! The prompt is model-read, so its literal text lives in `builder_prompt_text.rs`, generated
//! from the Python source by `generate_builder_prompt_text.py`; this module only joins it as
//! Python does. Python's `ruleset_name` and `project_root` arguments are never read, so they
//! are not taken here.

use sylvia_base::Json;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::{py_contains, py_get_attr, py_iter, py_str, py_truthy, py_type_name};

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "builder_prompt_text.rs"]
mod text;

/// The optional `available_*` arguments, as the caller's JSON values (`None` when not passed).
#[derive(Debug, Clone, Copy, Default)]
pub struct AvailableOptions<'a> {
    pub rulesets: Option<&'a Json>,
    pub settings: Option<&'a Json>,
    pub plans: Option<&'a Json>,
    pub characters: Option<&'a Json>,
    pub companions: Option<&'a Json>,
}

fn present(value: Option<&Json>) -> Option<&Json> {
    value.filter(|value| py_truthy(value))
}

/// `item.get(key, default)` as text, through `str()` as an f-string formats it.
fn field(item: &Json, key: &str, default: &Json) -> DomainResult<String> {
    Ok(py_str(py_get_attr(item, key)?.unwrap_or(default)))
}

/// `mapping.items()`, with Python's `AttributeError` for a value that is not a `dict`.
fn items(value: &Json) -> DomainResult<Vec<(String, &Json)>> {
    match value {
        Json::Object(map) => Ok(map.iter().map(|(key, item)| (key.clone(), item)).collect()),
        other => Err(DomainError::uncaught(
            "AttributeError",
            format!("'{}' object has no attribute 'items'", py_type_name(other)),
        )),
    }
}

/// One `    {"id":...,"name":...[,"description":...]}` line.
fn option_line(id: &str, name: &str, description: Option<&str>) -> String {
    match description {
        Some(description) => format!(r#"    {{"id":"{id}","name":"{name}","description":"{description}"}}"#),
        None => format!(r#"    {{"id":"{id}","name":"{name}"}}"#),
    }
}

/// `name.replace(" — Campaign Plan", "")` when the suffix is present, with Python's errors for
/// a name that is not text.
fn plan_name(name: &Json) -> DomainResult<String> {
    const SUFFIX: &str = " — Campaign Plan";
    if !py_contains(&Json::from(SUFFIX), name)? {
        return Ok(py_str(name));
    }
    match name {
        Json::String(text) => Ok(text.replace(SUFFIX, "")),
        other => Err(DomainError::uncaught(
            "AttributeError",
            format!("'{}' object has no attribute 'replace'", py_type_name(other)),
        )),
    }
}

/// A grouped section: `<heading>'<group>':\n` and its lines, for each non-empty group.
fn grouped(
    block: &mut String,
    groups: &Json,
    heading: &str,
    line: impl Fn(&Json) -> DomainResult<String>,
) -> DomainResult<()> {
    for (group, members) in items(groups)? {
        if !py_truthy(members) {
            continue;
        }
        let lines = py_iter(members)?.iter().map(&line).collect::<DomainResult<Vec<_>>>()?;
        block.push_str(&format!("{heading}'{group}':\n"));
        block.push_str(&lines.join("\n"));
        block.push_str("\n\n");
    }
    Ok(())
}

fn options_block(options: &AvailableOptions) -> DomainResult<String> {
    let Some(rulesets) = present(options.rulesets) else { return Ok(String::new()) };
    let mut lines = Vec::new();
    for ruleset in py_iter(rulesets)? {
        let id = py_get_attr(&ruleset, "id")?.cloned().unwrap_or(Json::from(""));
        let name = field(&ruleset, "name", &id)?;
        let description = py_get_attr(&ruleset, "description")?.cloned().unwrap_or(Json::from(""));
        let description = py_truthy(&description).then(|| py_str(&description));
        lines.push(option_line(&py_str(&id), &name, description.as_deref()));
    }
    let mut block = format!("{}{}{}", text::OPTIONS_HEADER, lines.join("\n"), text::OPTIONS_HEADER_END);
    if let Some(settings) = present(options.settings) {
        grouped(&mut block, settings, "Campaign Settings for ruleset ", |setting| {
            let slug = py_get_attr(setting, "slug")?.cloned().unwrap_or(Json::from(""));
            let name = field(setting, "name", &slug)?;
            let description = py_get_attr(setting, "description")?.cloned().unwrap_or(Json::from(""));
            let description = py_truthy(&description).then(|| py_str(&description));
            Ok(option_line(&py_str(&slug), &name, description.as_deref()))
        })?;
    }
    if let Some(plans) = present(options.plans) {
        grouped(&mut block, plans, "Campaign Plans for setting ", |plan| {
            let slug = py_get_attr(plan, "slug")?.cloned().unwrap_or(Json::from(""));
            let name = plan_name(py_get_attr(plan, "name")?.unwrap_or(&slug))?;
            let act_count = py_get_attr(plan, "act_count")?.cloned().unwrap_or(Json::from(0));
            let description = py_truthy(&act_count).then(|| format!("{} acts", py_str(&act_count)));
            Ok(option_line(&py_str(&slug), &name, description.as_deref()))
        })?;
    }
    for (groups, heading) in [
        (options.characters, "Player Characters for setting "),
        (options.companions, "Companions for setting "),
    ] {
        if let Some(groups) = present(groups) {
            grouped(&mut block, groups, heading, |member| {
                let slug = py_get_attr(member, "slug")?.cloned().unwrap_or(Json::from(""));
                let name = field(member, "name", &slug)?;
                Ok(option_line(&py_str(&slug), &name, None))
            })?;
        }
    }
    block.push_str(text::OPTIONS_WARNING);
    Ok(block)
}

/// `build_builder_system_prompt(ruleset_name, project_root, override, prompt_mode,
/// available_rulesets, ...)`.
pub fn build_builder_system_prompt(
    override_prompt: Option<&str>,
    prompt_mode: &str,
    options: &AvailableOptions,
) -> DomainResult<String> {
    let override_prompt = override_prompt.filter(|text| !text.is_empty());
    if let (Some(text), "replace") = (override_prompt, prompt_mode) {
        return Ok(text.to_owned());
    }
    let preamble = format!(
        "{}{}{}{}{}",
        text::PREAMBLE_HEAD,
        options_block(options)?,
        text::PREAMBLE_MIDDLE,
        text::GREETING,
        text::PREAMBLE_TAIL
    );
    if let (Some(text), "append") = (override_prompt, prompt_mode) {
        return Ok(format!("{preamble}\n\n{text}"));
    }
    Ok(preamble)
}

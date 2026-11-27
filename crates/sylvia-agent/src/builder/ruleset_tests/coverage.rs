//! `agent/_ruleset_test_coverage.py`: count observed, asserted scenario outcomes rather than
//! authored coverage labels.
//!
//! The records read here are the scenario suite (`sylvia_domain::ruleset_tests` admitted it, so
//! every assertion has a string `subject` and a `path` of strings and integers) and what
//! [`super::run_scenario`] built: each tool result is `{"tool", "result"}` with the result an
//! object, and each receipt the plain projection of an action receipt. Values of another shape
//! simply do not match, where Python would raise on a record no caller builds.

use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};

/// `value.get(key)` as text, when it is text.
fn text<'a>(value: Option<&'a JsonMap>, key: &str) -> Option<&'a str> {
    value.and_then(|map| map.get(key)).and_then(Json::as_str)
}

/// `item["result"]` of a tool result.
fn result_of(item: &JsonMap) -> Option<&JsonMap> {
    item.get("result").and_then(Json::as_object)
}

/// `receipt.get("result", {}).get("tool")`, when it is non-empty text (`if name:`).
fn receipt_tool(receipt: &JsonMap) -> Option<&str> {
    text(receipt.get("result").and_then(Json::as_object), "tool").filter(|name| !name.is_empty())
}

/// The strings of a JSON list (the coverage lists and `covers`).
fn strings(value: Option<&Json>) -> impl Iterator<Item = &str> {
    value.and_then(Json::as_array).into_iter().flatten().filter_map(Json::as_str)
}

/// A sorted JSON list of strings.
fn sorted<'a>(values: impl IntoIterator<Item = &'a String>) -> Json {
    let values: BTreeSet<&String> = values.into_iter().collect();
    Json::Array(values.into_iter().map(|value| Json::String(value.clone())).collect())
}

/// Whether a path part is `part` (a string).
fn is(part: Option<&Json>, expected: &str) -> bool {
    part.and_then(Json::as_str) == Some(expected)
}

/// `checked_outcomes(assertions, receipt, tool_results)`: attribute checks to the result they
/// inspect; state checks cover this step's actions.
pub fn checked_outcomes(assertions: &[Json], receipt: Option<&JsonMap>, tool_results: &[JsonMap]) -> JsonMap {
    let mut actions: BTreeSet<String> = BTreeSet::new();
    let mut rejections: BTreeSet<String> = BTreeSet::new();
    let mut preconditions: BTreeSet<String> = BTreeSet::new();
    let tool = |item: &JsonMap| item.get("tool").and_then(Json::as_str).unwrap_or_default().to_owned();
    let mut committed: BTreeSet<String> = tool_results
        .iter()
        .filter(|item| text(result_of(item), "status") == Some("action_committed"))
        .map(tool)
        .collect();
    // `if receipt and ...`: an empty receipt is falsy.
    let receipt = receipt.filter(|receipt| !receipt.is_empty());
    let receipt_committed = receipt.is_some_and(|receipt| text(Some(receipt), "status") == Some("committed"));
    if let Some(name) = receipt.filter(|_| receipt_committed).and_then(receipt_tool) {
        committed.insert(name.to_owned());
    }
    let empty = Vec::new();
    for assertion in assertions {
        let assertion = assertion.as_object();
        let subject = text(assertion, "subject").unwrap_or_default();
        let path = assertion.and_then(|item| item.get("path")).and_then(Json::as_array).unwrap_or(&empty);
        if subject == "state" && !path.is_empty() {
            actions.extend(committed.iter().cloned());
        } else if let Some(receipt) = receipt.filter(|_| {
            subject == "control" && !path.is_empty()
                || subject == "receipt"
                    && path.len() > 2
                    && is(path.first(), "result")
                    && is(path.get(1), "context")
        }) {
            if let Some(name) = receipt_tool(receipt).filter(|_| receipt_committed) {
                actions.insert(name.to_owned());
            }
        } else if subject == "tool_results" && path.len() >= 3 && is_int(&path[0]) {
            // `0 <= path[0] < len(tool_results)`; an integer beyond `i64` is out of range.
            let Some(item) = path[0]
                .as_i64()
                .and_then(|index| usize::try_from(index).ok())
                .and_then(|index| tool_results.get(index))
            else {
                continue;
            };
            if !is(path.get(1), "result") {
                continue;
            }
            let result = result_of(item);
            let status = text(result, "status");
            if status == Some("action_committed") && is(path.get(2), "result") && path.len() > 3 {
                actions.insert(tool(item));
            } else if matches!(status, Some("rejected" | "action_failed"))
                && (is(path.get(2), "status") || is(path.get(2), "code"))
            {
                rejections.insert(tool(item));
                if text(result, "code") == Some("ruleset_precondition_failed") {
                    preconditions.insert(tool(item));
                }
            }
        }
    }
    let mut outcome = JsonMap::new();
    outcome.insert("actions".into(), sorted(&actions));
    outcome.insert("rejections".into(), sorted(&rejections));
    outcome.insert("preconditions".into(), sorted(&preconditions));
    outcome
}

/// `type(value) is int`: a JSON integer, never a boolean or a float.
pub(super) fn is_int(value: &Json) -> bool {
    matches!(value, Json::Number(number) if number.is_i64() || number.is_u64())
}

/// `summarize_coverage(required, results, scenarios, *, required_rejections=None,
/// required_preconditions=None, requires_handoff=False)`: a coverage label only counts after
/// its scenario successfully checks the action.
///
/// # Panics
///
/// When `results` and `scenarios` differ in length: Python's `zip(..., strict=True)` raises
/// `ValueError`, a fault no caller reaches (one result per scenario).
pub fn summarize_coverage(
    required: &BTreeSet<String>,
    results: &[JsonMap],
    scenarios: &[JsonMap],
    required_rejections: Option<&BTreeSet<String>>,
    required_preconditions: Option<&BTreeSet<String>>,
    requires_handoff: bool,
) -> JsonMap {
    assert_eq!(results.len(), scenarios.len(), "summarize_coverage: one result per scenario");
    let mut checked: BTreeSet<String> = BTreeSet::new();
    let mut rejected: BTreeSet<String> = BTreeSet::new();
    let mut unproven: BTreeSet<String> = BTreeSet::new();
    let mut interactions: Vec<Json> = Vec::new();
    let mut preconditions: BTreeSet<String> = BTreeSet::new();
    let mut handoffs: Vec<Json> = Vec::new();
    for (scenario, result) in scenarios.iter().zip(results) {
        let declared: BTreeSet<String> = strings(scenario.get("covers")).map(str::to_owned).collect();
        let mut observed: BTreeSet<String> = BTreeSet::new();
        let mut observed_rejections: BTreeSet<String> = BTreeSet::new();
        let mut phases: BTreeSet<String> = BTreeSet::new();
        if result.get("status").and_then(Json::as_str) == Some("passed") {
            for step in result.get("steps").and_then(Json::as_array).into_iter().flatten() {
                let coverage = step.get("coverage");
                let list = |key: &str| strings(coverage.and_then(|coverage| coverage.get(key)));
                observed.extend(list("actions").map(str::to_owned));
                observed_rejections.extend(list("rejections").map(str::to_owned));
                preconditions
                    .extend(list("preconditions").filter(|name| declared.contains(*name)).map(str::to_owned));
                phases.extend(list("handoff_phases").map(str::to_owned));
            }
        }
        let id = scenario.get("id").cloned().unwrap_or(Json::Null);
        if phases.contains("held") && phases.contains("released") {
            handoffs.push(id.clone());
        }
        rejected.extend(observed_rejections.intersection(&declared).cloned());
        checked.extend(observed.intersection(&declared).cloned());
        if observed.iter().filter(|name| declared.contains(*name) && required.contains(*name)).count() > 1 {
            interactions.push(id);
        }
        unproven.extend(
            declared
                .iter()
                .filter(|name| !observed.contains(*name) && !observed_rejections.contains(*name))
                .cloned(),
        );
    }
    let none = BTreeSet::new();
    let required_rejections = required_rejections.unwrap_or(&none);
    let required_preconditions = required_preconditions.unwrap_or(&none);
    let missing_interaction = required.len() > 1 && interactions.is_empty();
    let missing_handoff = requires_handoff && handoffs.is_empty();
    let mut summary = JsonMap::new();
    summary.insert("required_actions".into(), sorted(required));
    summary.insert("checked_actions".into(), sorted(checked.intersection(required)));
    summary.insert("checked_rejections".into(), sorted(rejected.intersection(required)));
    summary.insert("missing_actions".into(), sorted(required.difference(&checked)));
    summary.insert("unproven_claims".into(), sorted(&unproven));
    summary.insert("missing_rejections".into(), sorted(required_rejections.difference(&rejected)));
    summary.insert("interaction_scenarios".into(), Json::Array(interactions));
    summary.insert("missing_interaction".into(), Json::Bool(missing_interaction));
    summary.insert("missing_preconditions".into(), sorted(required_preconditions.difference(&preconditions)));
    summary.insert("handoff_scenarios".into(), Json::Array(handoffs));
    summary.insert("missing_handoff".into(), Json::Bool(missing_handoff));
    summary
}

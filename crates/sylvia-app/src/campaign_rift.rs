//! `campaign/rift.py`: declarative selection and materialization for The Rift Awakening
//! campaign. Party templates are Python's plain JSON values (`dict[str, Any]`), read with
//! Python's own failures for a malformed field.
//!
//! In `sylvia-app` rather than `sylvia-domain`: it reads the bundled Rift Plan and template and
//! runs the initial character state check and the starting world, which live here.
//!
//! The shipped template is Python's `_template()` (`importlib.resources`), cached per
//! [`BundledPackages`] from `BundledSources::rift_party_template`.

use crate::bundled_packages::BundledPackages;
use crate::campaign_starting_world::materialize_starting_world;
use crate::initial_character_state::verify_initial_character_state;
use crate::reusable_participants::ReusableParticipant;
use crate::setting_character_requirements::validate_setting_character_data;
use serde_json::json;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, Participant, ParticipantFields, StateValue};
use sylvia_domain::campaign_plan::{parse_starting_party_policy, StartingPartyPolicy, ValidatedCampaignPlan};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::ParticipantArtifactDeclaration;
use sylvia_domain::pyops::{py_contains, py_eq, py_item, py_iter, py_require_hashable, py_str, py_truthy};
use sylvia_domain::schema_admission::validate_parameters;

pub const RIFT_TEMPLATE_ID: &str = "the-rift-awakening";
pub const RIFT_RULESET_ID: &str = "sylvia.srd-lite.bg3";
pub const RIFT_RULESET_COMPANION_MAXIMUM: i64 = 5;

/// `RiftSelection`: one validated Rift setup ready for ordinary campaign construction.
#[derive(Debug, Clone, PartialEq)]
pub struct RiftSelection {
    pub participants: Vec<Participant>,
    pub initial_state: Vec<StateValue>,
    pub canonical_input: JsonMap,
    pub campaign_plan: JsonMap,
    pub campaign_plan_markdown: String,
}

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

fn rejected_with(message: &str, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

fn runtime_error(message: &str) -> DomainError {
    DomainError::uncaught("RuntimeError", message)
}

/// `value.get(key)` on a template record, which is always a mapping here.
fn get<'a>(value: &'a Json, key: &str) -> Option<&'a Json> {
    value.as_object().and_then(|map| map.get(key))
}

static NULL: Json = Json::Null;

fn null() -> &'static Json {
    &NULL
}

/// `validate_rift_party_handoff(selection, starting_party_policy)`: recheck the final
/// materialized roster immediately before owner handoff.
pub fn validate_rift_party_handoff(
    selection: &RiftSelection,
    starting_party_policy: &Json,
) -> DomainResult<()> {
    let policy = parse_starting_party_policy(starting_party_policy)?;
    let role_count = |role: &str| {
        selection
            .participants
            .iter()
            .filter(|participant| {
                participant.data().get("party_role").is_some_and(|value| py_eq(value, &json!(role)))
            })
            .count() as i64
    };
    let players = role_count("player_character");
    let companions = role_count("companion");
    let empty = Json::Array(Vec::new());
    let selected_party = selection.canonical_input.get("selected_party").unwrap_or(&empty);
    let Json::Array(selected_party) = selected_party else {
        return Err(rejected("The materialized Rift party record is invalid"));
    };
    let required_count = selected_party
        .iter()
        .filter(|item| {
            item.as_object().is_some_and(|item| {
                item.get("party_role").is_some_and(|role| py_eq(role, &json!("companion")))
                    && item.get("required") == Some(&Json::Bool(true))
            })
        })
        .count() as i64;
    let custom_count = companions - required_count;
    if players != 1
        || companions > RIFT_RULESET_COMPANION_MAXIMUM
        || required_count != policy.required_companions.len() as i64
        || !(policy.custom_companion_minimum <= custom_count
            && custom_count <= policy.custom_companion_maximum)
    {
        return Err(rejected_with(
            "The final party does not satisfy the Campaign Plan starting-party policy",
            "campaign_plan_party_handoff_invalid",
        ));
    }
    Ok(())
}

/// `len(set(values))`, with Python's `TypeError` for an unhashable value.
fn distinct_count(values: &[&Json]) -> DomainResult<usize> {
    let mut seen: Vec<&Json> = Vec::new();
    for value in values {
        py_require_hashable(value)?;
        if !seen.iter().any(|item| py_eq(item, value)) {
            seen.push(value);
        }
    }
    Ok(seen.len())
}

/// `validate_rift_template(value)`: an authored Rift party template loaded from the selected
/// Setting copy.
pub fn validate_rift_template(value: &Json) -> DomainResult<Json> {
    const REQUIRED: [&str; 7] = [
        "format",
        "template_id",
        "title",
        "description",
        "player_archetypes",
        "companions",
        "encounter_entities",
    ];
    let Json::Object(map) = value else {
        return Err(DomainError::value("The Rift template has an invalid top-level shape"));
    };
    if map.len() != REQUIRED.len() || !REQUIRED.iter().all(|key| map.contains_key(*key)) {
        return Err(DomainError::value("The Rift template has an invalid top-level shape"));
    }
    if !py_eq(&map["format"], &json!(2)) || !py_eq(&map["template_id"], &json!(RIFT_TEMPLATE_ID)) {
        return Err(DomainError::value("The Rift template identity is invalid"));
    }
    let (Json::Array(archetypes), Json::Array(companions)) = (&map["player_archetypes"], &map["companions"])
    else {
        return Err(DomainError::value("The Rift template content is invalid"));
    };
    if archetypes.is_empty() || companions.len() > 5 {
        return Err(DomainError::value("The Rift template content is invalid"));
    }
    for (collection, label) in [(archetypes, "archetype"), (companions, "companion")] {
        let identities: Vec<&Json> = collection
            .iter()
            .filter_map(Json::as_object)
            .map(|item| item.get("id").unwrap_or(null()))
            .collect();
        if identities.len() != collection.len() || distinct_count(&identities)? != identities.len() {
            return Err(DomainError::value(format!("The Rift {label} identities are invalid")));
        }
    }
    Ok(value.clone())
}

impl BundledPackages {
    /// `_template()`: the shipped template, validated once (a failure is raised again).
    fn rift_template(&self) -> DomainResult<&Json> {
        if let Some(template) = self.rift_template.get() {
            return Ok(template);
        }
        let text = crate::reusable_plans::read_text(&self.sources.rift_party_template)?;
        let value = validate_rift_template(&crate::reusable_plans::loads(&text)?)?;
        Ok(self.rift_template.get_or_init(|| value))
    }

    /// `bundled_rift_template()`: the shipped party template, for Plan copies saved before their
    /// Setting carried one; the caller's copy.
    pub fn bundled_rift_template(&self) -> DomainResult<Json> {
        self.rift_template().cloned()
    }
}

/// The template a call uses: the shipped one, or `validate_rift_template(template)`.
fn template_value(bundled: &BundledPackages, template: Option<&Json>) -> DomainResult<Json> {
    match template {
        None => bundled.bundled_rift_template(),
        Some(template) => validate_rift_template(template),
    }
}

/// `rift_public_template(reusable_participants=..., template=...)`: path-free setup metadata safe
/// for the desktop renderer.
pub fn rift_public_template(
    bundled: &BundledPackages,
    reusable_participants: &[ReusableParticipant],
    template: Option<&Json>,
) -> DomainResult<JsonMap> {
    let value = template_value(bundled, template)?;
    let mut local_players = Vec::new();
    let mut local_companions = Vec::new();
    for participant in reusable_participants {
        let mut row = participant.catalog_payload();
        if participant.role == "player_character" {
            if participant.builtin_starter {
                row.insert("starter_preview".into(), Json::Object(participant.rift_source()));
            }
            local_players.push(row);
        } else if participant.role == "companion" {
            local_companions.push(row);
        }
    }
    let local = |item: &JsonMap, player: bool| -> JsonMap {
        let mut row = JsonMap::new();
        row.insert("id".into(), item["id"].clone());
        row.insert("name".into(), item["name"].clone());
        if player {
            row.insert("label".into(), item["name"].clone());
        }
        row.insert("summary".into(), item["summary"].clone());
        row.insert("reusable".into(), Json::Bool(true));
        row.insert("profile_status".into(), item["profile_status"].clone());
        row.insert("campaign_ready".into(), Json::Bool(py_eq(&item["profile_status"], &json!("authored"))));
        if player && item.get("builtin_starter").is_some_and(py_truthy) {
            row.insert("builtin_starter".into(), Json::Bool(true));
            row.insert("starter_preview".into(), item["starter_preview"].clone());
            row.insert("content_sha256".into(), item["content_sha256"].clone());
        }
        if let Some(profile) = item.get("source_profile").filter(|profile| py_truthy(profile)) {
            row.insert("source_profile".into(), profile.clone());
        }
        row
    };
    let mut archetypes = Vec::new();
    for item in value["player_archetypes"].as_array().expect("validated") {
        let mut row = JsonMap::new();
        row.insert("id".into(), py_item(item, "id")?.clone());
        row.insert("name".into(), py_item(py_item(item, "participant")?, "name")?.clone());
        row.insert("label".into(), py_item(item, "label")?.clone());
        row.insert("summary".into(), py_item(item, "summary")?.clone());
        row.insert("source_ref".into(), get(item, "source_ref").cloned().unwrap_or(Json::Null));
        archetypes.push(Json::Object(row));
    }
    archetypes.extend(local_players.iter().map(|item| Json::Object(local(item, true))));
    let mut companions = Vec::new();
    for item in value["companions"].as_array().expect("validated") {
        let mut row = JsonMap::new();
        row.insert("id".into(), py_item(item, "id")?.clone());
        row.insert("name".into(), py_item(item, "name")?.clone());
        row.insert("summary".into(), py_item(item, "summary")?.clone());
        row.insert("source_ref".into(), get(item, "source_ref").cloned().unwrap_or(Json::Null));
        companions.push(Json::Object(row));
    }
    companions.extend(local_companions.iter().map(|item| Json::Object(local(item, false))));
    let mut public = JsonMap::new();
    public.insert("template_id".into(), value["template_id"].clone());
    public.insert("title".into(), value["title"].clone());
    public.insert("description".into(), value["description"].clone());
    public.insert("player_archetypes".into(), Json::Array(archetypes));
    public.insert("companions".into(), Json::Array(companions));
    Ok(public)
}

/// `_selected_record(collection, identity, label)`.
fn selected_record<'a>(collection: &'a [Json], identity: &str, label: &str) -> DomainResult<&'a Json> {
    for item in collection {
        if py_eq(py_item(item, "id")?, &json!(identity)) {
            return Ok(item);
        }
    }
    Err(rejected(&format!("The Rift {label} selection is unavailable")))
}

/// `_character_file_record(source)`: an exact Character File source, kept out of the setup
/// catalog.
fn character_file_record(source: &Json) -> DomainResult<Json> {
    let source_id = get(source, "id").unwrap_or(null());
    let authored_profile = get(source, "authored_profile").unwrap_or(null());
    let sparse = json!("sparse");
    let profile_status = get(source, "profile_status").unwrap_or(&sparse);
    let invalid = || runtime_error("The Rift Character File source is invalid");
    if !source_id.as_str().is_some_and(|id| !id.is_empty()) {
        return Err(invalid());
    }
    // `profile_status not in {"authored", "sparse"}` hashes the value.
    py_require_hashable(profile_status)?;
    let authored = py_eq(profile_status, &json!("authored"));
    if !(authored || py_eq(profile_status, &sparse))
        || (authored && !authored_profile.is_string())
        || (py_eq(profile_status, &sparse) && !authored_profile.is_null())
    {
        return Err(invalid());
    }
    Ok(json!({
        "source_id": source_id,
        "profile_status": profile_status,
        "authored_profile": authored_profile,
        "setting_data": get(source, "setting_data").cloned().unwrap_or_else(|| json!({})),
    }))
}

/// `_require_authored_reusable_profile(source)`: keep an incomplete legacy Character File out of
/// a newly constructed campaign.
fn require_authored_reusable_profile(source: &Json) -> DomainResult<()> {
    if get(source, "reusable") != Some(&Json::Bool(true))
        || get(source, "profile_status").is_some_and(|status| py_eq(status, &json!("authored")))
    {
        return Ok(());
    }
    let name = match get(source, "name").filter(|name| py_truthy(name)) {
        Some(name) => py_str(name),
        None => "This character".to_owned(),
    };
    Err(rejected_with(
        &format!(
            "{name}'s Character File needs an authored profile before it can join a new campaign. \
             Complete the Character File or choose someone else."
        ),
        "character_file_profile_required",
    ))
}

/// `_record_for_plan_reference(collection, identity=..., path=..., label=...)`: one exact Setting
/// reference, without treating reusable artifacts as aliases.
fn record_for_plan_reference<'a>(
    collection: &'a [Json],
    identity: &str,
    path: &str,
    label: &str,
) -> DomainResult<&'a Json> {
    collection
        .iter()
        .find(|item| {
            get(item, "source_ref").and_then(Json::as_object).is_some_and(|reference| {
                reference.get("id").is_some_and(|id| py_eq(id, &json!(identity)))
                    && reference.get("path").is_some_and(|value| py_eq(value, &json!(path)))
            })
        })
        .ok_or_else(|| rejected(&format!("The Campaign Plan's fixed {label} is unavailable")))
}

/// `_validated_party_selection(...)`: the Plan policy inside the Ruleset's admitted Rift party
/// envelope.
fn validated_party_selection<'a>(
    policy: &StartingPartyPolicy,
    archetypes: &'a [Json],
    companions: &'a [Json],
    archetype_id: &str,
    companion_ids: &[String],
) -> DomainResult<(&'a Json, Vec<&'a Json>, Vec<String>)> {
    if policy.required_companions.len() as i64 + policy.custom_companion_maximum
        > RIFT_RULESET_COMPANION_MAXIMUM
    {
        return Err(rejected_with(
            "The Campaign Plan starting party exceeds the Ruleset companion envelope",
            "campaign_plan_party_exceeds_ruleset",
        ));
    }
    let archetype = if policy.player_character_mode == "fixed" {
        let fixed = policy
            .fixed_player_character
            .as_ref()
            .ok_or_else(|| rejected("The Campaign Plan fixed player character is invalid"))?;
        let archetype =
            record_for_plan_reference(archetypes, &fixed.character_id, &fixed.path, "player character")?;
        if !py_eq(&json!(archetype_id), py_item(archetype, "id")?) {
            return Err(rejected_with(
                "The Campaign Plan requires its fixed player character",
                "campaign_plan_fixed_player_required",
            ));
        }
        archetype
    } else {
        selected_record(archetypes, archetype_id, "archetype")?
    };
    let mut required_records = Vec::new();
    for reference in &policy.required_companions {
        required_records.push(record_for_plan_reference(
            companions,
            &reference.character_id,
            &reference.path,
            "companion",
        )?);
    }
    let mut required_ids: Vec<String> = Vec::new();
    for record in required_records {
        let id = py_str(py_item(record, "id")?);
        if !required_ids.contains(&id) {
            required_ids.push(id);
        }
    }
    let mut selected_ids: Vec<&String> = Vec::new();
    for id in companion_ids {
        if !selected_ids.contains(&id) {
            selected_ids.push(id);
        }
    }
    if !required_ids.iter().all(|id| selected_ids.contains(&id)) {
        return Err(rejected_with(
            "The Campaign Plan requires every mandatory companion",
            "campaign_plan_required_companion_missing",
        ));
    }
    let custom_count = selected_ids.iter().filter(|id| !required_ids.contains(**id)).count() as i64;
    if !(policy.custom_companion_minimum <= custom_count && custom_count <= policy.custom_companion_maximum) {
        return Err(rejected_with(
            "The selected custom companion count is outside the Campaign Plan policy",
            "campaign_plan_custom_companion_count_invalid",
        ));
    }
    let mut selected = Vec::new();
    for identity in companion_ids {
        selected.push(selected_record(companions, identity, "Companion")?);
    }
    Ok((archetype, selected, required_ids))
}

/// `setting_data` as Python passes it on: a missing or `None` value is no data.
fn setting_data_of(source: &Json) -> Option<&Json> {
    match get(source, "setting_data") {
        None | Some(Json::Null) => None,
        Some(value) => Some(value),
    }
}

/// `_participant(participant_id=..., party_role=..., source=..., package=..., declaration=...)`.
fn participant(
    participant_id: String,
    party_role: &str,
    source: &Json,
    package: &PackageLockEntry,
    declaration: &ParticipantArtifactDeclaration,
) -> DomainResult<(Participant, Json)> {
    let data = py_item(source, "participant")?.clone();
    let state = py_item(source, "entity_state")?.clone();
    let (Json::Object(mut data), Json::Object(state)) = (data, state) else {
        return Err(runtime_error("The Rift participant declaration is invalid"));
    };
    data.insert("party_role".into(), json!(party_role));
    validate_parameters(&data, &declaration.schema)?;
    let state = match &declaration.entity_state_schema {
        Some(schema) => {
            let calculations: Vec<Json> =
                declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
            let validation = declaration.initial_state_validation.clone().map(Json::Object);
            verify_initial_character_state(
                &Json::Object(data.clone()),
                &state,
                schema,
                &calculations,
                validation.as_ref(),
                setting_data_of(source),
            )?
        }
        None => state,
    };
    let participant = Participant::new(ParticipantFields {
        participant_id,
        kind: declaration.kind.clone(),
        package_id: package.package_id().to_owned(),
        package_version: package.version().to_owned(),
        data,
        package_sha256: Some(package.sha256().to_owned()),
        schema_id: Some(declaration.schema_id.clone()),
        schema_sha256: Some(declaration.schema_sha256.clone()),
        validation_status: "validated".into(),
        adaptation_id: None,
    })?;
    Ok((participant, Json::Object(state)))
}

/// `dict.update(other)` with Python's failures for a value that is not a mapping. A pair with a
/// key that is not text cannot be held in a JSON object and is not yet ported.
fn dict_update(target: &mut JsonMap, other: &Json) -> DomainResult<()> {
    if let Json::Object(map) = other {
        for (key, value) in map {
            target.insert(key.clone(), value.clone());
        }
        return Ok(());
    }
    for (index, element) in py_iter(other)?.into_iter().enumerate() {
        let pair = match &element {
            Json::Null | Json::Bool(_) | Json::Number(_) => {
                return Err(DomainError::type_error(format!(
                    "cannot convert dictionary update sequence element #{index} to a sequence"
                )))
            }
            other => py_iter(other)?,
        };
        if pair.len() != 2 {
            return Err(DomainError::value(format!(
                "dictionary update sequence element #{index} has length {}; 2 is required",
                pair.len()
            )));
        }
        py_require_hashable(&pair[0])?;
        let Json::String(key) = &pair[0] else {
            return Err(DomainError::command_rejected_with(
                "A non-text Rift encounter key is not yet ported",
                "rust_port_pending",
                Vec::new(),
            ));
        };
        target.insert(key.clone(), pair[1].clone());
    }
    Ok(())
}

/// The Plan policy `materialize_rift_selection` applies.
#[derive(Debug, Clone, Copy)]
pub enum RiftPartyPolicy<'a> {
    /// `None`: the Ruleset's compatible envelope.
    Default,
    /// A `starting_party` payload, parsed here.
    Payload(&'a Json),
    /// An already parsed `StartingPartyPolicy`.
    Parsed(&'a StartingPartyPolicy),
}

/// The arguments of `materialize_rift_selection(...)`.
#[derive(Debug, Clone, Copy)]
pub struct RiftChoices<'a> {
    pub participant_namespace: &'a str,
    pub lane: &'a str,
    pub archetype_id: &'a str,
    pub companion_ids: &'a [String],
    pub customization_notes: &'a str,
    pub package: &'a PackageLockEntry,
    pub participant_declaration: &'a ParticipantArtifactDeclaration,
    pub starting_party_policy: RiftPartyPolicy<'a>,
    pub reusable_participants: &'a [ReusableParticipant],
    pub template: Option<&'a Json>,
    pub selected_plan: Option<&'a ValidatedCampaignPlan>,
    pub setting_character_schema: Option<&'a JsonMap>,
}

/// `materialize_rift_selection(...)`: validate setup choices and render package-bound
/// participants plus the authored plan.
pub fn materialize_rift_selection(
    bundled: &BundledPackages,
    choices: &RiftChoices<'_>,
) -> DomainResult<RiftSelection> {
    let namespace = choices.participant_namespace;
    if namespace.chars().count() != 24 || !namespace.chars().all(|c| "0123456789abcdef".contains(c)) {
        return Err(rejected("The Rift participant namespace is invalid"));
    }
    if choices.lane != "quick_start" && choices.lane != "customize" {
        return Err(rejected("The Rift construction lane is unsupported"));
    }
    if choices.package.package_id() != RIFT_RULESET_ID
        || choices.participant_declaration.kind != "player_character"
    {
        return Err(rejected("The Rift requires the bundled D&D Player Character schema"));
    }
    let companion_ids = choices.companion_ids;
    let unique = companion_ids.iter().enumerate().all(|(index, id)| !companion_ids[..index].contains(id));
    if companion_ids.len() as i64 > RIFT_RULESET_COMPANION_MAXIMUM || !unique {
        return Err(rejected("The Rift accepts zero to five unique Companions"));
    }
    let notes = choices.customization_notes;
    if notes.chars().count() > 4_000 {
        return Err(rejected("The Rift customization notes are invalid"));
    }
    let notes = pystr::strip(notes);
    if choices.lane == "quick_start" && !notes.is_empty() {
        return Err(rejected("Quick Start cannot rewrite the authored campaign plan"));
    }
    let value = template_value(bundled, choices.template)?;
    let sources = |role: &str| -> Vec<Json> {
        choices
            .reusable_participants
            .iter()
            .filter(|participant| participant.role == role)
            .map(|participant| Json::Object(participant.rift_source()))
            .collect()
    };
    let mut archetypes = value["player_archetypes"].as_array().expect("validated").clone();
    archetypes.extend(sources("player_character"));
    let mut companions = value["companions"].as_array().expect("validated").clone();
    companions.extend(sources("companion"));
    let distinct = |collection: &[Json]| -> DomainResult<bool> {
        let mut ids: Vec<String> = Vec::new();
        for item in collection {
            let id = py_str(py_item(item, "id")?);
            if !ids.contains(&id) {
                ids.push(id);
            }
        }
        Ok(ids.len() == collection.len())
    };
    if !distinct(&archetypes)? || !distinct(&companions)? {
        return Err(rejected("The Rift participant library has conflicting identities"));
    }
    let parsed;
    let policy = match choices.starting_party_policy {
        RiftPartyPolicy::Parsed(policy) => policy,
        RiftPartyPolicy::Payload(payload) => {
            parsed = parse_starting_party_policy(payload)?;
            &parsed
        }
        RiftPartyPolicy::Default => {
            parsed = parse_starting_party_policy(&json!({
                "player_character": {"mode": "compatible"},
                "required_companions": [],
                "custom_companions": {"minimum": 0, "maximum": RIFT_RULESET_COMPANION_MAXIMUM},
            }))?;
            &parsed
        }
    };
    let (archetype, selected_companions, required_ids) =
        validated_party_selection(policy, &archetypes, &companions, choices.archetype_id, companion_ids)?;
    let schema = choices.setting_character_schema;
    let empty = json!({});
    require_authored_reusable_profile(archetype)?;
    validate_setting_character_data(get(archetype, "setting_data").unwrap_or(&empty), schema)?;
    for companion in &selected_companions {
        require_authored_reusable_profile(companion)?;
        validate_setting_character_data(get(companion, "setting_data").unwrap_or(&empty), schema)?;
    }
    let package = choices.package;
    let declaration = choices.participant_declaration;
    let (player, player_state) = participant(
        format!("rift-{namespace}-player-character"),
        "player_character",
        archetype,
        package,
        declaration,
    )?;
    let player_name = py_str(py_item(&Json::Object(player.data().clone()), "name")?);
    let mut entities = JsonMap::new();
    entities.insert(player.participant_id().to_owned(), player_state);
    let mut participants = vec![player];
    for companion in &selected_companions {
        let participant_id = format!("rift-{namespace}-companion-{}", py_str(py_item(companion, "id")?));
        let (made, state) =
            participant(participant_id.clone(), "companion", companion, package, declaration)?;
        participants.push(made);
        entities.insert(participant_id, state);
    }
    let bundled_plan;
    let plan_artifact = match choices.selected_plan {
        Some(plan) => plan,
        None => {
            bundled_plan = bundled.bundled_rift_campaign_plan()?;
            &bundled_plan
        }
    };
    let encounters = &value["encounter_entities"];
    dict_update(&mut entities, encounters)?;
    // A new start uses the Plan's declared encounter states exactly. Older templates supply only
    // fallback states, including intentionally missing fields.
    for row in plan_artifact.payload["initial_state"]["entities"].as_array().into_iter().flatten() {
        if py_contains(&row["id"], encounters)? {
            entities.insert(py_str(&row["id"]), row["state"].clone());
        }
    }
    let mut markdown = plan_artifact.readable_markdown.clone();
    let mut roster = vec![player_name.clone()];
    for companion in &selected_companions {
        roster.push(py_str(py_item(companion, "name")?));
    }
    markdown.push_str(&format!("\n\n## Selected campaign party\n\n{}\n", roster.join(", ")));
    let customized = choices.lane == "customize" && !notes.is_empty();
    if customized {
        markdown.push_str(&format!("\n\n## Player customization brief\n\n{notes}\n"));
    }
    let mut selected_party = vec![json!({
        "participant_id": participants[0].participant_id(),
        "party_role": "player_character",
        "name": player_name,
        "summary": py_str(py_item(archetype, "summary")?),
        "character_file": character_file_record(archetype)?,
    })];
    for (made, companion) in participants[1..].iter().zip(&selected_companions) {
        let mut row = json!({
            "participant_id": made.participant_id(),
            "party_role": "companion",
            "name": py_str(py_item(companion, "name")?),
            "summary": py_str(py_item(companion, "summary")?),
            "character_file": character_file_record(companion)?,
        });
        if required_ids.contains(&py_str(py_item(companion, "id")?)) {
            row["required"] = Json::Bool(true);
        }
        selected_party.push(row);
    }
    let Json::Object(mut canonical_input) = json!({
        "template_id": RIFT_TEMPLATE_ID,
        "template_version": value["format"],
        "player_character": {"name": player_name, "archetype_id": choices.archetype_id},
        "companion_ids": companion_ids,
        "starting_party_policy": policy.as_payload(),
        "selected_party": selected_party,
    }) else {
        unreachable!("an object")
    };
    if customized {
        canonical_input.insert("customization_notes".into(), json!(notes));
    }
    let initial_state = materialize_starting_world(
        plan_artifact,
        Some(&json!({"namespace": RIFT_RULESET_ID, "key": "entities"})),
        &Json::Object(entities),
        None,
    )?;
    let selection = RiftSelection {
        participants,
        initial_state,
        canonical_input,
        campaign_plan: plan_artifact.payload.clone(),
        campaign_plan_markdown: markdown,
    };
    validate_rift_party_handoff(&selection, &Json::Object(policy.as_payload()))?;
    Ok(selection)
}

//! Compare sylvia-domain's manifests, declarations and closures with the frozen Python.
//!
//! Each case holds Python's outcome: `["ok", sha256]` of the result's field-by-field projection
//! (`json.dumps` of the dataclass fields in declaration order), or the exception class and
//! message. Regenerate with `tests/vectors/generate_manifest_vectors.py`.

use serde_json::json;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{PackageLock, PackageRole};
use sylvia_domain::package_library::{
    campaign_setting_entries, closure_sha256, content_root_for_record, manifest_from_record,
    parse_campaign_setting_knowledge_bytes, parse_content_declarations_bytes, parse_gameplay_support_bytes,
    resolve_exact_closure, resolve_package_graph, validate_campaign_setting_knowledge_links,
    validate_content_declaration_links, CampaignSettingKnowledge, ContentSourceLocator,
    ExactPackageDependency, GameplaySupport, PackageContentDeclarations, PackageGraphNode, PackageManifest,
    SurfaceDeclaration,
};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/manifest_vectors.json")).expect("vector file is valid JSON")
}

fn shipped() -> JsonMap {
    let path =
        concat!(env!("CARGO_MANIFEST_DIR"), "/../sylvia-compare/recordings/windows/startup.recording.json");
    let recording = pyjson::loads(&std::fs::read_to_string(path).unwrap()).unwrap();
    recording["database"]["tables"]["package_manifests"]["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            (row["content_sha256"].as_str().unwrap().to_owned(), row["canonical_json"]["$json"].clone())
        })
        .collect()
}

fn digest(value: &Json) -> String {
    sha256_hex(pyjson::dumps(value, Dump::DEFAULT))
}

fn strings(values: &[String]) -> Json {
    Json::Array(values.iter().map(|v| Json::from(v.as_str())).collect())
}

fn object(map: &JsonMap) -> Json {
    Json::Object(map.clone())
}

fn optional<T>(value: &Option<T>, project: impl Fn(&T) -> Json) -> Json {
    value.as_ref().map(project).unwrap_or(Json::Null)
}

fn exact(dependency: &ExactPackageDependency) -> Json {
    json!({"namespace": dependency.namespace(), "kind": dependency.kind(), "package_id": dependency.package_id(),
           "version": dependency.version(), "content_sha256": dependency.content_sha256()})
}

fn surface(s: &SurfaceDeclaration) -> Json {
    json!({"identity": s.identity, "capability": s.capability, "title": s.title, "icon": s.icon,
           "renderer": s.renderer, "source": optional(&s.source, object), "group": s.group, "order": s.order,
           "default_size": s.default_size, "minimum_size": s.minimum_size,
           "default_placement": s.default_placement, "allowed_placements": strings(&s.allowed_placements),
           "actions": s.actions.iter().map(object).collect::<Vec<_>>()})
}

fn manifest(m: &PackageManifest) -> Json {
    json!({
        "package_id": m.package_id, "version": m.version, "role": m.role.as_str(),
        "content_sha256": m.content_sha256, "canonical_json": m.canonical_json,
        "metadata": object(&m.metadata), "provenance": object(&m.provenance),
        "compatibility": object(&m.compatibility),
        "minimum_model_capability_tier": m.minimum_model_capability_tier,
        "dependencies": m.dependencies.iter().map(exact).collect::<Vec<_>>(),
        "capabilities": strings(&m.capabilities),
        "tools": m.tools.iter().map(|t| json!({
            "name": t.name, "capability": t.capability, "input_schema": object(&t.input_schema),
            "state_predicates": t.state_predicates.iter().map(|p| json!({
                "namespace": p.namespace, "key": p.key, "equals": p.equals, "negated": p.negated})).collect::<Vec<_>>(),
            "turn_statuses": strings(&t.turn_statuses), "writers": strings(&t.writers)})).collect::<Vec<_>>(),
        "prompt_fragments": m.prompt_fragments.iter().map(surface).collect::<Vec<_>>(),
        "ui_surfaces": m.ui_surfaces.iter().map(surface).collect::<Vec<_>>(),
        "files": m.files.iter().map(|f| json!({"path": f.path(), "byte_count": f.byte_count(), "sha256": f.sha256()}))
            .collect::<Vec<_>>(),
        "format_version": m.format_version,
        "participant_artifacts": m.participant_artifacts.iter().map(|a| json!({
            "kind": a.kind, "schema_id": a.schema_id, "schema_sha256": a.schema_sha256, "schema": object(&a.schema),
            "entity_state_schema": optional(&a.entity_state_schema, object),
            "initial_state_calculations": a.initial_state_calculations.iter().map(object).collect::<Vec<_>>(),
            "initial_state_validation": optional(&a.initial_state_validation, object)})).collect::<Vec<_>>(),
        "content_declarations_path": m.content_declarations_path,
        "state_schemas": m.state_schemas.iter().map(|s| json!({
            "key": s.key, "schema": object(&s.schema),
            "references": s.references.iter().map(|r| json!({
                "source": strings(&r.source), "target_key": r.target_key, "target": strings(&r.target)}))
                .collect::<Vec<_>>(),
            "required": s.required, "visibility": s.visibility})).collect::<Vec<_>>(),
    })
}

fn locator(l: &ContentSourceLocator) -> Json {
    json!({"authority_id": l.authority_id, "source_unit_id": l.source_unit_id, "kind": l.kind, "page": l.page,
           "section_path": strings(&l.section_path), "derived_text_sha256": l.derived_text_sha256,
           "page_range": l.page_range.map(|(a, b)| json!([a, b])), "content_sha256": l.content_sha256})
}

fn declarations(d: &PackageContentDeclarations) -> Json {
    json!({
        "scope_id": d.scope_id, "scope_kind": d.scope_kind, "claim_status": d.claim_status,
        "source_boundary_refs": d.source_boundary_refs.iter().map(|(a, b)| json!([a, b])).collect::<Vec<_>>(),
        "source_authorities": d.source_authorities.iter().map(|a| json!({
            "identity": a.identity, "document_id": a.document_id, "version": a.version, "media_type": a.media_type,
            "content_sha256": a.content_sha256, "license_id": a.license_id})).collect::<Vec<_>>(),
        "inclusions": d.inclusions.iter().map(|i| json!({
            "identity": i.identity, "kind": i.kind, "family": i.family, "disposition": i.disposition,
            "classifications": strings(&i.classifications), "sources": i.sources.iter().map(locator).collect::<Vec<_>>(),
            "links": {"artifacts": strings(&i.links.artifacts), "schemas": strings(&i.links.schemas),
                      "state_namespaces": strings(&i.links.state_namespaces), "mechanics": strings(&i.links.mechanics),
                      "procedures": strings(&i.links.procedures), "capabilities": strings(&i.links.capabilities),
                      "tools": strings(&i.links.tools), "prompt_fragments": strings(&i.links.prompt_fragments),
                      "ui_surfaces": strings(&i.links.ui_surfaces)},
            "test_evidence": strings(&i.test_evidence)})).collect::<Vec<_>>(),
        "exclusions": d.exclusions.iter().map(|e| json!({
            "identity": e.identity, "kind": e.kind, "family": e.family, "reason": e.reason,
            "classifications": strings(&e.classifications), "sources": e.sources.iter().map(locator).collect::<Vec<_>>(),
            "within_declared_scope": e.within_declared_scope})).collect::<Vec<_>>(),
        "canonical_json": d.canonical_json,
    })
}

fn support(s: &GameplaySupport) -> Json {
    json!({"records": s.records.iter().map(|r| json!({
        "identity": r.identity, "reference_availability": r.reference_availability,
        "gameplay_resolution": r.gameplay_resolution, "mechanic_family": r.mechanic_family,
        "deterministic_part": r.deterministic_part, "gm_part": r.gm_part})).collect::<Vec<_>>(),
        "canonical_json": s.canonical_json})
}

fn knowledge(k: &CampaignSettingKnowledge) -> Json {
    json!({"facts": k.facts.iter().map(|f| json!({"identity": f.identity, "fact": f.fact,
        "known_by": strings(&f.known_by), "source_path": f.source_path})).collect::<Vec<_>>(),
        "private_references": strings(&k.private_references), "canonical_json": k.canonical_json})
}

fn lock(l: &PackageLock) -> Json {
    json!({"entries": l.entries().iter().map(|e| json!({
        "package_id": e.package_id(), "version": e.version(), "sha256": e.sha256(), "role": e.role().as_str(),
        "load_order": e.load_order(),
        "dependencies": e.dependencies().iter().map(|d| json!({"package_id": d.package_id(), "version": d.version(),
            "sha256": d.sha256(), "kind": d.kind(), "namespace": d.namespace()})).collect::<Vec<_>>(),
        "capabilities": strings(e.capabilities())})).collect::<Vec<_>>()})
}

/// Rust's outcome in the vector's form. Python's operator exceptions (`TypeError`, `KeyError`,
/// `AttributeError`, `IndexError`) all escape to the owner's internal error, so they compare
/// by class only, as `TypeError`.
fn outcome<T>(result: DomainResult<T>, project: impl Fn(&T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", digest(&project(&value))]),
        Err(DomainError::Type(_)) => json!(["TypeError"]),
        Err(DomainError::Diagnosed { error_type, message, diagnostics }) => {
            json!([
                error_type,
                message,
                diagnostics.iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>()
            ])
        }
        Err(error) => json!([error.python_class(), error.message()]),
    }
}

fn expected(case: &Json) -> Json {
    let raw = &case["outcome"];
    match raw[0].as_str().unwrap() {
        "TypeError" | "KeyError" | "AttributeError" | "IndexError" => json!(["TypeError"]),
        _ => raw.clone(),
    }
}

fn step<'v>(node: &'v mut Json, part: &Json) -> &'v mut Json {
    match part {
        Json::String(key) => &mut node[key.as_str()],
        Json::Number(index) => &mut node[index.as_u64().unwrap() as usize],
        other => panic!("bad path part {other}"),
    }
}

/// The generator's `apply(document, edits)`.
fn apply(base: &Json, edits: &[Json]) -> Json {
    let mut document = base.clone();
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let Some((last, parents)) = path.split_last() else {
            document = value.clone();
            continue;
        };
        let mut parent = &mut document;
        for part in parents {
            parent = step(parent, part);
        }
        match (op, parent) {
            ("delete", Json::Object(map)) => {
                map.shift_remove(last.as_str().unwrap());
            }
            ("delete", Json::Array(items)) => {
                items.remove(last.as_u64().unwrap() as usize);
            }
            ("swap", parent) => step(parent, last).as_array_mut().unwrap().reverse(),
            ("set", Json::Object(map)) => {
                map.insert(last.as_str().unwrap().to_owned(), value.clone());
            }
            ("set", Json::Array(items)) => items[last.as_u64().unwrap() as usize] = value.clone(),
            (other, _) => panic!("bad edit {other}"),
        }
    }
    document
}

fn report(name: &str, failures: Vec<String>, total: usize) {
    assert!(
        failures.is_empty(),
        "{name}: {} of {total} cases differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(3)]
    );
}

fn base_records(vectors: &Json) -> JsonMap {
    let mut records = shipped();
    for (sha, record) in vectors["records"].as_object().unwrap() {
        records.insert(sha.clone(), record.clone());
    }
    records
}

#[test]
fn manifests_match_python() {
    let vectors = vectors();
    let records = base_records(&vectors);
    let cases = vectors["record_cases"].as_array().unwrap();
    assert!(cases.len() > 2000);
    let mut failures = Vec::new();
    for case in cases {
        let mut document = apply(&records[case["base"].as_str().unwrap()], case["edits"].as_array().unwrap());
        if case["rehash"].as_bool().unwrap() {
            let root = content_root_for_record(document.as_object().unwrap());
            document["content_sha256"] = Json::from(root);
        }
        let actual = outcome(manifest_from_record(&document, None), manifest);
        if actual != expected(case) {
            failures.push(format!(
                "{} {}: python {}, rust {actual}",
                case["base"], case["edits"], case["outcome"]
            ));
        }
    }
    report("manifests", failures, cases.len());
}

#[test]
fn declaration_documents_match_python() {
    let vectors = vectors();
    let documents = vectors["documents"].as_object().unwrap();
    let cases = vectors["document_cases"].as_array().unwrap();
    assert!(cases.len() > 2000);
    let mut failures = Vec::new();
    for case in cases {
        let item = &documents[case["base"].as_str().unwrap()];
        let bytes = if let Some(raw) = case["raw"].as_str() {
            raw.as_bytes().to_vec()
        } else if case["edits"].as_array().unwrap().is_empty() {
            item["text"].as_str().unwrap().as_bytes().to_vec()
        } else {
            let parsed = pyjson::loads(item["text"].as_str().unwrap()).unwrap();
            pyjson::canonical_bytes(&apply(&parsed, case["edits"].as_array().unwrap()))
        };
        let actual = match item["kind"].as_str().unwrap() {
            "declarations" => outcome(parse_content_declarations_bytes(&bytes), declarations),
            "support" => outcome(parse_gameplay_support_bytes(&bytes), support),
            _ => outcome(parse_campaign_setting_knowledge_bytes(&bytes), knowledge),
        };
        if actual != expected(case) {
            failures.push(format!(
                "{} {}: python {}, rust {actual}",
                item["kind"], case["edits"], case["outcome"]
            ));
        }
    }
    report("documents", failures, cases.len());
}

#[test]
fn declaration_links_match_python() {
    let vectors = vectors();
    let records = base_records(&vectors);
    let documents = vectors["documents"].as_object().unwrap();
    let cases = vectors["link_cases"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let manifest_value = manifest_from_record(&records[case["record"].as_str().unwrap()], None).unwrap();
        let item = &documents[case["document"].as_str().unwrap()];
        let bytes = item["text"].as_str().unwrap().as_bytes();
        let actual = if item["kind"] == "declarations" {
            outcome(
                validate_content_declaration_links(
                    &manifest_value,
                    &parse_content_declarations_bytes(bytes).unwrap(),
                ),
                |_| Json::Null,
            )
        } else {
            outcome(
                validate_campaign_setting_knowledge_links(
                    &manifest_value,
                    &parse_campaign_setting_knowledge_bytes(bytes).unwrap(),
                ),
                |_| Json::Null,
            )
        };
        if actual != expected(case) {
            failures.push(format!(
                "{} on {}: python {}, rust {actual}",
                case["document"], case["record"], case["outcome"]
            ));
        }
    }
    report("links", failures, cases.len());
}

#[test]
fn package_graphs_resolve_like_python() {
    let vectors = vectors();
    let cases = vectors["graph_cases"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let built: DomainResult<Vec<PackageGraphNode>> = case["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|node| {
                let identity = node["identity"].as_array().unwrap();
                let text = |v: &Json| v.as_str().unwrap().to_owned();
                Ok(PackageGraphNode {
                    identity: (text(&identity[0]), text(&identity[1]), text(&identity[2])),
                    role: PackageRole::parse(node["role"].as_str().unwrap()).unwrap(),
                    format_version: node["format_version"].as_i64().unwrap(),
                    content_kind: (!node["content_kind"].is_null()).then(|| node["content_kind"].clone()),
                    dependencies: node["dependencies"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|d| {
                            ExactPackageDependency::new(
                                text(&d[0]),
                                text(&d[1]),
                                text(&d[2]),
                                text(&d[3]),
                                text(&d[4]),
                            )
                        })
                        .collect::<DomainResult<Vec<_>>>()?,
                })
            })
            .collect();
        let actual = outcome(built.and_then(|nodes| resolve_package_graph(&nodes)), |identities| {
            Json::Array(identities.iter().map(|(a, b, c)| json!([a, b, c])).collect())
        });
        if actual != expected(case) {
            failures.push(format!("{}: python {}, rust {actual}", case["nodes"], case["outcome"]));
        }
    }
    report("graphs", failures, cases.len());
}

#[test]
fn shipped_closures_resolve_like_python() {
    let vectors = vectors();
    let records = shipped();
    for case in vectors["closure_cases"].as_array().unwrap() {
        let manifests: Vec<PackageManifest> = case["records"]
            .as_array()
            .unwrap()
            .iter()
            .map(|sha| manifest_from_record(&records[sha.as_str().unwrap()], None).unwrap())
            .collect();
        let resolved = resolve_exact_closure(&manifests).unwrap();
        assert_eq!(closure_sha256(&resolved), case["closure_sha256"].as_str().unwrap());
        assert_eq!(digest(&lock(&resolved)), case["lock"].as_str().unwrap());
        let settings: Vec<Json> =
            campaign_setting_entries(&resolved).iter().map(|e| Json::from(e.package_id())).collect();
        assert_eq!(Json::Array(settings), case["settings"]);
    }
}

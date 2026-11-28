//! `application/builder_source_selection.py`: let a plain Builder bind a Ruleset and a compatible
//! Setting, retaining exact snapshots (`BuilderSourceSelection`). The selection is usable at once
//! and is recovered later from the turn's saved tool receipt (`launch_references`).

use crate::builder_references::{
    candidate_for_references, capture_content_references, launch_references, reader_for_references,
    BuilderReferences, ContentChoices,
};
use crate::bundled_packages::BundledPackages;
use crate::campaign_library_selection::SelectionOwner;
use crate::package_snapshots::ruleset_copy;
use std::path::{Path, PathBuf};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::package_library::{CandidateStatus, PackageCandidate};
use sylvia_domain::pyops::{py_contains, py_eq, py_item, py_iter};
use sylvia_domain::session::{SessionContext, SessionMessageKind};
use sylvia_domain::DomainError;

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected_with(message, "command_rejected", Vec::new())
}

/// `catalog()`: the content catalog this turn advertised.
pub type Catalog<'a, E> = Box<dyn Fn() -> Result<Vec<JsonMap>, E> + 'a>;

/// `item.get(key, default)`: a present key, even holding `null`, answers its own value.
fn get_or<'a>(item: &'a JsonMap, key: &str, default: &'a Json) -> &'a Json {
    item.get(key).unwrap_or(default)
}

/// `BuilderSourceSelection(owner, storage_root, context, catalog)`.
pub struct BuilderSourceSelection<'a, O: SelectionOwner> {
    owner: &'a O,
    bundled: &'a BundledPackages,
    storage_root: PathBuf,
    catalog: Catalog<'a, O::Error>,
    /// `self.references`: the selected snapshot, recovered from the chat when it has one.
    pub references: Option<JsonMap>,
    /// `self.selectable`: a plain standalone Builder chat with no launch or repair context.
    pub selectable: bool,
}

impl<'a, O: SelectionOwner> BuilderSourceSelection<'a, O> {
    pub fn new(
        owner: &'a O,
        bundled: &'a BundledPackages,
        storage_root: &Path,
        context: &SessionContext,
        catalog: Catalog<'a, O::Error>,
    ) -> Result<Self, O::Error> {
        let references = launch_references(context)?;
        let contexted = context.messages.iter().any(|message| {
            message.kind() == SessionMessageKind::Evidence
                && message
                    .content()
                    .get("kind")
                    .is_some_and(|kind| kind == "builder_launch_context" || kind == "import_repair_context")
        });
        let selectable = context.session.role() == WriterRole::Builder
            && context.session.campaign_id().is_none()
            && !contexted;
        Ok(Self { owner, bundled, storage_root: storage_root.to_path_buf(), catalog, references, selectable })
    }

    /// A captured selection whose candidate is installed and has passed validation.
    fn validated(&self, references: &JsonMap, refusal: &str) -> Result<(), O::Error> {
        let candidate = candidate_for_references(self.owner, Some(references))?;
        let usable = candidate.as_ref().is_some_and(|candidate| {
            matches!(candidate.status(), CandidateStatus::Validated | CandidateStatus::Activated)
        });
        if !usable {
            return Err(rejected(refusal).into());
        }
        Ok(())
    }

    fn answer(references: &JsonMap) -> JsonMap {
        let mut answer = JsonMap::new();
        answer.insert("success".into(), true.into());
        answer.insert("references".into(), Json::Object(references.clone()));
        answer
    }

    /// `select(ruleset_id)`: an advertised Ruleset, never replacing an existing binding.
    pub fn select(&mut self, ruleset_id: &str) -> Result<JsonMap, O::Error> {
        if !self.selectable {
            return Err(rejected("This Builder keeps its original content selection").into());
        }
        let catalog = (self.catalog)()?;
        let wanted = Json::from(ruleset_id);
        let mut advertised = false;
        for item in &catalog {
            if !item.get("kind").is_some_and(|kind| kind == "ruleset") {
                continue;
            }
            let selection = get_or(item, "selection_id", get_or(item, "id", &Json::Null));
            let aliases = get_or(item, "selection_aliases", &Json::Array(Vec::new())).clone();
            if py_eq(&wanted, selection) || py_contains(&wanted, &aliases)? {
                advertised = true;
                break;
            }
        }
        if !advertised {
            return Err(rejected("Choose a Ruleset returned by discover_campaign_content").into());
        }
        let choices = ContentChoices { ruleset_id: wanted, ..ContentChoices::default() };
        let references =
            capture_content_references(self.owner, self.bundled, &self.storage_root, &catalog, &choices)?;
        self.validated(&references, "Selected Builder Ruleset has not passed validation")?;
        if self
            .references
            .as_ref()
            .is_some_and(|current| !py_eq(&Json::Object(current.clone()), &Json::Object(references.clone())))
        {
            return Err(rejected("This Builder already selected a different source snapshot").into());
        }
        let answer = Self::answer(&references);
        self.references = Some(references);
        Ok(answer)
    }

    /// `candidate()`: the exact chosen package closure, for validation and publication.
    pub fn candidate(&self) -> Result<Option<PackageCandidate>, O::Error> {
        candidate_for_references(self.owner, self.references.as_ref())
    }

    /// `select_setting(candidate_id, setting_id)`: a plain chat's Ruleset binding extended with
    /// one explicit compatible Setting snapshot.
    pub fn select_setting(
        &mut self,
        candidate_id: Option<&str>,
        setting_id: &str,
    ) -> Result<JsonMap, O::Error> {
        if !self.selectable {
            return Err(rejected("This Builder keeps its original content selection").into());
        }
        let catalog = (self.catalog)()?;
        let candidate = candidate_id.map_or(Json::Null, Json::from);
        let mut ruleset = None;
        for item in &catalog {
            if !item.get("kind").is_some_and(|kind| kind == "ruleset") {
                continue;
            }
            let settings = py_iter(get_or(item, "compatible_settings", &Json::Array(Vec::new())))?;
            let compatible = settings.iter().any(|setting| {
                setting.get("id").is_some_and(|id| id == setting_id)
                    && py_eq(setting.get("candidate_id").unwrap_or(&Json::Null), &candidate)
            });
            if compatible {
                ruleset = Some(item.clone());
                break;
            }
        }
        let Some(ruleset) = ruleset else {
            return Err(rejected("Choose an exact Setting returned by discover_campaign_content").into());
        };
        let ruleset_id = match ruleset.get("selection_id") {
            Some(selection) => selection.clone(),
            None => py_item(&Json::Object(ruleset.clone()), "id")?.clone(),
        };
        let choices = ContentChoices {
            ruleset_id,
            setting_id: Json::from(setting_id),
            setting_candidate_id: candidate,
            plan_id: Json::Null,
        };
        let references =
            capture_content_references(self.owner, self.bundled, &self.storage_root, &catalog, &choices)?;
        self.validated(&references, "Selected Builder Setting has not passed validation")?;
        if let Some(current) = &self.references {
            let packages = |references: &JsonMap| -> Result<Vec<Json>, DomainError> {
                py_iter(get_or(references, "packages", &Json::Array(Vec::new())))
            };
            let (previous, incoming) = (packages(current)?, packages(&references)?);
            let had_setting =
                previous.iter().any(|item| item.get("prefix").is_some_and(|prefix| prefix == "setting"));
            if had_setting {
                if !py_eq(&Json::Object(current.clone()), &Json::Object(references.clone())) {
                    return Err(rejected("This Builder already selected a different Setting snapshot").into());
                }
            } else {
                // A Ruleset may come from an older Setting closure. Retain its exact mechanics,
                // not that unselected world's files; use the same boundary as Setting publication.
                let source = self.candidate()?.ok_or_else(|| {
                    DomainError::uncaught(
                        "AttributeError",
                        "'NoneType' object has no attribute 'candidate_id'",
                    )
                })?;
                let mut required_ids = Vec::new();
                for draft in ruleset_copy(self.owner, source.candidate_id())? {
                    required_ids.push(py_item(&Json::Object(draft.record), "id")?.clone());
                }
                for item in &previous {
                    let id = py_item(item, "id")?;
                    if required_ids.iter().any(|required| py_eq(required, id))
                        && !incoming.iter().any(|candidate| py_eq(candidate, item))
                    {
                        return Err(
                            rejected("This Builder already selected a different source snapshot").into()
                        );
                    }
                }
            }
        }
        let answer = Self::answer(&references);
        self.references = Some(references);
        Ok(answer)
    }

    /// `reader()`: the selected reference paths, available within the same turn.
    pub fn reader(&self) -> Result<Option<BuilderReferences<'a, O>>, O::Error> {
        reader_for_references(self.owner, self.references.as_ref())
    }
}

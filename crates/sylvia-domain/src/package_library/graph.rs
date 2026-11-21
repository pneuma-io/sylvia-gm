//! Exact package closures: `PackageGraphNode`, `resolve_package_graph`, `resolve_exact_closure`,
//! `campaign_setting_entries` and `manifest_index`.

use super::manifest::PackageManifest;
use super::ExactPackageDependency;
use crate::authority::{PackageDependency, PackageLock, PackageLockEntry, PackageRole};
use crate::error::{DomainError, DomainResult};
use crate::pyops::py_eq;
use std::collections::{HashMap, HashSet};
use sylvia_base::{pyjson, Json};

/// `(package_id, version, content_sha256)`.
pub type PackageIdentity = (String, String, String);

/// Exact dependency semantics without package schemas or content (`PackageGraphNode`).
#[derive(Debug, Clone, PartialEq)]
pub struct PackageGraphNode {
    pub identity: PackageIdentity,
    pub role: PackageRole,
    pub format_version: i64,
    /// `content_kind` of a format-4 record, as stored (Python compares it with `==`).
    pub content_kind: Option<Json>,
    pub dependencies: Vec<ExactPackageDependency>,
}

impl PackageGraphNode {
    /// `PackageGraphNode.from_manifest(manifest)`.
    pub fn from_manifest(manifest: &PackageManifest) -> Self {
        let content_kind = (manifest.format_version == 4).then(|| {
            pyjson::loads(&manifest.canonical_json).expect("a manifest's canonical JSON parses")
                ["content_kind"]
                .clone()
        });
        Self {
            identity: owned(manifest.identity()),
            role: manifest.role,
            format_version: manifest.format_version,
            content_kind,
            dependencies: manifest.dependencies.clone(),
        }
    }
}

fn owned((a, b, c): (&str, &str, &str)) -> PackageIdentity {
    (a.to_owned(), b.to_owned(), c.to_owned())
}

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

fn is_setting(kind: Option<&Json>) -> bool {
    kind.is_some_and(|kind| py_eq(kind, &Json::from("campaign_setting")))
}

/// `resolve_package_graph(nodes)`: current Setting roots and legacy Ruleset roots, by exact
/// dependency links. Returns the identities in execution order.
pub fn resolve_package_graph(nodes: &[PackageGraphNode]) -> DomainResult<Vec<PackageIdentity>> {
    let mut by_identity: HashMap<&PackageIdentity, usize> = HashMap::new();
    for (index, node) in nodes.iter().enumerate() {
        by_identity.insert(&node.identity, index);
    }
    if by_identity.len() != nodes.len() {
        return Err(value("Package identities in one archive must be unique"));
    }
    let primaries: Vec<usize> = (0..nodes.len()).filter(|i| nodes[*i].role == PackageRole::Primary).collect();
    if primaries.len() != 1 {
        return Err(value("An exact closure requires exactly one primary"));
    }
    let primary = primaries[0];
    let modern = nodes.iter().any(|node| node.format_version == 4);
    let mut root = primary;
    let lookup = |dependency: &ExactPackageDependency| -> Option<usize> {
        let (id, version, sha) = dependency.identity();
        by_identity.get(&(id.to_owned(), version.to_owned(), sha.to_owned())).copied()
    };
    if modern {
        if nodes.iter().any(|node| node.format_version != 4) {
            return Err(value("Snapshot packages require one archive format"));
        }
        let settings: Vec<usize> =
            (0..nodes.len()).filter(|i| is_setting(nodes[*i].content_kind.as_ref())).collect();
        if settings.len() > 1 {
            return Err(value("A snapshot contains at most one Campaign Setting"));
        }
        for (index, node) in nodes.iter().enumerate() {
            for dependency in &node.dependencies {
                let target = lookup(dependency);
                if index == primary
                    && (dependency.kind() == "campaign_setting"
                        || dependency.namespace() == "campaign_setting"
                        || target.is_some_and(|t| is_setting(nodes[t].content_kind.as_ref())))
                {
                    return Err(value("A Ruleset must not declare a Campaign Setting"));
                }
                if dependency.kind() == "ruleset_snapshot"
                    && (!is_setting(node.content_kind.as_ref())
                        || dependency.namespace() != "ruleset_snapshot"
                        || target != Some(primary))
                {
                    return Err(value("A Setting must contain its exact Ruleset snapshot"));
                }
            }
        }
        if let Some(setting) = settings.first() {
            root = *setting;
            if nodes[root].dependencies.iter().filter(|d| d.kind() == "ruleset_snapshot").count() != 1 {
                return Err(value("A Setting requires exactly one Ruleset snapshot"));
            }
        }
    }

    struct Walk<'n> {
        nodes: &'n [PackageGraphNode],
        ordered: Vec<usize>,
        visiting: HashSet<usize>,
        visited: HashSet<usize>,
        logical: HashMap<&'n str, &'n PackageIdentity>,
        modern: bool,
        root: usize,
    }

    fn visit(
        walk: &mut Walk<'_>,
        index: usize,
        lookup: &dyn Fn(&ExactPackageDependency) -> Option<usize>,
    ) -> DomainResult<()> {
        let nodes = walk.nodes;
        let identity = &nodes[index].identity;
        if walk.visiting.contains(&index) {
            return Err(value("Package closure contains a cycle"));
        }
        if let Some(previous) = walk.logical.get(identity.0.as_str()) {
            if *previous != identity {
                return Err(value("A logical package has conflicting exact identities"));
            }
        }
        walk.logical.insert(&identity.0, identity);
        if walk.visited.contains(&index) {
            return Ok(());
        }
        walk.visiting.insert(index);
        walk.ordered.push(index);
        for dependency in &nodes[index].dependencies {
            let target = lookup(dependency);
            let allowed_primary =
                walk.modern && index == walk.root && dependency.kind() == "ruleset_snapshot";
            match target {
                Some(target) if nodes[target].role != PackageRole::Primary || allowed_primary => {
                    visit(walk, target, lookup)?;
                }
                _ => return Err(value("Exact dependency is absent or attempts peer-primary merge")),
            }
        }
        walk.visiting.remove(&index);
        walk.visited.insert(index);
        Ok(())
    }

    let mut walk = Walk {
        nodes,
        ordered: Vec::new(),
        visiting: HashSet::new(),
        visited: HashSet::new(),
        logical: HashMap::new(),
        modern,
        root,
    };
    visit(&mut walk, root, &lookup)?;
    let reached: HashSet<usize> = walk.ordered.iter().copied().collect();
    if reached.len() != nodes.len() {
        return Err(value("Package archive contains undeclared closure members"));
    }
    let mut ordered = walk.ordered;
    if modern {
        // Execution loads the one effective Ruleset first; ownership follows the snapshot links.
        ordered.retain(|index| *index != primary);
        ordered.insert(0, primary);
    }
    Ok(ordered.into_iter().map(|index| nodes[index].identity.clone()).collect())
}

/// `resolve_exact_closure(manifests)`: the execution lock from the portable package graph.
pub fn resolve_exact_closure(manifests: &[PackageManifest]) -> DomainResult<PackageLock> {
    let nodes: Vec<PackageGraphNode> = manifests.iter().map(PackageGraphNode::from_manifest).collect();
    let identities = resolve_package_graph(&nodes)?;
    let mut entries = Vec::with_capacity(identities.len());
    for (order, identity) in identities.iter().enumerate() {
        // `{item.identity: item}`: a later manifest with the same identity wins, as in Python.
        let item = manifests
            .iter()
            .rev()
            .find(|m| owned(m.identity()) == *identity)
            .expect("resolved from these manifests");
        let dependencies = item
            .dependencies
            .iter()
            .map(|d| {
                PackageDependency::new(
                    d.package_id(),
                    d.version(),
                    d.content_sha256(),
                    d.kind(),
                    d.namespace(),
                )
            })
            .collect::<DomainResult<Vec<_>>>()?;
        entries.push(PackageLockEntry::new(
            item.package_id.clone(),
            item.version.clone(),
            item.content_sha256.clone(),
            item.role,
            order as i64,
            dependencies,
            item.capabilities.clone(),
        )?);
    }
    PackageLock::new(entries)
}

/// `campaign_setting_entries(lock)`: contained Settings in current snapshots and exact legacy
/// campaign locks.
pub fn campaign_setting_entries(lock: &PackageLock) -> Vec<&PackageLockEntry> {
    let Some(primary) = lock.primary() else { return Vec::new() };
    let primary_identity = primary.identity();
    let snapshots: Vec<&PackageLockEntry> = lock
        .entries()
        .iter()
        .filter(|item| {
            item.dependencies()
                .iter()
                .any(|d| d.kind() == "ruleset_snapshot" && d.identity() == primary_identity)
        })
        .collect();
    if !snapshots.is_empty() {
        return snapshots;
    }
    let legacy: HashSet<(&str, &str, &str)> = primary
        .dependencies()
        .iter()
        .filter(|d| d.kind() == "campaign_setting" || d.namespace() == "campaign_setting")
        .map(PackageDependency::identity)
        .collect();
    lock.entries().iter().filter(|item| legacy.contains(&item.identity())).collect()
}

/// `manifest_index(manifests)`: manifests by exact identity (a later one wins).
pub fn manifest_index(manifests: &[PackageManifest]) -> HashMap<PackageIdentity, &PackageManifest> {
    manifests.iter().map(|manifest| (owned(manifest.identity()), manifest)).collect()
}

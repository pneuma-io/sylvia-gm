"""Inspect exact installed package sources for an explicit Builder update."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sylvia_gm.application.package_materialization import snapshot_record
from sylvia_gm.domain.authority import PackageRole, SafePayload, owned_payload
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import (
    CandidateStatus,
    campaign_setting_entries,
    canonical_json_bytes,
)

if TYPE_CHECKING:
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import PackageLockEntry
    from sylvia_gm.domain.package_library import PackageCandidate


_MAX_CANDIDATES = 100
_MAX_PACKAGES = 64
_MAX_PATHS_PER_PACKAGE = 512
_MAX_TOTAL_PATHS = 2048
_MAX_TEXT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class LibrarySourceReader:
    """Read matching Ruleset sources and the Builder's own Setting when present."""

    owner: AuthorityOwner
    ruleset_id: str
    setting_id: str | None = None

    def candidates(self, *, query: str = "") -> tuple[SafePayload, ...]:
        """Return matching candidates in owner order without ranking opaque versions."""
        folded = query.casefold()
        results: list[SafePayload] = []
        for candidate in self.owner.package_candidates():
            if candidate.status not in {CandidateStatus.VALIDATED, CandidateStatus.ACTIVATED}:
                continue
            if not self._matches_source(candidate):
                continue
            summary = self._candidate_identity(candidate)
            searchable = " ".join(
                (
                    candidate.candidate_id,
                    candidate.closure_sha256,
                    *(str(package["id"]) for package in summary["packages"]),
                    *(str(package["version"]) for package in summary["packages"]),
                    *(str(package["label"]) for package in summary["packages"]),
                )
            ).casefold()
            if folded in searchable:
                results.append(summary)
            if len(results) > _MAX_CANDIDATES:
                raise CommandRejected("Too many compatible library sources; narrow the query")
        return tuple(results)

    def files(self, candidate_id: str) -> SafePayload:
        """List portable paths for one concrete compatible candidate."""
        candidate = self._candidate(candidate_id)
        packages = self._package_index(candidate)
        paths: list[str] = []
        for prefix, entry in packages:
            for path in self.owner.package_file_paths(
                entry.package_id,
                entry.version,
                entry.sha256,
                maximum_files=_MAX_PATHS_PER_PACKAGE,
            ):
                paths.append(f"{prefix}/{path}")
                if len(paths) > _MAX_TOTAL_PATHS:
                    raise CommandRejected("Library source has too many files to inspect")
        return owned_payload({**self._candidate_identity(candidate), "paths": sorted(paths)})

    def read(self, candidate_id: str, path: str) -> SafePayload:
        """Read one exact path previously exposed by files()."""
        candidate = self._candidate(candidate_id)
        for prefix, entry in self._package_index(candidate):
            prefix_with_slash = f"{prefix}/"
            if not path.startswith(prefix_with_slash):
                continue
            portable_path = path[len(prefix_with_slash) :]
            if portable_path not in self.owner.package_file_paths(
                entry.package_id,
                entry.version,
                entry.sha256,
                maximum_files=_MAX_PATHS_PER_PACKAGE,
            ):
                break
            content = self.owner.package_text_file(
                entry.package_id,
                entry.version,
                entry.sha256,
                portable_path,
                maximum_bytes=_MAX_TEXT_BYTES,
            )
            return owned_payload(
                {
                    "candidate_id": candidate.candidate_id,
                    "closure_sha256": candidate.closure_sha256,
                    "package": self._package_identity(entry, prefix),
                    "path": path,
                    "content": content,
                }
            )
        raise CommandRejected("Library source path is unavailable")

    def record(self, candidate_id: str, prefix: str) -> SafePayload:
        """Read editable package declarations with derived hashes and file indexes omitted."""
        candidate = self._candidate(candidate_id)
        primary = next(
            item for item in candidate.package_lock.entries if item.role is PackageRole.PRIMARY
        )
        for package_prefix, entry in self._package_index(candidate):
            if package_prefix != prefix:
                continue
            record = snapshot_record(
                self.owner.package_record(entry.package_id, entry.version, entry.sha256),
                primary=primary, settings=campaign_setting_entries(candidate.package_lock),
            )
            return owned_payload({
                "candidate_id": candidate.candidate_id,
                "closure_sha256": candidate.closure_sha256,
                "package": self._package_identity(entry, prefix),
                "path": prefix,
                "content": canonical_json_bytes(record).decode("utf-8"),
            })
        raise CommandRejected("Library source package is unavailable")

    def _candidate(self, candidate_id: str) -> PackageCandidate:
        candidate = self.owner.package_candidate(candidate_id)
        if candidate.status not in {
            CandidateStatus.VALIDATED,
            CandidateStatus.ACTIVATED,
        } or not self._matches_source(candidate):
            raise CommandRejected("Library source candidate is unavailable for this Builder")
        return candidate

    def _matches_source(self, candidate: PackageCandidate) -> bool:
        primary = next(
            (
                entry
                for entry in candidate.package_lock.entries
                if entry.role is PackageRole.PRIMARY
            ),
            None,
        )
        return primary is not None and primary.package_id == self.ruleset_id

    def _package_index(
        self, candidate: PackageCandidate
    ) -> tuple[tuple[str, PackageLockEntry], ...]:
        """Expose dependencies of the Ruleset and matching Setting, excluding other worlds."""
        if len(candidate.package_lock.entries) > _MAX_PACKAGES:
            raise CommandRejected("Library source has too many packages to inspect")
        setting_entries = campaign_setting_entries(candidate.package_lock)
        setting_id = next(
            (
                entry.package_id
                for entry in setting_entries
                if entry.package_id == self.setting_id
            ),
            None,
        )
        excluded_settings = {
            entry.package_id for entry in setting_entries if entry.package_id != setting_id
        }
        entries = {entry.package_id: entry for entry in candidate.package_lock.entries}
        pending = [self.ruleset_id, *([setting_id] if setting_id is not None else [])]
        visible: set[str] = set()
        while pending:
            identity = pending.pop()
            if identity in visible or identity in excluded_settings:
                continue
            visible.add(identity)
            pending.extend(dependency.package_id for dependency in entries[identity].dependencies)
        return tuple(
            (
                "ruleset"
                if entry.role is PackageRole.PRIMARY
                else "setting"
                if entry.package_id == setting_id
                else f"dependencies/{entry.package_id}",
                entry,
            )
            for entry in candidate.package_lock.entries
            if entry.package_id in visible
        )

    def _candidate_identity(self, candidate: PackageCandidate) -> SafePayload:
        packages = [
            self._package_identity(entry, prefix)
            for prefix, entry in self._package_index(candidate)
        ]
        return owned_payload(
            {
                "candidate_id": candidate.candidate_id,
                "closure_sha256": candidate.closure_sha256,
                "status": candidate.status.value,
                "source_kind": (
                    "setting"
                    if any(package["prefix"] == "setting" for package in packages)
                    else "ruleset"
                ),
                "packages": packages,
            }
        )

    def _package_identity(self, entry: PackageLockEntry, prefix: str) -> Mapping[str, object]:
        return {
            "prefix": prefix,
            "id": entry.package_id,
            "version": entry.version,
            "content_sha256": entry.sha256,
            "role": entry.role.value,
            "label": self.owner.package_label(entry.package_id, entry.version, entry.sha256),
        }

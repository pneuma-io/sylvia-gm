"""Public lab code uses an explicitly selected, separate private rules vault."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from balance_suite import provenance
from balance_suite.cli import main


class SourceInputTests(unittest.TestCase):
    def vault(self, root: Path) -> Path:
        root.mkdir()
        (root / "AGENTS.md").write_text("Private vault", encoding="utf-8")
        (root / "Testing").mkdir()
        return root

    def test_external_notes_do_not_need_to_be_inside_the_code_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = self.vault(Path(temporary) / "notes")
            with patch.dict(os.environ, {"TD_BALANCE_NOTES_DIR": str(vault)}):
                self.assertEqual(provenance.repo_root(), vault.resolve())

    def test_invalid_explicit_notes_do_not_fall_back_to_another_vault(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = self.vault(Path(temporary) / "notes")
            with (
                patch.dict(os.environ, {"TD_BALANCE_NOTES_DIR": str(vault / "missing")}),
                patch.object(Path, "cwd", return_value=vault),
                self.assertRaisesRegex(RuntimeError, "TD_BALANCE_NOTES_DIR"),
            ):
                provenance.repo_root()

    def test_default_commit_is_the_lab_code_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = self.vault(Path(temporary) / "notes")
            with (
                patch.dict(os.environ, {"TD_BALANCE_NOTES_DIR": str(vault)}),
                patch.object(
                    provenance.subprocess, "run", return_value=Mock(stdout="code-sha\n")
                ) as run,
            ):
                self.assertEqual(provenance.current_commit(), "code-sha")
                self.assertEqual(
                    run.call_args.kwargs["cwd"], Path(provenance.__file__).resolve().parents[1]
                )

    def test_excluded_credential_note_is_never_fingerprinted(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = self.vault(Path(temporary) / "notes")
            (vault / "Hacking").mkdir()
            (vault / "Hacking/CREDENTIAL.md").write_text(
                "Synthetic excluded fixture", encoding="utf-8"
            )
            (vault / "Hacking/NODE.md").write_text("Ordinary note", encoding="utf-8")
            fingerprint = provenance.protected_fingerprint(vault)
            self.assertIn("Hacking/NODE.md", fingerprint)
            self.assertNotIn("Hacking/CREDENTIAL.md", fingerprint)

    def test_missing_notes_is_a_structured_cli_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"TD_BALANCE_NOTES_DIR": str(Path(temporary) / "missing")}),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(main(["exact"]), 1)
            failure = json.loads(stderr.getvalue())
            self.assertEqual(failure["error"], "RuntimeError")
            self.assertIn("TD_BALANCE_NOTES_DIR", failure["message"])

    def test_stale_objective_results_are_refused_before_any_new_simulation(self):
        from balance_suite.parameters import default_manifest
        from balance_suite.runner import build_baseline

        parameters = default_manifest().seed_set()
        override = {
            "scenario_id": "TEAM-EXTRACT-4V4-V1",
            "parameter_set_hash": parameters.hash,
            "master_seed": 88,
            "sample_replay": {"present": True},
            "execution_source_hash": "older-lab-bytes",
        }
        with self.assertRaisesRegex(ValueError, "execution source mismatch"):
            build_baseline(
                parameters,
                master_seed=88,
                runs=1,
                objective_overrides={override["scenario_id"]: override},
            )

    def test_replay_refuses_a_changed_source_identity(self):
        from balance_suite.parameters import default_manifest
        from balance_suite.replay import ReplayMismatch, replay_artifact
        from balance_suite.simulation import DuelSpec, run_duel

        manifest = default_manifest()
        artifact = run_duel(
            DuelSpec(max_team_turns=2), manifest.seed_set(), 88, capture_events=True
        )
        artifact["replay_spec"]["execution_source_hash"] = "older-lab-bytes"
        with self.assertRaisesRegex(ReplayMismatch, "execution source"):
            replay_artifact(artifact, manifest)

    def test_structural_validation_does_not_claim_numeric_or_runtime_qualification(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["validate", "--structural-only"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["scope"], "STRUCTURAL_ONLY")
        self.assertNotEqual(result["balance_status"], "READY")
        self.assertFalse(result["specialty_objective_scenarios_complete"])


if __name__ == "__main__":
    unittest.main()

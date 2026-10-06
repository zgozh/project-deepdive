"""Focused D3b deterministic audit behavior checks."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from phase6_whole_book_audit import _anchor, _canonical_footnote, audit_whole_book  # noqa: E402
import whole_book_audit_workflow as audit_workflow  # noqa: E402


class WholeBookAuditTests(unittest.TestCase):
    def test_checks_bound_project_claims_and_ignores_links_inside_code_fences(self):
        unit_a = "CURRICULUM-UNIT-" + "1" * 64
        unit_b = "CURRICULUM-UNIT-" + "2" * 64
        claim_id = "CLAIM-" + "a" * 64
        evidence_id = "EVID-fixture"
        first = {
            "claims": [{"id": claim_id, "statement_sha256": "1" * 64, "evidence_ids": [evidence_id],
                        "resolved_evidence_levels": ["E1"], "graph_node_ids": [], "graph_edge_ids": [], "file_paths": ["src/a.py"]}],
            "evidence": [{"id": evidence_id, "level": "E1", "provisional": False,
                          "locator": {"path": "src/a.py", "line_start": 1, "line_end": 1}, "source_members": ["src/a.py"]}],
        }
        second = {
            "claims": [{**first["claims"][0], "statement_sha256": "2" * 64}],
            "evidence": first["evidence"],
        }
        rows = [
            {"position": 1, "unit_id": unit_a, "route": "PHASE6A_PROJECT_CLAIM", "selection_state": "SELECTED",
             "attempt_id": "attempt-0001", "prerequisite_ids": [], "review_status_sha256": "3" * 64,
             "answer_status": "INCLUDED"},
            {"position": 2, "unit_id": unit_b, "route": "PHASE6A_PROJECT_CLAIM", "selection_state": "SELECTED",
             "attempt_id": "attempt-0001", "prerequisite_ids": [unit_a], "review_status_sha256": "4" * 64,
             "answer_status": "NOT_SUPPLIED"},
        ]
        footnote = _canonical_footnote(first, first["claims"][0])
        wrong_footnote = f"[^CLAIM-{claim_id.removeprefix('CLAIM-')}]: {{}}"
        unbound_claim_id = "CLAIM-" + "b" * 64
        markdown = (
            "## Contents\n\n"
            f"- [First](#{_anchor(unit_a)})\n- [Second](#{_anchor(unit_b)})\n\n## Lessons\n\n"
            f'<a id="{_anchor(unit_a)}"></a>\n### First\n\n'
            f"Claim: supported [^{claim_id}]\n{wrong_footnote}\n{footnote}\n"
            "[bad](#missing)\n```md\n[ignored](#not-present)\n```\n\n"
            f'<a id="{_anchor(unit_b)}"></a>\n### Second\n\n'
            f"See [first](#{_anchor(unit_a)}).\nClaim: unsupported [^{unbound_claim_id}]\n{footnote}\n"
        )
        assembly = {"units": rows, "source_status": "PARTIAL", "unknown_files": 2}

        findings, counts = audit_whole_book(assembly, markdown, {unit_a: first, unit_b: second})
        codes = [item["code"] for item in findings]

        self.assertIn("FRAGMENT_UNRESOLVED", codes)
        self.assertEqual(2, counts["same_book_fragment_count"], "fenced-code link must not enter the inventory")
        self.assertIn("CLAIM_DEFINITION_DUPLICATE", codes)
        self.assertIn("CLAIM_DEFINITION_MISMATCH", codes)
        self.assertIn("CLAIM_BINDING_CONFLICT", codes)
        self.assertIn("CLAIM_REFERENCE_UNBOUND", codes)
        self.assertIn("SOURCE_PARTIAL", codes)
        self.assertIn("UNKNOWN_FILES", codes)
        self.assertIn("ANSWER_GAP", codes)
        self.assertEqual(1, counts["repeated_claim_id_count"])
        self.assertEqual(2, counts["claim_reference_count"])
        unbound_reference = next(item for item in findings if item["code"] == "CLAIM_REFERENCE_UNBOUND")
        self.assertEqual(unbound_claim_id, unbound_reference["claim_id"])
        self.assertEqual(1, counts["prerequisite_count"])

    def test_same_claim_definition_in_two_chapters_is_reported_at_second_chapter(self):
        unit_a = "CURRICULUM-UNIT-" + "3" * 64
        unit_b = "CURRICULUM-UNIT-" + "4" * 64
        claim_id = "CLAIM-" + "c" * 64
        evidence_id = "EVID-shared"
        facts = {
            "claims": [{"id": claim_id, "statement_sha256": "5" * 64, "evidence_ids": [evidence_id],
                        "resolved_evidence_levels": ["E1"], "graph_node_ids": [], "graph_edge_ids": [], "file_paths": ["src/shared.py"]}],
            "evidence": [{"id": evidence_id, "level": "E1", "provisional": False,
                          "locator": {"path": "src/shared.py", "line_start": 3, "line_end": 3}, "source_members": ["src/shared.py"]}],
        }
        rows = [
            {"position": 1, "unit_id": unit_a, "route": "PHASE6A_PROJECT_CLAIM", "selection_state": "SELECTED",
             "attempt_id": "attempt-0001", "prerequisite_ids": [], "review_status_sha256": "6" * 64,
             "answer_status": "NOT_SUPPLIED"},
            {"position": 2, "unit_id": unit_b, "route": "PHASE6A_PROJECT_CLAIM", "selection_state": "SELECTED",
             "attempt_id": "attempt-0001", "prerequisite_ids": [], "review_status_sha256": "7" * 64,
             "answer_status": "NOT_SUPPLIED"},
        ]
        footnote = _canonical_footnote(facts, facts["claims"][0])
        markdown = (
            "## Contents\n\n"
            f"- [First](#{_anchor(unit_a)})\n- [Second](#{_anchor(unit_b)})\n\n## Lessons\n\n"
            f'<a id="{_anchor(unit_a)}"></a>\n### First\n\nClaim: supported [^{claim_id}]\n{footnote}\n\n'
            f'<a id="{_anchor(unit_b)}"></a>\n### Second\n\nClaim: supported [^{claim_id}]\n{footnote}\n'
        )

        findings, _ = audit_whole_book(
            {"units": rows, "source_status": "PASS", "unknown_files": 0},
            markdown,
            {unit_a: facts, unit_b: facts},
        )
        duplicates = [item for item in findings if item["code"] == "CLAIM_DEFINITION_DUPLICATE"]

        self.assertEqual(1, len(duplicates))
        self.assertEqual(unit_b, duplicates[0]["unit_id"])
        self.assertEqual([unit_a, unit_b], duplicates[0]["related_unit_ids"])
        self.assertEqual(18, duplicates[0]["line"])


class WholeBookAuditCliTests(unittest.TestCase):
    def test_path_guard_error_is_fixed_cli_refusal_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            output = root / "audit-output"
            stdout = io.StringIO()
            stderr = io.StringIO()

            def trigger_path_guard(**kwargs):
                audit_workflow.chapter._assert_no_link_components(kwargs["target_root"])

            with patch.object(
                audit_workflow.chapter,
                "_assert_no_link_components",
                side_effect=audit_workflow.chapter.Phase6ChapterError("INPUT_INVALID"),
            ) as path_guard:
                with patch.object(audit_workflow, "audit_assembly", side_effect=trigger_path_guard):
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        status = audit_workflow.main([
                            "audit", "--assembly-dir", str(root / "assembly"),
                            "--state-dir", str(root / "state"), "--target-root", str(target),
                            "--out-dir", str(output),
                        ])

            self.assertEqual(2, status)
            self.assertEqual('{"error":"INPUT_INVALID"}\n', stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
            path_guard.assert_called_once_with(target)
            self.assertFalse(output.exists())
            self.assertEqual([], list(target.iterdir()))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Focused contracts for conservative D2B2b finding classification."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


class CurriculumRepairClassificationTests(unittest.TestCase):
    def _classifier(self):
        try:
            import phase6_curriculum_repair as repair
        except ModuleNotFoundError as exc:
            self.fail(f"D2B2b requires the route classifier module: {exc}")
        return repair.classify_repair_findings

    def test_general_factuality_wording_repair_requires_exact_code_action_pairs(self):
        classify = self._classifier()
        status = {
            "review_state": "REPAIR_REQUIRED",
            "review_session_id": "GENERAL-REVIEW-" + "1" * 64,
            "selected_unit_id": "CURRICULUM-PRIMER-" + "2" * 64,
        }
        reports = {
            "factuality": {"results": [
                {
                    "occurrence_id": "GL-OCC-" + "3" * 64,
                    "findings": [{
                        "issue_code": "MISSING_SCOPE_OR_CONDITION",
                        "location_id": "GL-OCC-" + "3" * 64,
                        "recommended_action": "NARROW_OR_CORRECT_CLAIM",
                    }],
                },
                {
                    "occurrence_id": "GL-OCC-" + "4" * 64,
                    "findings": [{
                        "issue_code": "UNSUPPORTED_GENERAL_FACT",
                        "location_id": "GL-OCC-" + "4" * 64,
                        "recommended_action": "REWRITE_AS_NONFACTUAL_TEACHING",
                    }],
                },
            ]},
            "beginner": {"results": [{
                "scope_id": "GL-SCOPE-" + "5" * 64,
                "findings": [{
                    "issue_code": "UNCLEAR_EXPLANATION",
                    "location_id": "GL-SCOPE-" + "5" * 64,
                    "recommended_action": "CLARIFY_EXPLANATION",
                }],
            }]},
        }
        kwargs = {
            "run_state_id": "CURRICULUM-RUN-STATE-" + "6" * 64,
            "prerequisite_ids": ["CURRICULUM-UNIT-" + "7" * 64],
        }

        result = classify("D2A_GENERAL_LEARNING", status, reports, **kwargs)
        self.assertEqual("SAME_SNAPSHOT_REPAIR", result["disposition"])
        self.assertEqual(
            ["TEACHING_EDIT_POSSIBLE"] * 3,
            [item["classification"] for item in result["findings"]],
        )

        for result_index, action in ((0, "CITE_AUTHORITY"), (1, "NARROW_OR_CORRECT_CLAIM")):
            invalid = copy.deepcopy(reports)
            invalid["factuality"]["results"][result_index]["findings"][0]["recommended_action"] = action
            result = classify("D2A_GENERAL_LEARNING", status, invalid, **kwargs)
            self.assertEqual("STOP_UNSUPPORTED", result["disposition"])

        disallowed = (
            ("CONTRADICTED_OR_OUTDATED", "NARROW_OR_CORRECT_CLAIM", "STOP_UNSUPPORTED"),
            ("TARGET_PROJECT_ASSERTION", "REMOVE_TARGET_PROJECT_CLAIM", "UPSTREAM_REQUIRED"),
            ("SENSITIVE_OR_COPIED_CONTENT", "REDACT_OR_REWRITE_CONTENT", "STOP_UNSUPPORTED"),
            ("AUTHORITY_INADEQUATE", "CITE_AUTHORITY", "STOP_UNSUPPORTED"),
            ("UNABLE_TO_ASSESS", "NO_CHANGE", "STOP_UNSUPPORTED"),
        )
        for code, action, disposition in disallowed:
            with self.subTest(code=code):
                invalid = copy.deepcopy(reports)
                invalid["factuality"]["results"][0]["findings"][0].update({
                    "issue_code": code, "recommended_action": action,
                })
                result = classify("D2A_GENERAL_LEARNING", status, invalid, **kwargs)
                self.assertEqual(disposition, result["disposition"])

    def test_general_beginner_finding_is_repairable_but_unqualified_factuality_is_not(self):
        classify = self._classifier()
        status = {
            "review_state": "REPAIR_REQUIRED",
            "review_session_id": "GENERAL-REVIEW-" + "1" * 64,
            "selected_unit_id": "CURRICULUM-PRIMER-" + "2" * 64,
        }
        reports = {
            "factuality": {"results": []},
            "beginner": {
                "results": [{
                    "scope_id": "GL-SCOPE-" + "3" * 64,
                    "findings": [{
                        "issue_code": "UNCLEAR_EXPLANATION",
                        "location_id": "GL-SCOPE-" + "3" * 64,
                        "severity": "MAJOR",
                        "recommended_action": "CLARIFY_EXPLANATION",
                    }],
                }],
            },
        }
        result = classify(
            "D2A_GENERAL_LEARNING", status, reports,
            run_state_id="CURRICULUM-RUN-STATE-" + "4" * 64,
            prerequisite_ids=["CURRICULUM-UNIT-" + "5" * 64],
        )
        self.assertEqual("SAME_SNAPSHOT_REPAIR", result["disposition"])
        self.assertEqual(["TEACHING_EDIT_POSSIBLE"], [item["classification"] for item in result["findings"]])

        reports["factuality"]["results"] = [{
            "occurrence_id": "GL-OCC-" + "6" * 64,
            "findings": [{"issue_code": "UNSUPPORTED_GENERAL_FACT", "location_id": "GL-OCC-" + "6" * 64}],
        }]
        result = classify(
            "D2A_GENERAL_LEARNING", status, reports,
            run_state_id="CURRICULUM-RUN-STATE-" + "4" * 64,
            prerequisite_ids=["CURRICULUM-UNIT-" + "5" * 64],
        )
        self.assertEqual("STOP_UNSUPPORTED", result["disposition"])
        self.assertIn("STOP_UNSUPPORTED", [item["classification"] for item in result["findings"]])

    def test_review_incomplete_never_opens_a_repair(self):
        classify = self._classifier()
        result = classify(
            "D2A_GENERAL_LEARNING",
            {"review_state": "REVIEW_INCOMPLETE", "review_session_id": "GENERAL-REVIEW-" + "1" * 64,
             "selected_unit_id": "CURRICULUM-PRIMER-" + "2" * 64},
            {"factuality": {"results": []}, "beginner": {"results": []}},
            run_state_id="CURRICULUM-RUN-STATE-" + "4" * 64,
            prerequisite_ids=[],
        )
        self.assertEqual("REVIEW_INCOMPLETE", result["disposition"])
        self.assertEqual([], result["findings"])

    def test_project_route_reuses_only_the_accepted_b2a_general_rewrite_tuple(self):
        classify = self._classifier()
        status = {
            "review_state": "REPAIR_REQUIRED",
            "review_session_id": "REVIEW-" + "1" * 64,
            "chapter_id": "CHAPTER-" + "2" * 64,
        }
        evidence = {
            "results": [{
                "occurrence_id": "OCC-" + "3" * 64,
                "kind": "GENERAL",
                "findings": [{
                    "issue_code": "PROJECT_FACT_UNMARKED",
                    "recommended_action": "MARK_AS_CLAIM_OR_REWRITE_AS_GENERAL",
                    "evidence_id": None,
                }],
            }],
        }
        beginner = {
            "results": [{
                "scope_id": "SECTION-" + "4" * 64,
                "findings": [{
                    "issue_code": "UNDEFINED_JARGON",
                    "recommended_action": "DEFINE_TERM_BEFORE_USE",
                }],
            }],
        }
        result = classify(
            "PHASE6A_PROJECT_CLAIM", status, {"evidence": evidence, "beginner": beginner},
            run_state_id="CURRICULUM-RUN-STATE-" + "5" * 64,
            prerequisite_ids=[],
        )
        self.assertEqual("SAME_SNAPSHOT_REPAIR", result["disposition"])

        evidence["results"][0]["findings"][0]["evidence_id"] = "EVID-proj-1"
        result = classify(
            "PHASE6A_PROJECT_CLAIM", status, {"evidence": evidence, "beginner": beginner},
            run_state_id="CURRICULUM-RUN-STATE-" + "5" * 64,
            prerequisite_ids=[],
        )
        self.assertEqual("UPSTREAM_REQUIRED", result["disposition"])


class CurriculumRepairPathTests(unittest.TestCase):
    def test_repaired_attempt_uses_its_own_immutable_attempt_directory(self):
        import curriculum_run_execution_workflow as workflow

        row = {"position": 2, "route": "PHASE6A_PROJECT_CLAIM", "attempt_id": "attempt-0002"}
        paths = workflow._unit_paths(Path("/external/run-state"), row)
        self.assertEqual(Path("/external/run-state/units/0002/attempt-0002"), paths["attempt"])

    def test_boundary_sync_updates_only_the_active_attempt(self):
        import curriculum_run_execution_workflow as workflow

        prior = {
            "attempt_id": "attempt-0001", "execution_state": "BLOCKED_REVIEW", "disposition": "ORIGINAL",
            "predecessor_attempt_id": None, "predecessor_review_status_sha256": None,
            "predecessor_report_digests": [],
            "artifact_digests": [{"role": "candidate", "relative_path": "units/0002/attempt-0001/general-prepare/candidate.json", "sha256": "a" * 64}],
        }
        prior_before = copy.deepcopy(prior)
        next_attempt = {
            "attempt_id": "attempt-0002", "execution_state": "REPAIR_READY", "disposition": "SAME_SNAPSHOT_REPAIR",
            "predecessor_attempt_id": "attempt-0001", "predecessor_review_status_sha256": "b" * 64,
            "predecessor_report_digests": [], "artifact_digests": [],
        }
        current = {"attempt_id": "attempt-0002", "execution_state": "PREPARED", "attempts": [prior, next_attempt],
                   "artifact_digests": prior["artifact_digests"] + [
                       {"role": "general_facts", "relative_path": "units/0002/attempt-0002/general-prepare/facts.json", "sha256": "c" * 64},
                   ]}

        workflow._sync_active_attempt(current, "1.1.0")

        self.assertEqual(prior_before, current["attempts"][0])
        self.assertEqual("PREPARED", current["attempts"][1]["execution_state"])
        self.assertEqual(current["artifact_digests"][-1:], current["attempts"][1]["artifact_digests"])

    def test_open_repair_attempt_binds_exact_predecessor_status_and_reports(self):
        import phase6_curriculum_repair as repair

        digests = [
            {"role": "general_facts", "relative_path": "units/0002/attempt-0001/general-prepare/facts.json", "sha256": "1" * 64},
            {"role": "candidate", "relative_path": "units/0002/attempt-0001/general-prepare/candidate.json", "sha256": "2" * 64},
            {"role": "factuality_report", "relative_path": "units/0002/attempt-0001/general-review/factuality.json", "sha256": "3" * 64},
            {"role": "beginner_report", "relative_path": "units/0002/attempt-0001/general-review/beginner.json", "sha256": "4" * 64},
            {"role": "general_review_status", "relative_path": "units/0002/attempt-0001/general-review/status.json", "sha256": "5" * 64},
        ]
        old_attempt = {
            "attempt_id": "attempt-0001", "execution_state": "BLOCKED_REVIEW", "disposition": "ORIGINAL",
            "predecessor_attempt_id": None, "predecessor_review_status_sha256": None,
            "predecessor_report_digests": [], "artifact_digests": copy.deepcopy(digests),
        }
        row = {
            "route": "D2A_GENERAL_LEARNING", "execution_state": "BLOCKED_REVIEW", "attempt_id": "attempt-0001",
            "repair_disposition": None, "artifact_digests": copy.deepcopy(digests), "attempts": [old_attempt],
        }
        prior = copy.deepcopy(row)

        opened = repair.open_repair_attempt(row)

        self.assertEqual(prior, row)
        self.assertEqual("REPAIR_READY", opened["execution_state"])
        self.assertEqual("attempt-0002", opened["attempt_id"])
        self.assertEqual("SAME_SNAPSHOT_REPAIR", opened["repair_disposition"])
        self.assertEqual("attempt-0001", opened["attempts"][1]["predecessor_attempt_id"])
        self.assertEqual("5" * 64, opened["attempts"][1]["predecessor_review_status_sha256"])
        self.assertEqual([
            {"role": "factuality_report", "sha256": "3" * 64},
            {"role": "beginner_report", "sha256": "4" * 64},
        ], opened["attempts"][1]["predecessor_report_digests"])
        self.assertEqual(digests, opened["artifact_digests"])

    def test_repair_lineage_allows_attempt_three_but_never_four(self):
        import phase6_curriculum_repair as repair

        def review_digests(attempt_id, seed):
            attempt_dir = f"units/0002/{attempt_id}/general-review"
            return [
                {"role": "factuality_report", "relative_path": f"{attempt_dir}/factuality.json", "sha256": str(seed) * 64},
                {"role": "beginner_report", "relative_path": f"{attempt_dir}/beginner.json", "sha256": str(seed + 1) * 64},
                {"role": "general_review_status", "relative_path": f"{attempt_dir}/status.json", "sha256": str(seed + 2) * 64},
            ]

        def blocked_attempt(attempt_id, seed, previous=None):
            return {
                "attempt_id": attempt_id, "execution_state": "BLOCKED_REVIEW",
                "disposition": "ORIGINAL" if previous is None else "SAME_SNAPSHOT_REPAIR",
                "predecessor_attempt_id": previous,
                "predecessor_review_status_sha256": None if previous is None else "a" * 64,
                "predecessor_report_digests": [], "artifact_digests": review_digests(attempt_id, seed),
            }

        first = blocked_attempt("attempt-0001", 1)
        second = blocked_attempt("attempt-0002", 4, "attempt-0001")
        row = {
            "route": "D2A_GENERAL_LEARNING", "execution_state": "BLOCKED_REVIEW",
            "attempt_id": "attempt-0002", "repair_disposition": "SAME_SNAPSHOT_REPAIR",
            "attempts": [first, second], "artifact_digests": first["artifact_digests"] + second["artifact_digests"],
        }

        opened = repair.open_repair_attempt(row)

        self.assertEqual("attempt-0003", opened["attempt_id"])
        self.assertEqual("attempt-0002", opened["attempts"][-1]["predecessor_attempt_id"])
        third = blocked_attempt("attempt-0003", 7, "attempt-0002")
        exhausted = copy.deepcopy(row)
        exhausted.update({
            "attempt_id": "attempt-0003", "attempts": [first, second, third],
            "artifact_digests": row["artifact_digests"] + third["artifact_digests"],
        })
        with self.assertRaisesRegex(ValueError, "REPAIR_LINEAGE_INVALID"):
            repair.open_repair_attempt(exhausted)

    def test_execution_cli_exposes_exact_unit_repair_selection(self):
        import curriculum_run_execution_workflow as workflow

        arguments = ["repair"]
        for option in (
            "phase4c-package", "claim-candidates", "claim-evidence", "claim-evidence-graph",
            "prerequisite-candidates", "prerequisite-graph", "curriculum-candidates", "curriculum",
            "run-dir", "root",
        ):
            arguments.extend([f"--{option}", f"{option}.json"])
        arguments.extend(["--plan", "plan.json", "--state-dir", "state", "--unit-id", "CURRICULUM-UNIT-" + "a" * 64])

        parsed = workflow._parser().parse_args(arguments)

        self.assertEqual("repair", parsed.command)
        self.assertEqual("CURRICULUM-UNIT-" + "a" * 64, parsed.unit_id)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for Project DeepDive's versioned intermediate artifact contract."""

import copy
import json
import hashlib
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
SCHEMA_ROOT = SKILL_ROOT / "schemas" / "v1"
FIXTURE_ROOT = SKILL_ROOT / "tests" / "fixtures" / "artifacts" / "v1"
CLI = SKILL_ROOT / "scripts" / "validate_artifact.py"


def _git_tree_path(revision, path):
    current_path = path.relative_to(REPOSITORY_ROOT).as_posix()
    current_result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:{current_path}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
    )
    if current_result.returncode == 0:
        return current_path

    try:
        skill_relative_path = path.relative_to(SKILL_ROOT)
    except ValueError:
        return current_path

    legacy_path = (
        REPOSITORY_ROOT / "skills" / "replicate-learning" / skill_relative_path
    ).relative_to(REPOSITORY_ROOT).as_posix()
    legacy_result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:{legacy_path}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
    )
    if legacy_result.returncode == 0:
        return legacy_path
    return current_path
EXPECTED_KINDS = {
    "development-plan",
    "project-index",
    "stack-profile",
    "evidence",
    "coverage",
    "static-analysis",
    "knowledge-graph",
    "curriculum",
    "quality-report",
    "phase3-run",
    "claim-candidates",
    "claim-evidence",
    "claim-evidence-graph",
    "semantic-proposals",
    "prerequisite-candidates",
    "prerequisite-graph",
    "curriculum-candidates",
    "curriculum-run-plan",
    "curriculum-run-state",
    "chapter-facts",
    "answer-candidates",
    "exercise-bank",
    "chapter-draft-status",
    "chapter-review-session",
    "chapter-evidence-review",
    "chapter-beginner-review",
    "chapter-review-status",
    "chapter-repair-triage",
    "general-unit-facts",
    "general-unit-candidate",
    "general-learning-unit",
    "general-review-session",
    "general-factuality-review",
    "general-beginner-review",
    "general-review-status",
    "answer-review-session",
    "answer-evidence-review",
    "answer-beginner-review",
    "answer-review-status",
    "whole-book-assembly",
    "whole-book-consistency-audit",
}
# Phase 2's scanner and Phase 3A's detector each opt in only their emitted
# artifact kinds. Every unrelated kind stays at exactly 1.0.0.
SCANNER_KINDS = {"project-index", "coverage"}
PHASE3A_KINDS = {"stack-profile", "evidence"}
STATIC_ANALYSIS_OPT_IN_VERSIONS = {
    "static-analysis": {"1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"},
}
SUPPORTED_V1_VERSIONS = ["1.0.0", "1.1.0"]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_contract import (  # noqa: E402
    ARTIFACT_KINDS,
    ArtifactValidationError,
    dumps_artifact,
    load_artifact,
    schema_path,
    validate_artifact,
    validate_schema_document,
)


def _minimal_phase4_identity():
    return {
        "source_metadata": {
            "snapshot_kind": "worktree",
            "g01_status": "PASS",
            "unknown_files": 0,
            "project_index_sha256": "a" * 64,
            "coverage_sha256": "b" * 64,
        },
        "source_run": {
            "artifact_kind": "phase3-run",
            "schema_version": "1.0.0",
            "manifest_sha256": "0" * 64,
            "status": "PASS",
            "e1_audit": {
                "status": "PASS",
                "g01": {"before": "PASS", "after": "PASS"},
                "adapters": {
                    name: {
                        "status": "NOT_RUN",
                        "version": None,
                        "applicable_files": 0,
                        "analyzed_files": 0,
                        "skipped_files": 0,
                        "partial_files": 0,
                        "limitations": [],
                    }
                    for name in ("python", "java", "frontend")
                },
                "counts": {
                    "tracked_files": 0,
                    "covered_tracked_files": 0,
                    "unknown_tracked_files": 0,
                    "analyzed_files": 0,
                    "skipped_files": 0,
                    "partial_files": 0,
                    "unreported_source_files": 0,
                    "outside_analyzer_files": 0,
                },
                "limitations": [],
            },
            "members": [
                {"path": "phase2/project-index.json", "artifact_kind": "project-index", "schema_version": "1.1.0", "sha256": "c" * 64},
                {"path": "phase2/coverage.json", "artifact_kind": "coverage", "schema_version": "1.1.0", "sha256": "d" * 64},
                {"path": "phase3a/stack-profile.json", "artifact_kind": "stack-profile", "schema_version": "1.1.0", "sha256": "e" * 64},
                {"path": "phase3a/evidence.json", "artifact_kind": "evidence", "schema_version": "1.1.0", "sha256": "f" * 64},
            ],
        },
    }


def minimal_artifacts():
    common = {
        "schema_version": "1.0.0",
        "repository_revision": "1463a06437fc903edec24722ecbb686a46d8f9da",
        "generated_at": "2026-09-22T00:00:00Z",
    }
    phase4_identity = _minimal_phase4_identity()
    phase4_metadata = phase4_identity["source_metadata"]
    phase4_run = phase4_identity["source_run"]
    phase4_inputs = [
        {"path": "knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": "1" * 64},
        {"path": "evidence.json", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": "2" * 64},
    ]
    phase5_inputs = [
        {"path": "evidence.json", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": "1" * 64},
        {"path": "knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": "2" * 64},
        {"path": "semantic-proposals.json", "artifact_kind": "semantic-proposals", "schema_version": "1.0.0", "sha256": "3" * 64},
    ]
    review_inputs = [
        {"role": role, "artifact_kind": kind, "schema_version": version, "sha256": "a" * 64}
        for role, kind, version in (
            ("phase4_base_graph", "knowledge-graph", "1.1.0"),
            ("phase4_base_evidence", "evidence", "1.2.0"),
            ("phase4_semantic_proposals", "semantic-proposals", "1.0.0"),
            ("phase4_claim_candidates", "claim-candidates", "1.1.0"),
            ("phase4_claim_evidence", "claim-evidence", "1.1.0"),
            ("phase4_claim_evidence_graph", "claim-evidence-graph", "1.0.0"),
            ("phase5_prerequisite_candidates", "prerequisite-candidates", "1.0.0"),
            ("phase5_prerequisite_graph", "prerequisite-graph", "1.0.0"),
            ("phase5_curriculum_candidates", "curriculum-candidates", "1.0.0"),
            ("phase5_curriculum", "curriculum", "1.1.0"),
        )
    ]
    review_id = "REVIEW-" + "7" * 64
    review_chapter_id = "CHAPTER-" + "2" * 64
    review_identity = {
        "review_session_id": review_id,
        "review_session_sha256": "8" * 64,
        "review_round": 0,
        "repository_revision": common["repository_revision"],
        "snapshot_kind": "worktree",
        "source_run_manifest_sha256": phase4_run["manifest_sha256"],
        "chapter_id": review_chapter_id,
        "chapter_sha256": "5" * 64,
        "chapter_facts_sha256": "6" * 64,
        "exercise_bank_sha256": "9" * 64,
    }
    review_session = {
        "artifact_kind": "chapter-review-session",
        "schema_version": "1.0.0",
        **{key: value for key, value in review_identity.items() if key != "review_session_sha256"},
        "review_session_id": review_id,
        "review_round": 0,
        "generated_at": common["generated_at"],
        "source_metadata": copy.deepcopy(phase4_metadata),
        "source_status": "PASS",
        "unknown_files": 0,
        "phase3_member_digests": [{
            "path": "phase2/project-index.json",
            "artifact_kind": "project-index",
            "schema_version": "1.1.0",
            "sha256": "e" * 64,
        }],
        "input_digests": copy.deepcopy(review_inputs),
        "writer_alias": "writer-local",
        "prompt_versions": {"evidence_verifier": "1.0.0", "beginner_critic": "1.0.0"},
        "chapter_facts_sha256": "6" * 64,
        "exercise_bank_sha256": "9" * 64,
        "phase6a_status_sha256": "b" * 64,
        "evidence_packet_sha256": "c" * 64,
        "beginner_packet_sha256": "d" * 64,
    }
    review_line_sha = "e" * 64
    review_occurrence = "OCC-" + "f" * 64
    review_beginner_results = [
        {
            "scope_id": ("SECTION-" if heading != "Exercise set" else "EXERCISES-") + format(index + 1, "064x"),
            "scope_kind": "SECTION" if heading != "Exercise set" else "EXERCISE_SET",
            "heading": heading,
            "line_number": index + 1,
            "line_sha256": review_line_sha,
            "outcome": "FOLLOWABLE_FOR_DECLARED_BEGINNER",
            "findings": [],
        }
        for index, heading in enumerate((
            "Intuition", "Prerequisite", "Project use", "Architecture", "Source", "Mechanism",
            "Failure and debugging", "Extension", "Learning checks", "Exercise set",
        ))
    ]
    general_unit = {
        "id": "CURRICULUM-PRIMER-" + "4" * 64,
        "order": 1,
        "title": "Request basics",
        "kind": "prerequisite",
        "scope": "PROJECT_SPECIFIC",
        "depth": 1,
        "content_status": "OUTLINE_ONLY",
        "epistemic_status": "UNVERIFIED_TEACHING",
        "prerequisite_ids": [],
        "introduces_concept_keys": ["request.basics"],
        "requires_concept_keys": [],
        "project_refs": {key: [] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")},
        "origin": "PREREQUISITE_PRIMER",
        "primer_concept_key": "request.basics",
        "prerequisite_node_id": "PREREQ-NODE-" + "5" * 64,
        "concept_epistemic_status": "UNVERIFIED_TEACHING",
    }
    general_unit_facts = {
        "artifact_kind": "general-unit-facts",
        "schema_version": "1.0.0",
        "repository_revision": common["repository_revision"],
        "generated_at": common["generated_at"],
        "snapshot_kind": "worktree",
        "source_metadata": copy.deepcopy(phase4_metadata),
        "source_status": "PASS",
        "source_run_manifest_sha256": phase4_run["manifest_sha256"],
        "unknown_files": 0,
        "input_digests": copy.deepcopy(review_inputs),
        "selected_unit": copy.deepcopy(general_unit),
    }
    general_unit_candidate = {
        "artifact_kind": "general-unit-candidate",
        "schema_version": "1.0.0",
        "curriculum_unit_id": general_unit["id"],
        "facts_sha256": "6" * 64,
        "origin": "PREREQUISITE_PRIMER",
        "scope": "PROJECT_SPECIFIC",
        "writer_alias": "writer-local",
        "primer_concept_key": general_unit["primer_concept_key"],
        "prerequisite_node_id": general_unit["prerequisite_node_id"],
        "concept_epistemic_status": general_unit["concept_epistemic_status"],
        "lesson": {
            "intuition": "A request asks for something.",
            "prerequisite_explanations": [],
            "concept_explanations": [{"concept_key": "request.basics", "explanation": "A request is a question."}],
            "worked_example": "A reader asks for a book.",
            "common_misconception": "A request is not its answer.",
            "learning_check": {
                "question": "What did the reader ask for?",
                "reference_answer": "The reader asked for a book.",
                "hints": ["Look at the reader's question."],
                "rubric": ["Names the book as the requested item."],
            },
        },
    }
    general_learning_unit = {
        "artifact_kind": "general-learning-unit",
        "schema_version": "1.0.0",
        "repository_revision": common["repository_revision"],
        "generated_at": common["generated_at"],
        "snapshot_kind": "worktree",
        "source_metadata": copy.deepcopy(phase4_metadata),
        "source_status": "PASS",
        "source_run_manifest_sha256": phase4_run["manifest_sha256"],
        "unknown_files": 0,
        "input_digests": copy.deepcopy(review_inputs),
        "selected_unit": copy.deepcopy(general_unit),
        "candidate": copy.deepcopy(general_unit_candidate),
        "prepared_facts_sha256": "6" * 64,
        "candidate_sha256": "7" * 64,
        "lesson_markdown_sha256": "8" * 64,
        "answer_book_markdown_sha256": "9" * 64,
        "lesson_status": "DRAFT",
        "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL",
        "epistemic_status": "UNVERIFIED_TEACHING",
        "general_fact_review": "NOT_RUN",
        "beginner_review": "NOT_RUN",
        "answer_book_review": "NOT_RUN",
    }
    general_review_session = {
        "artifact_kind": "general-review-session", "schema_version": "1.0.0",
        "review_session_id": "GENERAL-REVIEW-" + "a" * 64, "review_round": 0,
        "generated_at": common["generated_at"], "repository_revision": common["repository_revision"],
        "snapshot_kind": "worktree", "source_metadata": copy.deepcopy(phase4_metadata), "source_status": "PASS",
        "source_run_manifest_sha256": phase4_run["manifest_sha256"], "unknown_files": 0,
        "input_digests": copy.deepcopy(review_inputs),
        "prompt_versions": {"general_factuality": "1.0.0", "beginner_answer": "1.0.0"},
        "selected_unit_id": general_unit["id"], "selected_unit_origin": general_unit["origin"],
        "selected_unit_scope": general_unit["scope"], "facts_sha256": "1" * 64,
        "candidate_sha256": "2" * 64, "unit_artifact_sha256": "3" * 64,
        "lesson_markdown_sha256": "4" * 64, "answer_book_markdown_sha256": "5" * 64,
        "writer_packet_sha256": "6" * 64, "writer_alias": "writer-local",
        "occurrence_inventory_sha256": "7" * 64, "beginner_scope_inventory_sha256": "8" * 64,
        "factuality_packet_sha256": "9" * 64, "beginner_packet_sha256": "a" * 64,
    }
    gl_occurrence = {
        "occurrence_id": "GL-OCC-" + "b" * 64, "unit_id": general_unit["id"],
        "field": "lesson.intuition", "location": "lesson.intuition", "render_locations": ["lesson.intuition"],
        "text_sha256": "c" * 64,
    }
    gl_scope = {
        "scope_id": "GL-SCOPE-" + "d" * 64, "unit_id": general_unit["id"],
        "scope_kind": "LESSON_SECTION", "section": "intuition", "occurrence_ids": [gl_occurrence["occurrence_id"]],
        "scope_sha256": "e" * 64,
    }
    general_review_report_binding = {
        "review_session_id": general_review_session["review_session_id"],
        "review_session_sha256": "f" * 64, "review_round": 0,
        "repository_revision": common["repository_revision"], "snapshot_kind": "worktree",
        "source_run_manifest_sha256": phase4_run["manifest_sha256"],
        "selected_unit_id": general_unit["id"], "selected_unit_origin": general_unit["origin"],
        "selected_unit_scope": general_unit["scope"], "facts_sha256": "1" * 64,
        "candidate_sha256": "2" * 64, "unit_artifact_sha256": "3" * 64,
        "lesson_markdown_sha256": "4" * 64, "answer_book_markdown_sha256": "5" * 64,
        "writer_alias": "writer-local", "packet_sha256": "9" * 64,
    }
    authority_reference = {
        "reference_id": "reference-1", "title": "Official guide", "publisher": "Standards body",
        "locator": "https://example.org/standard", "edition": None, "authority_class": "STANDARD",
    }
    general_factuality_review = {
        "artifact_kind": "general-factuality-review", "schema_version": "1.0.0",
        "generated_at": common["generated_at"], **general_review_report_binding,
        "reviewer_alias": "facts-local", "reviewer_session_id": "facts-session-1",
        "fresh_context_isolated": True, "source_reference_inspection": True,
        "results": [{**gl_occurrence, "outcome": "SUPPORTED_BY_AUTHORITY", "authority_references": [authority_reference], "findings": []}],
    }
    general_beginner_review = {
        "artifact_kind": "general-beginner-review", "schema_version": "1.0.0",
        "generated_at": common["generated_at"], **general_review_report_binding,
        "reviewer_alias": "beginner-local", "reviewer_session_id": "beginner-session-1",
        "fresh_context_isolated": True, "results": [{**gl_scope, "outcome": "FOLLOWABLE_FOR_BEGINNER", "findings": []}],
    }
    general_review_status = {
        "artifact_kind": "general-review-status", "schema_version": "1.0.0",
        "review_session_id": general_review_session["review_session_id"], "review_session_sha256": "f" * 64,
        "review_round": 0, "generated_at": common["generated_at"],
        "repository_revision": common["repository_revision"], "snapshot_kind": "worktree",
        "source_metadata": copy.deepcopy(phase4_metadata), "source_status": "PASS",
        "source_run_manifest_sha256": phase4_run["manifest_sha256"], "unknown_files": 0,
        "input_digests": copy.deepcopy(review_inputs), "selected_unit_id": general_unit["id"],
        "selected_unit_origin": general_unit["origin"], "selected_unit_scope": general_unit["scope"],
        "facts_sha256": "1" * 64, "candidate_sha256": "2" * 64, "unit_artifact_sha256": "3" * 64,
        "lesson_markdown_sha256": "4" * 64, "answer_book_markdown_sha256": "5" * 64,
        "writer_packet_sha256": "6" * 64, "occurrence_inventory_sha256": "7" * 64,
        "beginner_scope_inventory_sha256": "8" * 64, "factuality_report_sha256": "9" * 64,
        "beginner_report_sha256": "a" * 64, "missing_roles": [], "writer_alias": "writer-local",
        "factuality_reviewer_alias": "facts-local", "beginner_reviewer_alias": "beginner-local",
        "reviewer_contexts_distinct": True, "source_reference_inspection": True,
        "supported_fact_occurrences": 1, "factuality_outcome": "SUPPORTED_BY_AUTHORITY",
        "beginner_outcome": "FOLLOWABLE_FOR_BEGINNER", "lesson_status": "DRAFT", "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL", "epistemic_status": "UNVERIFIED_TEACHING", "general_fact_review": "NOT_RUN",
        "beginner_review": "NOT_RUN", "answer_book_review": "NOT_RUN", "review_state": "REVIEWED_DRAFT",
        "finding_counts": {"BLOCKER": 0, "MAJOR": 0, "MINOR": 0},
    }
    artifacts = {
        "project-index": {
            **common,
            "artifact_kind": "project-index",
            "project": {"name": "replicate-learning", "root": ".", "vcs": "git", "dirty": False, "snapshot_kind": "worktree"},
            "file_count": 1,
            "files": [{"path": "README.md", "bytes": 28229, "sha256": "0" * 64, "tracked": True}],
        },
        "stack-profile": {**common, "artifact_kind": "stack-profile", "detections": []},
        "evidence": {**common, "artifact_kind": "evidence", "items": []},
        "coverage": {
            **common,
            "artifact_kind": "coverage",
            "tracked_file_count": 1,
            "unknown_count": 0,
            "entries": [{"path": "README.md", "surface": "documentation", "classification": "COVERED", "teaching_status": "LOCATED"}],
        },
        "static-analysis": {
            **common,
            "artifact_kind": "static-analysis",
            "analysis_status": "PASS",
            "source_metadata": {
                "snapshot_kind": "worktree",
                "g01_status": "PASS",
                "unknown_files": 0,
                "project_index_sha256": "a" * 64,
                "coverage_sha256": "b" * 64,
            },
            "languages": [{
                "language": "python",
                "analysis_status": "PASS",
                "limitations": [{
                    "code": "SLICE_SCOPE",
                    "reason": "3B1 extracts definitions and imports only.",
                }],
                "files": [{
                    "path": "src/app.py",
                    "sha256": "c" * 64,
                    "status": "ANALYZED",
                    "line_count": 1,
                    "limitations": [],
                }],
            }],
            "symbols": [{
                "id": "SYM-" + "1" * 24,
                "language": "python",
                "kind": "module",
                "name": "app",
                "qualified_name": "app",
                "path": "src/app.py",
                "extraction_method": "python-ast",
                "certainty": "VERIFIED",
                "line_start": 1,
                "line_end": 1,
                "evidence_ids": ["EVID-fixture"],
            }, {
                "id": "SYM-" + "3" * 24,
                "language": "python",
                "kind": "function",
                "name": "serve",
                "qualified_name": "app.serve",
                "path": "src/app.py",
                "extraction_method": "python-ast",
                "certainty": "VERIFIED",
                "line_start": 1,
                "line_end": 1,
                "evidence_ids": ["EVID-fixture"],
            }],
            "relations": [{
                "id": "REL-" + "2" * 24,
                "language": "python",
                "kind": "DEFINES",
                "source_id": "SYM-" + "1" * 24,
                "target_id": "SYM-" + "3" * 24,
                "unresolved_target": None,
                "path": "src/app.py",
                "extraction_method": "python-ast",
                "certainty": "VERIFIED",
                "line_start": 1,
                "line_end": 1,
                "evidence_ids": ["EVID-fixture"],
            }],
        },
        "knowledge-graph": {**common, "artifact_kind": "knowledge-graph", "nodes": [], "edges": []},
        "curriculum": {**common, "artifact_kind": "curriculum", "learner_profile": "BEGINNER", "units": []},
        "quality-report": {
            **common,
            "artifact_kind": "quality-report",
            "mode": "FAST",
            "status": "NOT_RUN",
            "gates": [],
            "critical_gaps": ["Quality gates have not run."],
            "warnings": [],
        },
        "phase3-run": {
            "artifact_kind": "phase3-run",
            "schema_version": "1.0.0",
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "status": "PASS",
            "snapshot_kind": "worktree",
            "source_metadata": {
                "snapshot_kind": "worktree",
                "g01_status": "PASS",
                "unknown_files": 0,
                "project_index_sha256": "a" * 64,
                "coverage_sha256": "b" * 64,
            },
            "members": [
                {"path": "phase2/project-index.json", "artifact_kind": "project-index", "schema_version": "1.1.0", "sha256": "c" * 64},
                {"path": "phase2/coverage.json", "artifact_kind": "coverage", "schema_version": "1.1.0", "sha256": "d" * 64},
                {"path": "phase3a/stack-profile.json", "artifact_kind": "stack-profile", "schema_version": "1.1.0", "sha256": "e" * 64},
                {"path": "phase3a/evidence.json", "artifact_kind": "evidence", "schema_version": "1.1.0", "sha256": "f" * 64},
            ],
            "e1_audit": {
                "status": "PASS",
                "g01": {"before": "PASS", "after": "PASS"},
                "adapters": {
                    name: {
                        "status": "NOT_RUN",
                        "version": None,
                        "applicable_files": 0,
                        "analyzed_files": 0,
                        "skipped_files": 0,
                        "partial_files": 0,
                        "limitations": [],
                    }
                    for name in ("python", "java", "frontend")
                },
                "counts": {
                    "tracked_files": 0,
                    "covered_tracked_files": 0,
                    "unknown_tracked_files": 0,
                    "analyzed_files": 0,
                    "skipped_files": 0,
                    "partial_files": 0,
                    "unreported_source_files": 0,
                    "outside_analyzer_files": 0,
                },
                "limitations": [],
            },
        },
        "claim-candidates": {
            **common,
            "artifact_kind": "claim-candidates",
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_run": copy.deepcopy(phase4_run),
            "phase4_inputs": copy.deepcopy(phase4_inputs),
            "claims": [],
        },
        "claim-evidence": {
            **common,
            "artifact_kind": "claim-evidence",
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_run": copy.deepcopy(phase4_run),
            "phase4_inputs": copy.deepcopy(phase4_inputs),
            "audit_status": "PASS",
            "claims": [],
        },
        "semantic-proposals": {
            **common,
            "artifact_kind": "semantic-proposals",
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_run": copy.deepcopy(phase4_run),
            "phase4_inputs": [
                {"path": "phase4a/evidence.json", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": "3" * 64},
                {"path": "phase4a/knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": "4" * 64},
            ],
            "proposals": [{
                "id": "PROP-" + "5" * 64,
                "category": "concept",
                "kind": "node",
                "label": "A concept",
                "node_type": "Concept",
            }],
        },
        "claim-evidence-graph": {
            **common,
            "artifact_kind": "claim-evidence-graph",
            "schema_version": "1.0.0",
            "status": "PARTIAL",
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_run": {
                "artifact_kind": "phase3-run",
                "schema_version": "1.0.0",
                "manifest_sha256": "0" * 64,
                "status": "PARTIAL",
                "members": copy.deepcopy(phase4_run["members"]),
            },
            "audit_status": "PASS",
            "input_digests": [
                {"role": "base_evidence", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": "1" * 64},
                {"role": "base_graph", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": "2" * 64},
                {"role": "claim_candidates", "artifact_kind": "claim-candidates", "schema_version": "1.1.0", "sha256": "3" * 64},
                {"role": "claim_evidence", "artifact_kind": "claim-evidence", "schema_version": "1.1.0", "sha256": "4" * 64},
                {"role": "semantic_proposals", "artifact_kind": "semantic-proposals", "schema_version": "1.0.0", "sha256": "5" * 64},
            ],
            "nodes": [],
            "edges": [],
        },
        "prerequisite-candidates": {
            **common,
            "artifact_kind": "prerequisite-candidates",
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_run": copy.deepcopy(phase4_run),
            "source_status": "PASS",
            "phase4_inputs": copy.deepcopy(phase5_inputs),
            "nodes": [],
            "edges": [],
        },
        "prerequisite-graph": {
            **common,
            "artifact_kind": "prerequisite-graph",
            "source_status": "PASS",
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "phase4_inputs": copy.deepcopy(phase5_inputs),
            "candidate_input": {
                "artifact_kind": "prerequisite-candidates",
                "schema_version": "1.0.0",
                "sha256": "4" * 64,
            },
            "nodes": [],
            "edges": [],
        },
        "curriculum-candidates": {
            "artifact_kind": "curriculum-candidates",
            "schema_version": "1.0.0",
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "phase4_inputs": copy.deepcopy(phase5_inputs),
            "prerequisite_inputs": [
                {"path": "prerequisite-candidates.json", "artifact_kind": "prerequisite-candidates", "schema_version": "1.0.0", "sha256": "4" * 64},
                {"path": "prerequisite-graph.json", "artifact_kind": "prerequisite-graph", "schema_version": "1.0.0", "sha256": "5" * 64},
            ],
            "units": [{
                "unit_key": "intro",
                "stage": "project-purpose",
                "title": "Project purpose outline",
                "scope": "GENERAL_LEARNING",
                "introduces_concept_keys": [],
                "requires_concept_keys": [],
                "project_refs": {
                    "graph_node_ids": [],
                    "graph_edge_ids": [],
                    "evidence_ids": [],
                    "file_paths": [],
                },
            }],
        },
        "chapter-facts": {
            "artifact_kind": "chapter-facts",
            "schema_version": "1.0.0",
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_status": "PASS",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "unknown_files": 0,
            "input_digests": [
                {"role": role, "artifact_kind": kind, "schema_version": version, "sha256": "a" * 64}
                for role, kind, version in (
                    ("phase4_base_graph", "knowledge-graph", "1.1.0"),
                    ("phase4_base_evidence", "evidence", "1.2.0"),
                    ("phase4_semantic_proposals", "semantic-proposals", "1.0.0"),
                    ("phase4_claim_candidates", "claim-candidates", "1.1.0"),
                    ("phase4_claim_evidence", "claim-evidence", "1.1.0"),
                    ("phase4_claim_evidence_graph", "claim-evidence-graph", "1.0.0"),
                    ("phase5_prerequisite_candidates", "prerequisite-candidates", "1.0.0"),
                    ("phase5_prerequisite_graph", "prerequisite-graph", "1.0.0"),
                    ("phase5_curriculum_candidates", "curriculum-candidates", "1.0.0"),
                    ("phase5_curriculum", "curriculum", "1.1.0"),
                )
            ],
            "selected_unit": {
                "curriculum_unit_id": "CURRICULUM-UNIT-" + "1" * 64,
                "chapter_id": "CHAPTER-" + "2" * 64,
                "stage": "project-purpose",
                "title": "Project purpose",
                "scope": "PROJECT_SPECIFIC",
                "prerequisite_concept_keys": [],
                "project_refs": {key: [] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")},
            },
            "claims": [],
            "evidence": [],
        },
        "exercise-bank": {
            "artifact_kind": "exercise-bank",
            "schema_version": "1.0.0",
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "snapshot_kind": "worktree",
            "chapter_id": "CHAPTER-" + "2" * 64,
            "curriculum_unit_id": "CURRICULUM-UNIT-" + "1" * 64,
            "records": [
                {
                    "id": "EX-CHAPTER-" + "2" * 64 + "-recall-purpose",
                    "objective_key": "recall-purpose",
                    "type": "recall",
                    "prompt": "What is the project for?",
                    "prompt_sha256": "3" * 64,
                    "prerequisite_concept_keys": [],
                    "claim_ids": [],
                    "evidence_ids": [],
                    "source_refs": {key: [] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")},
                    "answer_status": "NOT_AUTHORED",
                },
                {
                    "id": "EX-CHAPTER-" + "2" * 64 + "-trace-flow",
                    "objective_key": "trace-flow",
                    "type": "trace_predict",
                    "prompt": "What happens next?",
                    "prompt_sha256": "4" * 64,
                    "prerequisite_concept_keys": [],
                    "claim_ids": [],
                    "evidence_ids": [],
                    "source_refs": {key: [] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")},
                    "answer_status": "NOT_AUTHORED",
                },
            ],
        },
        "answer-candidates": {
            "artifact_kind": "answer-candidates",
            "schema_version": "1.0.0",
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "snapshot_kind": "worktree",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "chapter_id": review_chapter_id,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "review_session_id": review_id,
            "review_session_sha256": "8" * 64,
            "review_status_sha256": "b" * 64,
            "writer_packet_sha256": "c" * 64,
            "writer_alias": "writer-local",
            "records": [
                {
                    "question_id": "EX-CHAPTER-" + "2" * 64 + "-recall-purpose",
                    "prompt_sha256": "3" * 64,
                    "worked_steps": [{"kind": "GENERAL", "text": "Start from the inputs."}],
                    "progressive_hints": [{
                        "level": 1,
                        "fragments": [{"kind": "GENERAL", "text": "Name the first step."}],
                    }],
                    "common_mistakes": [{"kind": "GENERAL", "text": "Skipping the first step."}],
                    "rubric": [{"kind": "GENERAL", "text": "Explain each step."}],
                    "acceptable_tradeoffs": [],
                },
                {
                    "question_id": "EX-CHAPTER-" + "2" * 64 + "-trace-flow",
                    "prompt_sha256": "4" * 64,
                    "worked_steps": [{"kind": "GENERAL", "text": "Follow the request."}],
                    "progressive_hints": [{
                        "level": 1,
                        "fragments": [{"kind": "GENERAL", "text": "Find the entry point."}],
                    }],
                    "common_mistakes": [{"kind": "GENERAL", "text": "Assuming the next step."}],
                    "rubric": [{"kind": "GENERAL", "text": "Connect the stages."}],
                    "acceptable_tradeoffs": [],
                },
            ],
        },
        "chapter-draft-status": {
            "artifact_kind": "chapter-draft-status",
            "schema_version": "1.0.0",
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_status": "PASS",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "unknown_files": 0,
            "chapter_id": "CHAPTER-" + "2" * 64,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "chapter_status": "DRAFT",
            "structural_status": "PASS",
            "overall_status": "PARTIAL",
            "check_results": {"reference_integrity": "PASS", "heading_order": "PASS", "claim_footnotes": "PASS", "exercise_sync": "PASS", "excerpt_identity": "NOT_RUN"},
            "errors": [],
            "semantic_entailment": "NOT_RUN",
            "evidence_verifier": "NOT_RUN",
            "beginner_critic": "NOT_RUN",
            "privacy_review": "NOT_RUN",
            "g08": "PARTIAL",
            "g09": "PARTIAL",
            "g11": "NOT_RUN",
        },
        "chapter-review-session": review_session,
        "chapter-evidence-review": {
            "artifact_kind": "chapter-evidence-review",
            "schema_version": "1.0.0",
            "generated_at": common["generated_at"],
            **review_identity,
            "reviewer_alias": "verifier-local",
            "reviewer_session_id": "verifier-session-01",
            "fresh_context_isolated": True,
            "direct_source_inspection": True,
            "packet_sha256": "c" * 64,
            "results": [{
                "occurrence_id": review_occurrence,
                "line_number": 1,
                "line_sha256": review_line_sha,
                "kind": "GENERAL",
                "claim_id": None,
                "evidence_ids": [],
                "outcome": "GENERAL_TEACHING",
                "findings": [],
            }],
        },
        "chapter-beginner-review": {
            "artifact_kind": "chapter-beginner-review",
            "schema_version": "1.0.0",
            "generated_at": common["generated_at"],
            **review_identity,
            "reviewer_alias": "critic-local",
            "reviewer_session_id": "critic-session-01",
            "fresh_context_isolated": True,
            "packet_sha256": "d" * 64,
            "results": review_beginner_results,
        },
        "chapter-review-status": {
            "artifact_kind": "chapter-review-status",
            "schema_version": "1.0.0",
            "review_session_id": review_id,
            "review_session_sha256": "8" * 64,
            "review_round": 0,
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_status": "PASS",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "unknown_files": 0,
            "chapter_id": review_chapter_id,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "evidence_report_sha256": "c" * 64,
            "beginner_report_sha256": "d" * 64,
            "writer_alias": "writer-local",
            "evidence_reviewer_alias": "verifier-local",
            "beginner_reviewer_alias": "critic-local",
            "reviewer_contexts_distinct": True,
            "verifier_source_inspection": True,
            "evidence_outcome": "CONSISTENT_WITH_CITED_EVIDENCE",
            "beginner_outcome": "FOLLOWABLE_FOR_DECLARED_BEGINNER",
            "review_state": "REVIEWED_DRAFT",
            "overall_status": "PARTIAL",
            "finding_counts": {"BLOCKER": 0, "MAJOR": 0, "MINOR": 0},
        },
        "chapter-repair-triage": {
            "artifact_kind": "chapter-repair-triage",
            "schema_version": "1.0.0",
            "repository_revision": common["repository_revision"],
            "generated_at": common["generated_at"],
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_status": "PASS",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "unknown_files": 0,
            "chapter_id": review_chapter_id,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "review_session_id": review_id,
            "review_session_sha256": "8" * 64,
            "review_status_sha256": "a" * 64,
            "evidence_report_sha256": "c" * 64,
            "beginner_report_sha256": "d" * 64,
            "review_state": "REPAIR_REQUIRED",
            "overall_status": "PARTIAL",
            "triage_id": "TRIAGE-" + "b" * 64,
            "overall_disposition": "UPSTREAM_CLAIM_REQUIRED",
            "finding_counts": {"TEACHING_EDIT_POSSIBLE": 0, "UPSTREAM_CLAIM_REQUIRED": 0},
            "findings": [],
        },
        "answer-review-session": {
            "artifact_kind": "answer-review-session",
            "schema_version": "1.0.0",
            "review_session_id": "ANSWER-REVIEW-" + "7" * 64,
            "review_round": 0,
            "generated_at": common["generated_at"],
            "repository_revision": common["repository_revision"],
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_status": "PARTIAL",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "unknown_files": 3,
            "phase3_member_digests": [copy.deepcopy(review_session["phase3_member_digests"][0])],
            "input_digests": copy.deepcopy(review_inputs),
            "prompt_versions": {"evidence_verifier": "1.0.0", "beginner_reviewer": "1.0.0"},
            "chapter_id": review_chapter_id,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "chapter_review_session_sha256": "8" * 64,
            "chapter_review_status_sha256": "b" * 64,
            "answer_candidates_sha256": "1" * 64,
            "answer_bank_sha256": "a" * 64,
            "answer_book_sha256": "2" * 64,
            "writer_alias": "writer-local",
            "answer_book_status": "DRAFT",
            "overall_status": "PARTIAL",
            "answer_evidence_review": "NOT_RUN",
            "answer_beginner_review": "NOT_RUN",
            "evidence_inventory_sha256": "c" * 64,
            "beginner_inventory_sha256": "d" * 64,
            "evidence_packet_sha256": "e" * 64,
            "beginner_packet_sha256": "f" * 64,
        },
        "answer-evidence-review": {
            "artifact_kind": "answer-evidence-review",
            "schema_version": "1.0.0",
            "generated_at": common["generated_at"],
            "review_session_id": "ANSWER-REVIEW-" + "7" * 64,
            "review_session_sha256": "8" * 64,
            "review_round": 0,
            "repository_revision": common["repository_revision"],
            "snapshot_kind": "worktree",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "chapter_id": review_chapter_id,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "chapter_review_session_sha256": "8" * 64,
            "chapter_review_status_sha256": "b" * 64,
            "answer_candidates_sha256": "1" * 64,
            "answer_bank_sha256": "a" * 64,
            "answer_book_sha256": "2" * 64,
            "writer_alias": "writer-local",
            "reviewer_alias": "evidence-local",
            "reviewer_session_id": "evidence-session-01",
            "fresh_context_isolated": True,
            "direct_source_inspection": True,
            "packet_sha256": "e" * 64,
            "results": [{
                "occurrence_id": "A-OCC-" + "f" * 64,
                "question_id": "EX-CHAPTER-" + "2" * 64 + "-recall-purpose",
                "section": "worked_steps",
                "fragment_index": 0,
                "hint_level": None,
                "text_sha256": "3" * 64,
                "kind": "GENERAL",
                "claim_id": None,
                "evidence_ids": [],
                "outcome": "GENERAL_TEACHING",
                "findings": [],
            }],
        },
        "answer-beginner-review": {
            "artifact_kind": "answer-beginner-review",
            "schema_version": "1.0.0",
            "generated_at": common["generated_at"],
            "review_session_id": "ANSWER-REVIEW-" + "7" * 64,
            "review_session_sha256": "8" * 64,
            "review_round": 0,
            "repository_revision": common["repository_revision"],
            "snapshot_kind": "worktree",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "chapter_id": review_chapter_id,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "chapter_review_session_sha256": "8" * 64,
            "chapter_review_status_sha256": "b" * 64,
            "answer_candidates_sha256": "1" * 64,
            "answer_bank_sha256": "a" * 64,
            "answer_book_sha256": "2" * 64,
            "writer_alias": "writer-local",
            "reviewer_alias": "beginner-local",
            "reviewer_session_id": "beginner-session-01",
            "fresh_context_isolated": True,
            "packet_sha256": "f" * 64,
            "results": [{
                "scope_id": "A-SCOPE-" + "f" * 64,
                "question_id": "EX-CHAPTER-" + "2" * 64 + "-recall-purpose",
                "scope_kind": "ANSWER_SECTION",
                "section": "worked_steps",
                "fragment_occurrence_ids": ["A-OCC-" + "f" * 64],
                "scope_sha256": "3" * 64,
                "outcome": "FOLLOWABLE_FOR_BEGINNER",
                "findings": [],
            }],
        },
        "answer-review-status": {
            "artifact_kind": "answer-review-status",
            "schema_version": "1.0.0",
            "review_session_id": "ANSWER-REVIEW-" + "7" * 64,
            "review_session_sha256": "8" * 64,
            "review_round": 0,
            "generated_at": common["generated_at"],
            "repository_revision": common["repository_revision"],
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_status": "PARTIAL",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "unknown_files": 3,
            "chapter_id": review_chapter_id,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "chapter_review_session_sha256": "8" * 64,
            "chapter_review_status_sha256": "b" * 64,
            "answer_candidates_sha256": "1" * 64,
            "answer_bank_sha256": "a" * 64,
            "answer_book_sha256": "2" * 64,
            "evidence_report_sha256": "e" * 64,
            "beginner_report_sha256": "f" * 64,
            "writer_alias": "writer-local",
            "evidence_reviewer_alias": "evidence-local",
            "beginner_reviewer_alias": "beginner-local",
            "reviewer_contexts_distinct": True,
            "verifier_source_inspection": True,
            "evidence_outcome": "CONSISTENT_WITH_CITED_EVIDENCE",
            "beginner_outcome": "FOLLOWABLE_FOR_BEGINNER",
            "answer_book_status": "DRAFT",
            "overall_status": "PARTIAL",
            "review_state": "REVIEWED_DRAFT",
            "finding_counts": {"BLOCKER": 0, "MAJOR": 0, "MINOR": 0},
        },
        "general-unit-facts": general_unit_facts,
        "general-unit-candidate": general_unit_candidate,
        "general-learning-unit": general_learning_unit,
        "general-review-session": general_review_session,
        "general-factuality-review": general_factuality_review,
        "general-beginner-review": general_beginner_review,
        "general-review-status": general_review_status,
        "curriculum-run-plan": {
            "artifact_kind": "curriculum-run-plan",
            "schema_version": "1.0.0",
            "run_plan_id": "CURRICULUM-RUN-PLAN-" + "a" * 64,
            "repository_revision": common["repository_revision"],
            "snapshot_kind": "worktree",
            "source_metadata": copy.deepcopy(phase4_metadata),
            "source_status": "PASS",
            "source_run_manifest_sha256": phase4_run["manifest_sha256"],
            "unknown_files": 0,
            "generated_at": common["generated_at"],
            "curriculum_schema_version": "1.1.0",
            "curriculum_sha256": "b" * 64,
            "unit_count": 2,
            "input_digests": [
                {"role": role, "artifact_kind": kind, "schema_version": version, "sha256": "c" * 64}
                for role, kind, version in (
                    ("phase4_base_graph", "knowledge-graph", "1.1.0"),
                    ("phase4_base_evidence", "evidence", "1.2.0"),
                    ("phase4_semantic_proposals", "semantic-proposals", "1.0.0"),
                    ("phase4_claim_candidates", "claim-candidates", "1.1.0"),
                    ("phase4_claim_evidence", "claim-evidence", "1.1.0"),
                    ("phase4_claim_evidence_graph", "claim-evidence-graph", "1.0.0"),
                    ("phase5_prerequisite_candidates", "prerequisite-candidates", "1.0.0"),
                    ("phase5_prerequisite_graph", "prerequisite-graph", "1.0.0"),
                    ("phase5_curriculum_candidates", "curriculum-candidates", "1.0.0"),
                    ("phase5_curriculum", "curriculum", "1.1.0"),
                )
            ],
            "units": [
                {
                    "position": 1,
                    "unit_identity": {
                        "id": "CURRICULUM-UNIT-" + "d" * 64,
                        "order": 1,
                        "title": "General concept",
                        "kind": "prerequisite",
                        "scope": "GENERAL_LEARNING",
                        "depth": 1,
                        "content_status": "OUTLINE_ONLY",
                        "epistemic_status": "UNVERIFIED_TEACHING",
                        "prerequisite_ids": [],
                        "introduces_concept_keys": ["general.concept"],
                        "requires_concept_keys": [],
                        "project_refs": {key: [] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")},
                        "origin": "CANDIDATE",
                        "unit_key": "general-concept",
                    },
                    "route": "D2A_GENERAL_LEARNING",
                    "state": "PLANNED",
                    "reason_codes": ["ROUTE_GENERAL_CANDIDATE"],
                    "blocked_by": [],
                },
                {
                    "position": 2,
                    "unit_identity": {
                        "id": "CURRICULUM-PRIMER-" + "e" * 64,
                        "order": 2,
                        "title": "Primer concept",
                        "kind": "prerequisite",
                        "scope": "PROJECT_SPECIFIC",
                        "depth": 1,
                        "content_status": "OUTLINE_ONLY",
                        "epistemic_status": "UNVERIFIED_TEACHING",
                        "prerequisite_ids": ["CURRICULUM-UNIT-" + "d" * 64],
                        "introduces_concept_keys": [],
                        "requires_concept_keys": ["general.concept"],
                        "project_refs": {key: [] for key in ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")},
                        "origin": "PREREQUISITE_PRIMER",
                        "primer_concept_key": "general.concept",
                        "prerequisite_node_id": "PREREQ-NODE-" + "f" * 64,
                        "concept_epistemic_status": "UNVERIFIED_TEACHING",
                    },
                    "route": "D2A_GENERAL_LEARNING",
                    "state": "PLANNED",
                    "reason_codes": ["ROUTE_PREREQUISITE_PRIMER"],
                    "blocked_by": [],
                },
            ],
        },
    }
    plan = artifacts["curriculum-run-plan"]
    state_units = []
    for plan_row in plan["units"]:
        identity = plan_row["unit_identity"]
        state_units.append({
            "position": plan_row["position"],
            "unit_id": identity["id"],
            "unit_identity_sha256": hashlib.sha256(json.dumps(
                identity, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
            ).encode("utf-8")).hexdigest(),
            "route": plan_row["route"],
            "plan_state": plan_row["state"],
            "prerequisite_ids": identity["prerequisite_ids"],
            "reason_codes": plan_row["reason_codes"],
            "blocked_by": plan_row["blocked_by"],
            "execution_state": "PLANNED",
            "attempt_id": None,
            "artifact_digests": [],
        })
    artifacts["curriculum-run-state"] = {
        "artifact_kind": "curriculum-run-state",
        "schema_version": "1.0.0",
        "generated_at": plan["generated_at"],
        "run_state_id": "CURRICULUM-RUN-STATE-" + "1" * 64,
        "run_plan_id": plan["run_plan_id"],
        "run_plan_sha256": "2" * 64,
        "state_revision": 1,
        "previous_state_sha256": None,
        "repository_revision": plan["repository_revision"],
        "snapshot_kind": plan["snapshot_kind"],
        "source_metadata": copy.deepcopy(plan["source_metadata"]),
        "source_status": plan["source_status"],
        "source_run_manifest_sha256": plan["source_run_manifest_sha256"],
        "unknown_files": plan["unknown_files"],
        "curriculum_schema_version": plan["curriculum_schema_version"],
        "curriculum_sha256": plan["curriculum_sha256"],
        "unit_count": plan["unit_count"],
        "input_digests": copy.deepcopy(plan["input_digests"]),
        "run_state": "READY",
        "units": state_units,
    }
    assembly_units = []
    for position, plan_row in enumerate(plan["units"], start=1):
        identity = copy.deepcopy(plan_row["unit_identity"])
        selected = position == 1
        assembly_units.append({
            "position": position,
            "unit_id": identity["id"],
            "unit_identity": identity,
            "unit_identity_sha256": hashlib.sha256(json.dumps(
                identity, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
            ).encode("utf-8")).hexdigest(),
            "route": plan_row["route"],
            "plan_state": plan_row["state"],
            "reason_codes": list(plan_row["reason_codes"]),
            "blocked_by": list(plan_row["blocked_by"]),
            "prerequisite_ids": list(identity["prerequisite_ids"]),
            "execution_state": "REVIEWED_DRAFT" if selected else "PLANNED",
            "selection_state": "SELECTED" if selected else "NOT_SELECTED",
            "attempt_id": "attempt-0001" if selected else None,
            "review_state": "REVIEWED_DRAFT" if selected else None,
            "review_status_sha256": "a" * 64 if selected else None,
            "artifact_digests": [],
            "answer_status": "INCLUDED" if selected and plan_row["route"] == "D2A_GENERAL_LEARNING" else "NOT_SELECTED",
        })
    artifacts["whole-book-assembly"] = {
        "artifact_kind": "whole-book-assembly",
        "schema_version": "1.0.0",
        "repository_revision": common["repository_revision"],
        "generated_at": common["generated_at"],
        "assembly_id": "WHOLE-BOOK-ASSEMBLY-" + "8" * 64,
        "run_plan_id": plan["run_plan_id"],
        "run_plan_sha256": "2" * 64,
        "run_state_id": "CURRICULUM-RUN-STATE-" + "1" * 64,
        "run_state_revision": 1,
        "run_state_sha256": "3" * 64,
        "run_state_schema_version": "1.0.0",
        "snapshot_kind": plan["snapshot_kind"],
        "source_metadata": copy.deepcopy(plan["source_metadata"]),
        "source_status": plan["source_status"],
        "source_run_manifest_sha256": plan["source_run_manifest_sha256"],
        "unknown_files": plan["unknown_files"],
        "curriculum_schema_version": plan["curriculum_schema_version"],
        "curriculum_sha256": plan["curriculum_sha256"],
        "input_digests": copy.deepcopy(plan["input_digests"]),
        "unit_count": len(assembly_units),
        "selected_count": 1,
        "units": assembly_units,
        "lesson_status": "DRAFT",
        "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL",
        "cross_chapter_audit": "NOT_RUN",
        "outputs": [
            {"role": "whole_book", "path": "WHOLE-BOOK.md", "sha256": "4" * 64},
            {"role": "answer_book", "path": "WHOLE-ANSWER-BOOK.md", "sha256": "5" * 64},
            {"role": "quality_and_gaps", "path": "QUALITY-AND-GAPS.md", "sha256": "6" * 64},
        ],
    }
    assembly = artifacts["whole-book-assembly"]
    selected_row = next(row for row in assembly["units"] if row["selection_state"] == "SELECTED")
    artifacts["whole-book-consistency-audit"] = {
        "artifact_kind": "whole-book-consistency-audit",
        "schema_version": "1.0.0",
        "repository_revision": assembly["repository_revision"],
        "generated_at": assembly["generated_at"],
        "audit_id": "WHOLE-BOOK-AUDIT-" + "9" * 64,
        "assembly_id": assembly["assembly_id"],
        "assembly_manifest_sha256": "7" * 64,
        "run_plan_id": assembly["run_plan_id"],
        "run_plan_sha256": assembly["run_plan_sha256"],
        "run_state_id": assembly["run_state_id"],
        "run_state_revision": assembly["run_state_revision"],
        "run_state_sha256": assembly["run_state_sha256"],
        "run_state_schema_version": assembly["run_state_schema_version"],
        "snapshot_kind": assembly["snapshot_kind"],
        "source_status": assembly["source_status"],
        "source_run_manifest_sha256": assembly["source_run_manifest_sha256"],
        "unknown_files": assembly["unknown_files"],
        "curriculum_sha256": assembly["curriculum_sha256"],
        "input_digests": copy.deepcopy(assembly["input_digests"]),
        "selected_units": [{
            "position": selected_row["position"],
            "unit_id": selected_row["unit_id"],
            "route": selected_row["route"],
            "attempt_id": selected_row["attempt_id"],
            "prerequisite_ids": list(selected_row["prerequisite_ids"]),
            "review_status_sha256": selected_row["review_status_sha256"],
            "chapter_facts_sha256": None,
            "answer_status": selected_row["answer_status"],
            "d1b_review_state": None,
        }],
        "d3a_outputs": copy.deepcopy(assembly["outputs"]),
        "check_counts": {
            "anchor_count": 0,
            "toc_link_count": 0,
            "same_book_fragment_count": 0,
            "prerequisite_count": 0,
            "claim_reference_count": 0,
            "repeated_claim_id_count": 0,
            "finding_count": 0,
        },
        "findings": [],
        "audit_status": "REPORT_ONLY",
        "semantic_consistency": "NOT_RUN",
        "glossary_review": "NOT_RUN",
        "lesson_status": "DRAFT",
        "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL",
        "report_sha256": "8" * 64,
    }
    artifacts["development-plan"] = json.loads(
        (FIXTURE_ROOT / "development-plan.json").read_text(encoding="utf-8")
    )
    return artifacts


class SchemaDocumentTests(unittest.TestCase):
    def test_v1_contains_exactly_the_registered_versioned_schemas(self):
        actual = {path.name for path in SCHEMA_ROOT.glob("*.schema.json")}

        self.assertEqual(
            {f"{kind}.schema.json" for kind in EXPECTED_KINDS}
            | {
                "static-analysis-v1.1.schema.json",
                "static-analysis-v1.2.schema.json",
                "static-analysis-v1.3.schema.json",
                "static-analysis-v1.4.schema.json",
                "static-analysis-v1.5.schema.json",
                "knowledge-graph-v1.1.schema.json",
                "knowledge-graph-v1.2.schema.json",
                "evidence-v1.2.schema.json",
                "evidence-v1.3.schema.json",
                "evidence-v1.4.schema.json",
                "phase3-run-v1.1.schema.json",
                "claim-candidates-v1.1.schema.json",
                "claim-candidates-v1.2.schema.json",
                "claim-evidence-v1.1.schema.json",
                "claim-evidence-v1.2.schema.json",
                "claim-evidence-graph-v1.1.schema.json",
                "semantic-proposals-v1.1.schema.json",
                "curriculum-v1.1.schema.json",
                "curriculum-v1.2.schema.json",
                "prerequisite-candidates-v1.1.schema.json",
                "prerequisite-graph-v1.1.schema.json",
                "curriculum-candidates-v1.1.schema.json",
                "curriculum-run-state-v1.1.schema.json",
                "exercise-bank-v1.1.schema.json",
                "general-learning-unit-v1.1.schema.json",
            },
            actual,
        )

    def test_every_schema_is_parseable_and_requires_common_identity_fields(self):
        for kind in sorted(EXPECTED_KINDS):
            with self.subTest(kind=kind):
                path = SCHEMA_ROOT / f"{kind}.schema.json"
                schema = json.loads(path.read_text(encoding="utf-8"))

                self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
                self.assertEqual(f"https://project-deepdive.dev/schemas/v1/{kind}.schema.json", schema["$id"])
                self.assertEqual("object", schema["type"])
                self.assertFalse(schema["additionalProperties"])
                common_identity = {"artifact_kind", "schema_version"}
                if kind != "general-unit-candidate":
                    common_identity |= {"repository_revision", "generated_at"}
                self.assertEqual(common_identity, common_identity & set(schema["required"]))
                self.assertEqual({"const": kind}, schema["properties"]["artifact_kind"])
                if kind in SCANNER_KINDS | PHASE3A_KINDS:
                    self.assertEqual(
                        {"enum": SUPPORTED_V1_VERSIONS},
                        schema["properties"]["schema_version"],
                    )
                else:
                    self.assertEqual({"const": "1.0.0"}, schema["properties"]["schema_version"])

        v11_path = schema_path("static-analysis", "1.1.0")
        v11_schema = json.loads(v11_path.read_text(encoding="utf-8"))
        self.assertEqual(
            "https://project-deepdive.dev/schemas/v1/static-analysis-1.1.schema.json",
            v11_schema["$id"],
        )
        self.assertEqual({"const": "1.1.0"}, v11_schema["properties"]["schema_version"])
        v12_schema = json.loads(schema_path("static-analysis", "1.2.0").read_text(encoding="utf-8"))
        self.assertEqual(
            "https://project-deepdive.dev/schemas/v1/static-analysis-1.2.schema.json",
            v12_schema["$id"],
        )
        self.assertEqual({"const": "1.2.0"}, v12_schema["properties"]["schema_version"])
        v13_schema = json.loads(schema_path("static-analysis", "1.3.0").read_text(encoding="utf-8"))
        self.assertEqual(
            "https://project-deepdive.dev/schemas/v1/static-analysis-1.3.schema.json",
            v13_schema["$id"],
        )
        self.assertEqual({"const": "1.3.0"}, v13_schema["properties"]["schema_version"])
        self.assertEqual(
            {
                "stack_profile_sha256",
                "phase3a_evidence_sha256",
                "java_analysis_v12_sha256",
                "java_evidence_v12_sha256",
            },
            set(v13_schema["required"])
            & {
                "stack_profile_sha256",
                "phase3a_evidence_sha256",
                "java_analysis_v12_sha256",
                "java_evidence_v12_sha256",
            },
        )
        v14_schema = json.loads(schema_path("static-analysis", "1.4.0").read_text(encoding="utf-8"))
        self.assertEqual(
            "https://project-deepdive.dev/schemas/v1/static-analysis-1.4.schema.json",
            v14_schema["$id"],
        )
        self.assertEqual({"const": "1.4.0"}, v14_schema["properties"]["schema_version"])


class ArtifactValidationTests(unittest.TestCase):
    def test_general_learning_unit_v11_is_registered_additively(self):
        self.assertEqual("general-learning-unit.schema.json", schema_path("general-learning-unit", "1.0.0").name)
        self.assertEqual("general-learning-unit-v1.1.schema.json", schema_path("general-learning-unit", "1.1.0").name)

        legacy = minimal_artifacts()["general-learning-unit"]
        validate_artifact(legacy)
        current = copy.deepcopy(legacy)
        current["schema_version"] = "1.1.0"
        validate_artifact(current)

    def test_whole_book_consistency_audit_is_registered_only_at_v1_0(self):
        try:
            registered = schema_path("whole-book-consistency-audit", "1.0.0")
        except ArtifactValidationError as exc:
            self.fail(f"whole-book-consistency-audit 1.0.0 must be registered: {exc}")
        self.assertEqual("whole-book-consistency-audit.schema.json", registered.name)
        with self.assertRaises(ArtifactValidationError):
            schema_path("whole-book-consistency-audit", "1.1.0")

    def test_whole_book_consistency_audit_remains_report_only(self):
        artifact = minimal_artifacts()["whole-book-consistency-audit"]
        validate_artifact(artifact)
        self.assertEqual("REPORT_ONLY", artifact["audit_status"])
        self.assertEqual("NOT_RUN", artifact["semantic_consistency"])
        self.assertEqual("NOT_RUN", artifact["glossary_review"])
        self.assertEqual("PARTIAL", artifact["overall_status"])

    def test_whole_book_assembly_is_registered_only_at_v1_0(self):
        try:
            registered = schema_path("whole-book-assembly", "1.0.0")
        except ArtifactValidationError as exc:
            self.fail(f"whole-book-assembly 1.0.0 must be registered: {exc}")
        self.assertEqual("whole-book-assembly.schema.json", registered.name)
        with self.assertRaises(ArtifactValidationError):
            schema_path("whole-book-assembly", "1.1.0")

    def test_d2a2_writer_alias_matches_d2a_candidate_length_contract(self):
        artifacts = minimal_artifacts()
        review_kinds = (
            "general-review-session",
            "general-factuality-review",
            "general-beginner-review",
            "general-review-status",
        )
        for kind in review_kinds:
            with self.subTest(kind=kind):
                artifact = copy.deepcopy(artifacts[kind])
                artifact["writer_alias"] = "W" * 80
                validate_artifact(artifact)

                artifact["writer_alias"] = "W" * 81
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(artifact)

                artifact["writer_alias"] = ""
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(artifact)

    def test_phase6a_artifact_contracts_are_registered_as_opt_in_v1(self):
        expected = {
            "chapter-facts": "chapter-facts.schema.json",
            "chapter-draft-status": "chapter-draft-status.schema.json",
            "general-unit-facts": "general-unit-facts.schema.json",
            "general-unit-candidate": "general-unit-candidate.schema.json",
            "general-review-session": "general-review-session.schema.json",
            "general-factuality-review": "general-factuality-review.schema.json",
            "general-beginner-review": "general-beginner-review.schema.json",
            "general-review-status": "general-review-status.schema.json",
        }
        for kind, filename in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(filename, schema_path(kind, "1.0.0").name)
                with self.assertRaises(ArtifactValidationError):
                    schema_path(kind, "1.1.0")
        self.assertEqual("exercise-bank-v1.1.schema.json", schema_path("exercise-bank", "1.1.0").name)
        self.assertEqual("exercise-bank.schema.json", schema_path("exercise-bank", "1.0.0").name)
        self.assertEqual("answer-candidates.schema.json", schema_path("answer-candidates", "1.0.0").name)
        with self.assertRaises(ArtifactValidationError):
            schema_path("answer-candidates", "1.1.0")
        self.assertEqual("curriculum.schema.json", schema_path("curriculum", "1.0.0").name)
        self.assertEqual("curriculum-v1.1.schema.json", schema_path("curriculum", "1.1.0").name)

    def test_phase6a_schemas_are_strict_and_status_digest_is_optional(self):
        for kind in ("chapter-facts", "exercise-bank", "chapter-draft-status"):
            with self.subTest(kind=kind):
                artifact = minimal_artifacts()[kind]
                self.assertIs(artifact, validate_artifact(artifact))
                invalid = copy.deepcopy(artifact)
                invalid["unexpected"] = True
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(invalid)

        status = copy.deepcopy(minimal_artifacts()["chapter-draft-status"])
        status["exercise_bank_sha256"] = "7" * 64
        self.assertIs(status, validate_artifact(status))

        status["check_results"]["reference_integrity"] = "NOT_RUN"
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(status)

    def test_answer_book_candidate_and_enriched_bank_are_additive_strict_versions(self):
        candidate = minimal_artifacts()["answer-candidates"]
        self.assertIs(candidate, validate_artifact(candidate))
        invalid_candidate = copy.deepcopy(candidate)
        invalid_candidate["unexpected"] = True
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(invalid_candidate)

        bank = copy.deepcopy(minimal_artifacts()["exercise-bank"])
        candidate_by_id = {item["question_id"]: item for item in candidate["records"]}
        bank["schema_version"] = "1.1.0"
        bank.update({
            "source_run_manifest_sha256": "0" * 64,
            "chapter_sha256": "5" * 64,
            "chapter_facts_sha256": "6" * 64,
            "exercise_bank_sha256": "9" * 64,
            "review_session_id": "REVIEW-" + "7" * 64,
            "review_session_sha256": "8" * 64,
            "review_status_sha256": "b" * 64,
            "writer_packet_sha256": "c" * 64,
            "writer_alias": "writer-local",
            "answer_book_status": "DRAFT",
            "overall_status": "PARTIAL",
            "source_status": "PASS",
            "unknown_files": 0,
            "integrity_checks": {
                "input_bindings": "PASS",
                "question_identity": "PASS",
                "answer_shape": "PASS",
                "claim_reference_integrity": "PASS",
            },
            "answer_evidence_review": "NOT_RUN",
            "answer_beginner_review": "NOT_RUN",
            "input_digests": [
                {"role": role, "sha256": char * 64}
                for role, char in (
                    ("chapter", "1"), ("chapter_facts", "2"), ("exercise_bank", "3"),
                    ("review_session", "4"), ("review_status", "5"),
                    ("writer_packet", "6"), ("answer_candidates", "7"),
                )
            ],
            "claim_evidence_catalog": [],
        })
        for record in bank["records"]:
            answer = candidate_by_id[record["id"]]
            record.update({
                "answer_status": "AUTHORED",
                "worked_steps": answer["worked_steps"],
                "progressive_hints": answer["progressive_hints"],
                "common_mistakes": answer["common_mistakes"],
                "rubric": answer["rubric"],
                "acceptable_tradeoffs": answer["acceptable_tradeoffs"],
            })
        self.assertIs(bank, validate_artifact(bank))
        invalid_bank = copy.deepcopy(bank)
        invalid_bank["answer_evidence_review"] = "PASS"
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(invalid_bank)

        legacy = minimal_artifacts()["exercise-bank"]
        self.assertIs(legacy, validate_artifact(legacy))
        self.assertTrue(all(item["answer_status"] == "NOT_AUTHORED" for item in legacy["records"]))

    def test_phase6b1_review_artifacts_are_additive_strict_v1_contracts(self):
        for kind in (
            "chapter-review-session", "chapter-evidence-review",
            "chapter-beginner-review", "chapter-review-status",
        ):
            with self.subTest(kind=kind):
                artifact = minimal_artifacts()[kind]
                self.assertEqual(f"{kind}.schema.json", schema_path(kind, "1.0.0").name)
                self.assertIs(artifact, validate_artifact(artifact))
                invalid = copy.deepcopy(artifact)
                invalid["unexpected"] = True
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(invalid)
                for version in ("1.1.0", "2.0.0"):
                    with self.assertRaises(ArtifactValidationError):
                        schema_path(kind, version)
        legacy_status = minimal_artifacts()["chapter-draft-status"]
        self.assertEqual("NOT_RUN", legacy_status["evidence_verifier"])
        self.assertEqual("NOT_RUN", legacy_status["beginner_critic"])

    def test_phase6b2_triage_is_additive_strict_v1_contract(self):
        artifact = minimal_artifacts()["chapter-repair-triage"]
        self.assertEqual("chapter-repair-triage.schema.json", schema_path("chapter-repair-triage", "1.0.0").name)
        self.assertIs(artifact, validate_artifact(artifact))
        for version in ("1.1.0", "2.0.0"):
            with self.assertRaises(ArtifactValidationError):
                schema_path("chapter-repair-triage", version)
        invalid = copy.deepcopy(artifact)
        invalid["overall_status"] = "PASS"
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(invalid)

    def test_phase6d1b_answer_review_artifacts_are_additive_strict_v1_contracts(self):
        kinds = (
            "answer-review-session", "answer-evidence-review",
            "answer-beginner-review", "answer-review-status",
        )
        for kind in kinds:
            with self.subTest(kind=kind):
                artifact = minimal_artifacts()[kind]
                self.assertEqual(f"{kind}.schema.json", schema_path(kind, "1.0.0").name)
                self.assertIs(artifact, validate_artifact(artifact))
                invalid = copy.deepcopy(artifact)
                invalid["unexpected"] = True
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(invalid)
                for version in ("1.1.0", "2.0.0"):
                    with self.assertRaises(ArtifactValidationError):
                        schema_path(kind, version)

        invalid_status = copy.deepcopy(minimal_artifacts()["answer-review-status"])
        invalid_status["overall_status"] = "PASS"
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(invalid_status)

    def test_registry_matches_the_public_v1_schema_set(self):
        self.assertEqual(EXPECTED_KINDS, set(ARTIFACT_KINDS))
        for kind in EXPECTED_KINDS:
            self.assertTrue(schema_path(kind, "1.0.0").is_file())

    def test_curriculum_run_plan_is_registered_only_at_v1_0(self):
        self.assertEqual("curriculum-run-plan.schema.json", schema_path("curriculum-run-plan", "1.0.0").name)
        for version in ("1.1.0", "2.0.0"):
            with self.subTest(version=version), self.assertRaises(ArtifactValidationError):
                schema_path("curriculum-run-plan", version)

        artifact = minimal_artifacts()["curriculum-run-plan"]
        self.assertIs(artifact, validate_artifact(artifact))
        invalid = copy.deepcopy(artifact)
        invalid["unexpected"] = True
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(invalid)
        for field, value in (("source_status", "UNKNOWN"), ("unknown_files", None)):
            invalid = copy.deepcopy(artifact)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ArtifactValidationError):
                validate_artifact(invalid)
        invalid = copy.deepcopy(artifact)
        invalid["units"][0]["unit_identity"]["origin"] = "FUTURE_ORIGIN"
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(invalid)

    def test_run_artifacts_accept_only_explicit_curriculum_metadata_versions(self):
        from phase6_curriculum_run_state import initial_state, upgrade_to_v1_1

        source = minimal_artifacts()
        plan = copy.deepcopy(source["curriculum-run-plan"])
        plan_raw = dumps_artifact(plan).encode("utf-8")
        state_v1 = initial_state(plan, plan_raw, generated_at=plan["generated_at"])
        state_v1_raw = dumps_artifact(state_v1).encode("utf-8")
        state_v1_1 = upgrade_to_v1_1(
            state_v1,
            state_v1_raw,
            plan,
            plan_raw,
            generated_at="2026-10-01T00:00:01Z",
        )
        cases = (
            ("curriculum-run-plan", plan),
            ("curriculum-run-state 1.0.0", state_v1),
            ("curriculum-run-state 1.1.0", state_v1_1),
            ("whole-book-assembly", source["whole-book-assembly"]),
        )
        for metadata_version in ("1.1.0", "1.2.0"):
            for label, original in cases:
                with self.subTest(label=label, curriculum_schema_version=metadata_version):
                    artifact = copy.deepcopy(original)
                    artifact["curriculum_schema_version"] = metadata_version
                    self.assertIs(artifact, validate_artifact(artifact))

        for label, original in cases:
            with self.subTest(label=label, curriculum_schema_version="1.3.0"):
                artifact = copy.deepcopy(original)
                artifact["curriculum_schema_version"] = "1.3.0"
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(artifact)

    def test_curriculum_run_state_v1_and_additive_v1_1_are_registered(self):
        self.assertIn("curriculum-run-state", ARTIFACT_KINDS)
        self.assertEqual("curriculum-run-state.schema.json", schema_path("curriculum-run-state", "1.0.0").name)
        artifact = minimal_artifacts()["curriculum-run-state"]
        self.assertIs(artifact, validate_artifact(artifact))
        for mutation in (
            lambda value: value.update(unexpected=True),
            lambda value: value.update(run_state="PASS"),
            lambda value: value["units"][0].update(execution_state="VERIFIED"),
        ):
            invalid = copy.deepcopy(artifact)
            mutation(invalid)
            with self.assertRaises(ArtifactValidationError):
                validate_artifact(invalid)

        self.assertEqual("curriculum-run-state-v1.1.schema.json", schema_path("curriculum-run-state", "1.1.0").name)
        repair_state = copy.deepcopy(artifact)
        repair_state["schema_version"] = "1.1.0"
        for row in repair_state["units"]:
            row["repair_disposition"] = None
            row["attempts"] = []
        self.assertIs(repair_state, validate_artifact(repair_state))

    def test_prerequisite_artifact_profiles_are_additive_and_versioned(self):
        for kind, filename in (
            ("prerequisite-candidates", "prerequisite-candidates.schema.json"),
            ("prerequisite-graph", "prerequisite-graph.schema.json"),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(filename, schema_path(kind, "1.0.0").name)
                artifact = minimal_artifacts()[kind]
                self.assertIs(artifact, validate_artifact(artifact))
                self.assertEqual(
                    f"{kind}-v1.1.schema.json",
                    schema_path(kind, "1.1.0").name,
                )
                for version in ("1.2.0", "2.0.0"):
                    with self.assertRaises(ArtifactValidationError):
                        schema_path(kind, version)
                invalid = copy.deepcopy(artifact)
                invalid["unexpected"] = True
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(invalid)

        phase4_v11 = [
            {"path": "evidence.json", "artifact_kind": "evidence", "schema_version": "1.4.0", "sha256": "1" * 64},
            {"path": "knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.2.0", "sha256": "2" * 64},
            {"path": "semantic-proposals.json", "artifact_kind": "semantic-proposals", "schema_version": "1.1.0", "sha256": "3" * 64},
        ]
        candidates = copy.deepcopy(minimal_artifacts()["prerequisite-candidates"])
        candidates["schema_version"] = "1.1.0"
        candidates["phase4_inputs"] = copy.deepcopy(phase4_v11)
        candidates["source_run"]["schema_version"] = "1.1.0"
        candidates["source_run"]["members"].append({
            "path": "documentation/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.3.0",
            "sha256": "4" * 64,
        })
        self.assertIs(candidates, validate_artifact(candidates))

        graph = copy.deepcopy(minimal_artifacts()["prerequisite-graph"])
        graph["schema_version"] = "1.1.0"
        graph["phase4_inputs"] = copy.deepcopy(phase4_v11)
        graph["candidate_input"]["schema_version"] = "1.1.0"
        self.assertIs(graph, validate_artifact(graph))

        legacy_source_run = copy.deepcopy(candidates)
        legacy_source_run["source_run"]["schema_version"] = "1.0.0"
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(legacy_source_run)

        legacy_candidate_binding = copy.deepcopy(graph)
        legacy_candidate_binding["candidate_input"]["schema_version"] = "1.0.0"
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(legacy_candidate_binding)

    def test_curriculum_versions_are_additive_and_candidates_are_versioned(self):
        self.assertEqual("curriculum.schema.json", schema_path("curriculum", "1.0.0").name)
        try:
            curriculum_v11 = schema_path("curriculum", "1.1.0")
        except ArtifactValidationError:
            self.fail("curriculum v1.1.0 must be an explicit registered opt-in")
        self.assertEqual("curriculum-v1.1.schema.json", curriculum_v11.name)
        self.assertEqual("curriculum-v1.2.schema.json", schema_path("curriculum", "1.2.0").name)
        self.assertEqual(
            "curriculum-candidates.schema.json",
            schema_path("curriculum-candidates", "1.0.0").name,
        )
        self.assertEqual(
            "curriculum-candidates-v1.1.schema.json",
            schema_path("curriculum-candidates", "1.1.0").name,
        )
        for kind, version in (("curriculum", "1.3.0"), ("curriculum-candidates", "1.2.0")):
            with self.subTest(kind=kind, version=version), self.assertRaises(ArtifactValidationError):
                schema_path(kind, version)
        legacy = minimal_artifacts()["curriculum"]
        self.assertIs(legacy, validate_artifact(legacy))
        candidates = minimal_artifacts()["curriculum-candidates"]
        self.assertIs(candidates, validate_artifact(candidates))
        candidates_v11 = copy.deepcopy(candidates)
        candidates_v11["schema_version"] = "1.1.0"
        for row, version in zip(
            candidates_v11["phase4_inputs"], ("1.4.0", "1.2.0", "1.1.0"),
        ):
            row["schema_version"] = version
        for row in candidates_v11["prerequisite_inputs"]:
            row["schema_version"] = "1.1.0"
        self.assertIs(candidates_v11, validate_artifact(candidates_v11))
        missing_revision = copy.deepcopy(candidates)
        del missing_revision["repository_revision"]
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(missing_revision)
        curriculum_v11_schema = json.loads(schema_path("curriculum", "1.1.0").read_text(encoding="utf-8"))
        self.assertEqual(
            "https://project-deepdive.dev/schemas/v1/curriculum-v1.1.schema.json",
            curriculum_v11_schema["$id"],
        )
        self.assertEqual({"const": "1.1.0"}, curriculum_v11_schema["properties"]["schema_version"])
        curriculum_v12_schema = json.loads(schema_path("curriculum", "1.2.0").read_text(encoding="utf-8"))
        self.assertEqual(
            "https://project-deepdive.dev/schemas/v1/curriculum-v1.2.schema.json",
            curriculum_v12_schema["$id"],
        )
        self.assertEqual({"const": "1.2.0"}, curriculum_v12_schema["properties"]["schema_version"])

    def test_phase4_graph_and_evidence_versions_have_separate_contracts(self):
        graph_v10 = schema_path("knowledge-graph", "1.0.0")
        evidence_v10 = schema_path("evidence", "1.0.0")
        evidence_v11 = schema_path("evidence", "1.1.0")

        self.assertEqual("knowledge-graph.schema.json", graph_v10.name)
        self.assertEqual("evidence.schema.json", evidence_v10.name)
        self.assertEqual("evidence.schema.json", evidence_v11.name)
        self.assertEqual(
            "knowledge-graph-v1.1.schema.json",
            schema_path("knowledge-graph", "1.1.0").name,
        )
        self.assertEqual(
            "evidence-v1.2.schema.json",
            schema_path("evidence", "1.2.0").name,
        )
        self.assertNotEqual(graph_v10, schema_path("knowledge-graph", "1.1.0"))
        self.assertNotEqual(evidence_v11, schema_path("evidence", "1.2.0"))
        self.assertEqual(
            "knowledge-graph-v1.2.schema.json",
            schema_path("knowledge-graph", "1.2.0").name,
        )
        self.assertEqual("evidence-v1.4.schema.json", schema_path("evidence", "1.4.0").name)
        for kind, version in (("knowledge-graph", "1.3.0"), ("evidence", "1.5.0")):
            with self.subTest(kind=kind, version=version), self.assertRaises(ArtifactValidationError):
                schema_path(kind, version)
        self.assertEqual("evidence-v1.3.schema.json", schema_path("evidence", "1.3.0").name)

    def test_phase4_claim_artifacts_register_additive_versions_1_1_and_1_2(self):
        for kind, expected_filename in (
            ("claim-candidates", "claim-candidates.schema.json"),
            ("claim-evidence", "claim-evidence.schema.json"),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(expected_filename, schema_path(kind, "1.0.0").name)
                expected_v11 = f"{kind}-v1.1.schema.json"
                try:
                    registered_v11 = schema_path(kind, "1.1.0")
                except ArtifactValidationError as exc:
                    self.fail(f"{kind} 1.1.0 should be registered as an opt-in version: {exc}")
                self.assertEqual(expected_v11, registered_v11.name)
                expected_v12 = f"{kind}-v1.2.schema.json"
                self.assertEqual(expected_v12, schema_path(kind, "1.2.0").name)
                for version in ("2.0.0",):
                    with self.assertRaises(ArtifactValidationError):
                        schema_path(kind, version)

    def test_phase4_claim_v11_schemas_require_three_phase4_inputs(self):
        records = [
            {"path": "evidence.json", "artifact_kind": "evidence", "schema_version": "1.2.0", "sha256": "a" * 64},
            {"path": "knowledge-graph.json", "artifact_kind": "knowledge-graph", "schema_version": "1.1.0", "sha256": "b" * 64},
            {"path": "semantic-proposals.json", "artifact_kind": "semantic-proposals", "schema_version": "1.0.0", "sha256": "c" * 64},
        ]
        for kind in ("claim-candidates", "claim-evidence"):
            with self.subTest(kind=kind):
                artifact = copy.deepcopy(minimal_artifacts()[kind])
                artifact["schema_version"] = "1.1.0"
                artifact["phase4_inputs"] = copy.deepcopy(records)
                self.assertIs(artifact, validate_artifact(artifact))
                invalid = copy.deepcopy(artifact)
                invalid["phase4_inputs"] = records[:2]
                with self.assertRaises(ArtifactValidationError):
                    validate_artifact(invalid)

    def test_phase4_semantic_proposals_register_versions_1_0_and_1_1(self):
        artifact = minimal_artifacts()["semantic-proposals"]
        try:
            registered_schema = schema_path("semantic-proposals", "1.0.0")
        except ArtifactValidationError as exc:
            self.fail(f"semantic-proposals 1.0.0 is not registered: {exc}")
        self.assertEqual("semantic-proposals.schema.json", registered_schema.name)
        self.assertIs(artifact, validate_artifact(artifact))
        try:
            registered_v11 = schema_path("semantic-proposals", "1.1.0")
        except ArtifactValidationError as exc:
            self.fail(f"semantic-proposals 1.1.0 is not registered: {exc}")
        self.assertEqual("semantic-proposals-v1.1.schema.json", registered_v11.name)
        documentation_aware = copy.deepcopy(artifact)
        documentation_aware["schema_version"] = "1.1.0"
        documentation_aware["source_run"]["schema_version"] = "1.1.0"
        documentation_aware["source_run"]["members"].append({
            "path": "documentation/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.3.0",
            "sha256": "d" * 64,
        })
        documentation_aware["source_run"]["members"].sort(key=lambda row: row["path"])
        documentation_aware["phase4_inputs"] = [
            {
                "path": "phase4a/evidence.json",
                "artifact_kind": "evidence",
                "schema_version": "1.4.0",
                "sha256": "e" * 64,
            },
            {
                "path": "phase4a/knowledge-graph.json",
                "artifact_kind": "knowledge-graph",
                "schema_version": "1.2.0",
                "sha256": "f" * 64,
            },
        ]
        self.assertIs(documentation_aware, validate_artifact(documentation_aware))
        for version in ("2.0.0",):
            with self.assertRaises(ArtifactValidationError):
                schema_path("semantic-proposals", version)

    def test_claim_evidence_graph_versions_1_0_and_1_1_are_separate(self):
        artifact = minimal_artifacts()["claim-evidence-graph"]
        self.assertEqual(
            "claim-evidence-graph.schema.json",
            schema_path("claim-evidence-graph", "1.0.0").name,
        )
        self.assertIs(artifact, validate_artifact(artifact))
        self.assertEqual(
            "claim-evidence-graph-v1.1.schema.json",
            schema_path("claim-evidence-graph", "1.1.0").name,
        )
        for version in ("2.0.0",):
            with self.assertRaises(ArtifactValidationError):
                schema_path("claim-evidence-graph", version)
        invalid = copy.deepcopy(artifact)
        invalid["unexpected"] = True
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(invalid)

    def test_legacy_graph_and_evidence_fixtures_still_validate(self):
        graph = load_artifact(FIXTURE_ROOT / "knowledge-graph.json")
        evidence = load_artifact(FIXTURE_ROOT / "evidence.json")

        self.assertEqual("1.0.0", graph["schema_version"])
        self.assertEqual("1.0.0", evidence["schema_version"])

    def test_phase3_run_versions_are_registered_additively(self):
        artifact = minimal_artifacts()["phase3-run"]
        self.assertIs(artifact, validate_artifact(artifact))
        self.assertEqual("phase3-run.schema.json", schema_path("phase3-run", "1.0.0").name)
        artifact["schema_version"] = "1.1.0"
        artifact["members"].append({
            "path": "documentation/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.3.0",
            "sha256": "0" * 64,
        })
        self.assertIs(artifact, validate_artifact(artifact))
        self.assertEqual("phase3-run-v1.1.schema.json", schema_path("phase3-run", "1.1.0").name)
        self.assertNotEqual(schema_path("phase3-run", "1.0.0"), schema_path("phase3-run", "1.1.0"))

    def test_repository_documentation_evidence_v13_is_separately_registered(self):
        evidence = {
            "artifact_kind": "evidence",
            "schema_version": "1.3.0",
            "repository_revision": "a" * 40,
            "generated_at": "2026-09-25T00:00:00Z",
            "snapshot_kind": "git-tree",
            "source_metadata": {
                "snapshot_kind": "git-tree",
                "g01_status": "PASS",
                "unknown_files": 0,
                "project_index_sha256": "b" * 64,
                "coverage_sha256": "c" * 64,
            },
            "items": [{
                "id": "EVID-" + "d" * 24,
                "level": "E2",
                "kind": "repository_documentation",
                "summary": "The selected repository documentation span records a repository declaration.",
                "confidence": 1.0,
                "locator": {"path": "README.md", "line_start": 1, "line_end": 2},
                "source_bytes": {
                    "file_sha256": "e" * 64,
                    "span_sha256": "f" * 64,
                    "span_bytes": 12,
                },
            }],
        }
        self.assertIs(evidence, validate_artifact(evidence))
        self.assertEqual("evidence-v1.3.schema.json", schema_path("evidence", "1.3.0").name)
        self.assertNotEqual(schema_path("evidence", "1.2.0"), schema_path("evidence", "1.3.0"))

    def test_static_analysis_has_separate_v10_and_v11_contracts(self):
        artifact = minimal_artifacts()["static-analysis"]
        self.assertIs(artifact, validate_artifact(artifact))
        self.assertNotEqual(
            schema_path("static-analysis", "1.0.0"),
            schema_path("static-analysis", "1.1.0"),
        )
        extended = copy.deepcopy(artifact)
        extended["schema_version"] = "1.1.0"
        extended["roles"] = []
        extended["relations"][0]["kind"] = "CALL_CANDIDATE"
        extended["relations"][0]["certainty"] = "UNRESOLVED"
        extended["relations"][0]["target_id"] = None
        extended["relations"][0]["unresolved_target"] = "name:serve"
        self.assertIs(extended, validate_artifact(extended))
        for version in ("1.6.0",):
            with self.subTest(version=version):
                unsupported = copy.deepcopy(artifact)
                unsupported["schema_version"] = version
                with self.assertRaisesRegex(ArtifactValidationError, r"\$\.schema_version"):
                    validate_artifact(unsupported)

    def test_static_analysis_v13_is_opt_in_and_other_kinds_reject_it(self):
        artifact = copy.deepcopy(minimal_artifacts()["static-analysis"])
        artifact["schema_version"] = "1.3.0"
        artifact["roles"] = []
        artifact.update({
            "stack_profile_sha256": "1" * 64,
            "phase3a_evidence_sha256": "2" * 64,
            "java_analysis_v12_sha256": "3" * 64,
            "java_evidence_v12_sha256": "4" * 64,
        })

        self.assertIs(artifact, validate_artifact(artifact))
        for name in (
            "stack_profile_sha256",
            "phase3a_evidence_sha256",
            "java_analysis_v12_sha256",
            "java_evidence_v12_sha256",
        ):
            malformed = copy.deepcopy(artifact)
            malformed[name] = "A" * 64
            with self.subTest(field=name), self.assertRaises(ArtifactValidationError):
                validate_artifact(malformed)
        for kind in EXPECTED_KINDS - {"static-analysis", "evidence"}:
            with self.subTest(kind=kind):
                with self.assertRaises(ArtifactValidationError):
                    schema_path(kind, "1.3.0")

    def test_static_analysis_v14_is_opt_in_only(self):
        artifact = {
            "artifact_kind": "static-analysis",
            "schema_version": "1.4.0",
            "repository_revision": "1463a06437fc903edec24722ecbb686a46d8f9da",
            "generated_at": "2026-09-22T00:00:00Z",
            "source_metadata": {
                "snapshot_kind": "git-tree",
                "g01_status": "PASS",
                "unknown_files": 0,
                "project_index_sha256": "a" * 64,
                "coverage_sha256": "b" * 64,
            },
            "stack_profile_sha256": "c" * 64,
            "phase3a_evidence_sha256": "d" * 64,
            "analysis_status": "PASS",
            "languages": [{
                "language": "typescript",
                "analysis_status": "PASS",
                "limitations": [],
                "files": [{
                    "path": "frontend/Entry.tsx",
                    "sha256": "e" * 64,
                    "status": "ANALYZED",
                    "line_count": 1,
                    "limitations": [],
                }],
            }],
            "symbols": [],
            "relations": [],
            "roles": [],
        }

        self.assertIs(artifact, validate_artifact(artifact))
        for kind in EXPECTED_KINDS - {"static-analysis", "evidence"}:
            with self.subTest(kind=kind):
                with self.assertRaises(ArtifactValidationError):
                    schema_path(kind, "1.4.0")
        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.schema_version"):
            validate_artifact({**artifact, "artifact_kind": "evidence", "schema_version": "1.5.0"})

    def test_static_analysis_v15_is_registered_only_for_frontend_static_analysis(self):
        artifact = {
            "artifact_kind": "static-analysis",
            "schema_version": "1.5.0",
            "repository_revision": "1463a06437fc903edec24722ecbb686a46d8f9da",
            "generated_at": "2026-09-22T00:00:00Z",
            "source_metadata": {
                "snapshot_kind": "git-tree",
                "g01_status": "PASS",
                "unknown_files": 0,
                "project_index_sha256": "a" * 64,
                "coverage_sha256": "b" * 64,
            },
            "stack_profile_sha256": "c" * 64,
            "phase3a_evidence_sha256": "d" * 64,
            "analysis_status": "PASS",
            "languages": [{
                "language": "typescript",
                "analysis_status": "PASS",
                "limitations": [],
                "files": [{
                    "path": "frontend/Entry.tsx",
                    "sha256": "e" * 64,
                    "status": "ANALYZED",
                    "line_count": 1,
                    "limitations": [],
                }],
            }],
            "symbols": [{
                "id": "SYM-" + "1" * 24,
                "language": "typescript",
                "kind": "variable",
                "name": "useAppStore",
                "qualified_name": "frontend.Entry.useAppStore",
                "path": "frontend/Entry.tsx",
                "extraction_method": "typescript-6.0.3-parse",
                "certainty": "VERIFIED",
                "line_start": 1,
                "line_end": 1,
                "evidence_ids": ["EVID-store"],
            }],
            "relations": [],
            "roles": [{
                "id": "ROLE-" + "2" * 24,
                "language": "typescript",
                "kind": "store_candidate",
                "symbol_id": "SYM-" + "1" * 24,
                "path": "frontend/Entry.tsx",
                "extraction_method": "typescript-6.0.3-parse",
                "certainty": "CANDIDATE",
                "line_start": 1,
                "line_end": 1,
                "evidence_ids": ["EVID-store-role"],
            }],
        }

        self.assertEqual("1.5.0", validate_artifact(artifact)["schema_version"])
        self.assertEqual("static-analysis-v1.5.schema.json", schema_path(
            "static-analysis", "1.5.0",
        ).name)
        for kind in EXPECTED_KINDS - {"static-analysis"}:
            with self.subTest(kind=kind), self.assertRaises(ArtifactValidationError):
                schema_path(kind, "1.5.0")

    def test_static_analysis_v12_has_java_contract(self):
        artifact = copy.deepcopy(minimal_artifacts()["static-analysis"])
        artifact["schema_version"] = "1.2.0"
        artifact["roles"] = []
        artifact["languages"][0]["language"] = "java"
        artifact["symbols"][1].update({
            "language": "java",
            "kind": "interface",
            "extraction_method": "jdk-javac-parse",
        })
        artifact["relations"][0].update({
            "language": "java",
            "kind": "IMPLEMENTS",
            "unresolved_target": "java-type:Runnable",
            "certainty": "UNRESOLVED",
            "extraction_method": "jdk-javac-parse",
        })

        self.assertIs(artifact, validate_artifact(artifact))
        self.assertNotEqual(
            schema_path("static-analysis", "1.1.0"),
            schema_path("static-analysis", "1.2.0"),
        )

    def test_v12_contracts_are_registered_only_for_their_artifact_kinds(self):
        for kind in EXPECTED_KINDS - {"static-analysis", "evidence", "knowledge-graph", "claim-candidates", "claim-evidence", "curriculum"}:
            with self.subTest(kind=kind):
                artifact = copy.deepcopy(minimal_artifacts()[kind])
                artifact["schema_version"] = "1.2.0"
                with self.assertRaisesRegex(ArtifactValidationError, r"\$\.schema_version"):
                    validate_artifact(artifact)

    def test_previously_registered_prerequisite_and_curriculum_versions(self):
        # Explicit expected versions: never infer expected support from the registry.
        expected = {
            ("prerequisite-candidates", "1.1.0"): "prerequisite-candidates-v1.1.schema.json",
            ("prerequisite-graph", "1.1.0"): "prerequisite-graph-v1.1.schema.json",
            ("curriculum-candidates", "1.1.0"): "curriculum-candidates-v1.1.schema.json",
            ("curriculum", "1.2.0"): "curriculum-v1.2.schema.json",
        }
        for (kind, version), filename in expected.items():
            with self.subTest(kind=kind, version=version):
                self.assertEqual(filename, schema_path(kind, version).name)
                artifact = copy.deepcopy(minimal_artifacts()[kind])
                artifact["schema_version"] = version
                # A legacy body is invalid at this newer version, but the root
                # version is registered: validation must reach its body contract.
                with self.assertRaises(ArtifactValidationError) as raised:
                    validate_artifact(artifact)
                self.assertNotIn("$.schema_version:", str(raised.exception))
                artifact["schema_version"] = "1.99.0"
                with self.assertRaisesRegex(ArtifactValidationError, r"\$\.schema_version"):
                    validate_artifact(artifact)
        for kind in EXPECTED_KINDS - {item[0] for item in expected}:
            with self.subTest(kind=kind), self.assertRaisesRegex(ArtifactValidationError, r"\$\.schema_version"):
                schema_path(kind, "1.99.0")

    def test_static_analysis_v10_rejects_v11_fields_and_relation_kinds(self):
        artifact = minimal_artifacts()["static-analysis"]
        artifact["roles"] = []
        with self.assertRaisesRegex(ArtifactValidationError, r"roles|unexpected"):
            validate_artifact(artifact)

        artifact = minimal_artifacts()["static-analysis"]
        artifact["relations"][0]["kind"] = "CALL_CANDIDATE"
        with self.assertRaisesRegex(ArtifactValidationError, r"relations|kind"):
            validate_artifact(artifact)

    def test_project_index_accepts_legacy_1_0_and_scanner_1_1(self):
        legacy = minimal_artifacts()["project-index"]
        self.assertIs(legacy, validate_artifact(legacy))

        current = copy.deepcopy(legacy)
        current["schema_version"] = "1.1.0"
        current["files"][0].update({
            "content_kind": "text",
            "media_type": "text/x-python",
            "extension": "py",
            "vcs_object_id": "a" * 40,
        })
        self.assertIs(current, validate_artifact(current))

    def test_coverage_accepts_legacy_1_0_and_scanner_1_1(self):
        legacy = minimal_artifacts()["coverage"]
        self.assertIs(legacy, validate_artifact(legacy))

        current = copy.deepcopy(legacy)
        current["schema_version"] = "1.1.0"
        current["classification_policy_version"] = "1.0.0"
        current["entries"][0].update({
            "secondary_surfaces": [],
            "rule_id": "path:test",
        })
        self.assertIs(current, validate_artifact(current))

    def test_unlisted_v1_minor_versions_fail_closed(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "1.2.0"
        with self.assertRaisesRegex(ArtifactValidationError, "schema_version"):
            validate_artifact(artifact)

    def test_unlisted_minor_is_not_enabled_for_other_artifact_kinds(self):
        opted_in_v11_kinds = {"knowledge-graph", "semantic-proposals", "claim-candidates", "claim-evidence", "claim-evidence-graph", "curriculum", "curriculum-run-state", "exercise-bank", "general-learning-unit", "phase3-run", "prerequisite-candidates", "prerequisite-graph", "curriculum-candidates"}
        for kind in EXPECTED_KINDS - SCANNER_KINDS - PHASE3A_KINDS - STATIC_ANALYSIS_OPT_IN_VERSIONS.keys() - opted_in_v11_kinds:
            with self.subTest(kind=kind):
                artifact = minimal_artifacts()[kind]
                artifact["schema_version"] = "1.1.0"
                with self.assertRaisesRegex(ArtifactValidationError, r"\$\.schema_version"):
                    validate_artifact(artifact)

    def test_phase3a_stack_and_evidence_accept_v11_source_metadata(self):
        metadata = {
            "snapshot_kind": "worktree",
            "g01_status": "PASS",
            "unknown_files": 0,
            "project_index_sha256": "a" * 64,
            "coverage_sha256": "b" * 64,
        }
        artifacts = minimal_artifacts()
        for kind in ("stack-profile", "evidence"):
            with self.subTest(kind=kind):
                artifact = copy.deepcopy(artifacts[kind])
                artifact["schema_version"] = "1.1.0"
                artifact["source_metadata"] = copy.deepcopy(metadata)
                self.assertIs(artifact, validate_artifact(artifact))

    def test_v11_source_metadata_schema_rejects_invalid_digest_shape(self):
        artifact = copy.deepcopy(minimal_artifacts()["stack-profile"])
        artifact["schema_version"] = "1.1.0"
        artifact["source_metadata"] = {
            "snapshot_kind": "worktree",
            "g01_status": "PASS",
            "unknown_files": 0,
            "project_index_sha256": "not-a-sha256",
            "coverage_sha256": "b" * 64,
        }

        with self.assertRaisesRegex(ArtifactValidationError, r"source_metadata\.project_index_sha256"):
            validate_artifact(artifact)

    def test_project_index_v1_1_rejects_unknown_typing_vocabulary(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "1.1.0"
        artifact["files"][0].update({
            "content_kind": "hologram",
            "media_type": "text/x-python",
            "extension": "py",
            "vcs_object_id": "a" * 40,
        })

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.files\[0\]\.content_kind"):
            validate_artifact(artifact)

    def test_scanner_entries_keep_every_legacy_required_field(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "1.1.0"
        artifact["files"][0].update({
            "content_kind": "text",
            "media_type": "text/x-python",
            "extension": "py",
            "vcs_object_id": "a" * 40,
        })
        del artifact["files"][0]["tracked"]

        with self.assertRaisesRegex(ArtifactValidationError, r"tracked: required property is missing"):
            validate_artifact(artifact)

    def test_coverage_secondary_surfaces_reuse_the_primary_surface_vocabulary(self):
        artifact = minimal_artifacts()["coverage"]
        artifact["schema_version"] = "1.1.0"
        artifact["entries"][0].update({
            "secondary_surfaces": ["imaginary-surface"],
            "rule_id": "path:test",
        })

        with self.assertRaisesRegex(ArtifactValidationError, r"secondary_surfaces\[0\]"):
            validate_artifact(artifact)

    def test_minimal_artifact_for_every_kind_is_valid(self):
        for kind, artifact in minimal_artifacts().items():
            with self.subTest(kind=kind):
                self.assertIs(artifact, validate_artifact(artifact))

    def test_missing_required_field_reports_its_json_path(self):
        artifact = minimal_artifacts()["project-index"]
        del artifact["generated_at"]

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.generated_at: required property is missing"):
            validate_artifact(artifact)

    def test_nested_type_error_reports_the_nested_json_path(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["project"]["dirty"] = 1

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.project\.dirty: expected boolean"):
            validate_artifact(artifact)

    def test_unknown_artifact_kind_fails_closed(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["artifact_kind"] = "imaginary-report"

        with self.assertRaisesRegex(ArtifactValidationError, "unsupported artifact_kind"):
            validate_artifact(artifact)

    def test_unsupported_major_version_fails_closed(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["schema_version"] = "2.0.0"

        with self.assertRaisesRegex(ArtifactValidationError, "unsupported schema major version: 2"):
            validate_artifact(artifact)

    def test_unexpected_property_is_rejected(self):
        artifact = minimal_artifacts()["coverage"]
        artifact["surprise"] = True

        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.surprise: unexpected property"):
            validate_artifact(artifact)

    def test_partial_coverage_can_record_unknown_until_the_final_gate_resolves_it(self):
        artifact = minimal_artifacts()["coverage"]
        artifact["unknown_count"] = 1
        artifact["entries"][0]["classification"] = "UNKNOWN"

        self.assertIs(artifact, validate_artifact(artifact))

    def test_canonical_serialization_round_trips_stably(self):
        artifact = minimal_artifacts()["evidence"]

        first = dumps_artifact(artifact)
        second = dumps_artifact(json.loads(first))

        self.assertEqual(first, second)
        self.assertTrue(first.endswith("\n"))
        self.assertLess(first.index('"artifact_kind"'), first.index('"schema_version"'))

    def test_canonical_serialization_refuses_non_finite_numbers(self):
        artifact = minimal_artifacts()["stack-profile"]
        artifact["detections"] = [{
            "category": "language",
            "name": "Python",
            "status": "LIKELY",
            "confidence": math.nan,
            "evidence_ids": [],
        }]

        with self.assertRaises(ValueError):
            dumps_artifact(artifact)

    def test_open_objects_still_reject_non_json_values_and_keys(self):
        artifact = minimal_artifacts()["knowledge-graph"]
        artifact["nodes"] = [{
            "id": "node:one",
            "type": "Project",
            "label": "One",
            "evidence_ids": [],
            "properties": {"bad_value": {1, 2}},
        }]
        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.nodes\[0\]\.properties\.bad_value: expected JSON value"):
            validate_artifact(artifact)

        artifact["nodes"][0]["properties"] = {1: "integer key"}
        with self.assertRaisesRegex(ArtifactValidationError, r"object keys must be strings"):
            validate_artifact(artifact)

    def test_open_objects_reject_non_finite_floats(self):
        artifact = minimal_artifacts()["quality-report"]
        artifact["gates"] = [{
            "id": "G01",
            "result": "PASS",
            "measurements": {"ratio": math.nan},
            "failures": [],
            "warnings": [],
            "evidence_ids": [],
            "recommended_repair": "",
        }]

        with self.assertRaisesRegex(ArtifactValidationError, r"measurements\.ratio: expected finite number"):
            validate_artifact(artifact)

    def test_arbitrarily_large_json_integer_does_not_overflow(self):
        artifact = minimal_artifacts()["project-index"]
        artifact["file_count"] = 10**400

        self.assertIs(artifact, validate_artifact(artifact))

    def test_schema_definition_rejects_unsupported_assertion_keyword(self):
        with self.assertRaisesRegex(ArtifactValidationError, r"\$\.not: unsupported schema keyword"):
            validate_schema_document({"type": "string", "not": {"const": "bad"}})

    def test_strict_loader_rejects_non_standard_nan_and_duplicate_keys(self):
        artifact = dumps_artifact(minimal_artifacts()["quality-report"])
        nan_text = artifact.replace('"gates": []', '"gates": [], "extra_number": NaN')
        duplicate_text = artifact.replace(
            '"artifact_kind": "quality-report"',
            '"artifact_kind": "quality-report", "artifact_kind": "quality-report"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            nan_path = Path(tmp) / "nan.json"
            duplicate_path = Path(tmp) / "duplicate.json"
            nan_path.write_text(nan_text, encoding="utf-8")
            duplicate_path.write_text(duplicate_text, encoding="utf-8")

            with self.assertRaisesRegex(ArtifactValidationError, "invalid JSON constant: NaN"):
                load_artifact(nan_path)
            with self.assertRaisesRegex(ArtifactValidationError, "duplicate object key: artifact_kind"):
                load_artifact(duplicate_path)


class ArtifactCliTests(unittest.TestCase):
    def run_cli(self, *paths):
        return subprocess.run(
            [sys.executable, str(CLI), *(str(path) for path in paths)],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_cli_validates_all_real_v1_fixtures(self):
        paths = sorted(FIXTURE_ROOT.glob("*.json"))
        self.assertEqual(10, len(paths))

        result = self.run_cli(*paths)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(10, result.stdout.count("PASS "))
        self.assertIn("project-index v1.0.0", result.stdout)
        self.assertIn("quality-report v1.0.0", result.stdout)

    def test_cli_returns_one_and_reports_an_actionable_path_for_invalid_input(self):
        artifact = minimal_artifacts()["project-index"]
        del artifact["generated_at"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(artifact), encoding="utf-8")

            result = self.run_cli(path)

        self.assertEqual(1, result.returncode)
        self.assertIn("FAIL", result.stdout)
        self.assertIn("$.generated_at: required property is missing", result.stdout)

    def test_cli_checks_every_input_even_when_one_fails(self):
        valid = FIXTURE_ROOT / "evidence.json"
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "invalid.json"
            invalid.write_text("{}", encoding="utf-8")

            result = self.run_cli(invalid, valid)

        self.assertEqual(1, result.returncode)
        self.assertEqual(1, result.stdout.count("FAIL "))
        self.assertEqual(1, result.stdout.count("PASS "))

    def test_cli_reports_malformed_utf8_and_continues_with_later_inputs(self):
        valid = FIXTURE_ROOT / "evidence.json"
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "invalid-utf8.json"
            invalid.write_bytes(b"{\"artifact_kind\":\xff}")

            result = self.run_cli(invalid, valid)

        self.assertEqual(1, result.returncode)
        self.assertIn("cannot read JSON", result.stdout)
        self.assertEqual(1, result.stdout.count("FAIL "))
        self.assertEqual(1, result.stdout.count("PASS "))


class FixtureTruthTests(unittest.TestCase):
    def load_fixtures(self):
        return {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in FIXTURE_ROOT.glob("*.json")
        }

    def test_fixture_revision_is_one_real_git_commit(self):
        fixtures = self.load_fixtures()
        revisions = {artifact["repository_revision"] for artifact in fixtures.values()}
        self.assertEqual(1, len(revisions))

        revision = revisions.pop()
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, f"fixture revision is not a commit: {revision}")

    def test_legacy_fixture_revision_resolves_the_pre_rename_skill_path(self):
        fixture = SKILL_ROOT / "tests" / "fixtures" / "py_mini" / "plan.json"
        self.assertEqual(
            "skills/replicate-learning/tests/fixtures/py_mini/plan.json",
            _git_tree_path("1463a06437fc903edec24722ecbb686a46d8f9da", fixture),
        )

    def test_project_index_hashes_and_sizes_match_real_fixture_files(self):
        project_index = self.load_fixtures()["project-index"]
        target_root = SKILL_ROOT / project_index["project"]["root"]
        self.assertEqual(project_index["file_count"], len(project_index["files"]))
        for entry in project_index["files"]:
            with self.subTest(path=entry["path"]):
                path = target_root / entry["path"]
                if project_index["project"]["snapshot_kind"] == "git-tree":
                    git_path = _git_tree_path(project_index["repository_revision"], path)
                    content = subprocess.check_output(
                        ["git", "show", f"{project_index['repository_revision']}:{git_path}"],
                        cwd=REPOSITORY_ROOT,
                    )
                else:
                    self.assertTrue(path.is_file(), path)
                    content = path.read_bytes()
                self.assertEqual(entry["bytes"], len(content))
                self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())

    def test_evidence_paths_share_the_project_index_root_and_exist_in_the_pinned_tree(self):
        fixtures = self.load_fixtures()
        project_index = fixtures["project-index"]
        target_root = SKILL_ROOT / project_index["project"]["root"]
        for item in fixtures["evidence"]["items"]:
            relative = item["locator"].get("path")
            if relative is None:
                continue
            with self.subTest(evidence_id=item["id"], path=relative):
                path = (target_root / relative).resolve()
                self.assertTrue(path.is_relative_to(target_root.resolve()))
                git_path = _git_tree_path(project_index["repository_revision"], path)
                result = subprocess.run(
                    ["git", "cat-file", "-e", f"{project_index['repository_revision']}:{git_path}"],
                    cwd=REPOSITORY_ROOT,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, f"dangling evidence path: {relative}")

    def test_fixture_cross_references_resolve(self):
        fixtures = self.load_fixtures()
        evidence_ids = {item["id"] for item in fixtures["evidence"]["items"]}
        nodes = {item["id"] for item in fixtures["knowledge-graph"]["nodes"]}
        units = {item["id"] for item in fixtures["curriculum"]["units"]}

        for detection in fixtures["stack-profile"]["detections"]:
            self.assertLessEqual(set(detection["evidence_ids"]), evidence_ids)
        for node in fixtures["knowledge-graph"]["nodes"]:
            self.assertLessEqual(set(node["evidence_ids"]), evidence_ids)
        for edge in fixtures["knowledge-graph"]["edges"]:
            self.assertIn(edge["from"], nodes)
            self.assertIn(edge["to"], nodes)
            self.assertLessEqual(set(edge["evidence_ids"]), evidence_ids)
        for unit in fixtures["curriculum"]["units"]:
            self.assertLessEqual(set(unit["graph_node_ids"]), nodes)
            self.assertLessEqual(set(unit["evidence_ids"]), evidence_ids)
            self.assertLessEqual(set(unit["prerequisite_ids"]), units)
        for gate in fixtures["quality-report"]["gates"]:
            self.assertLessEqual(set(gate["evidence_ids"]), evidence_ids)


if __name__ == "__main__":
    unittest.main()

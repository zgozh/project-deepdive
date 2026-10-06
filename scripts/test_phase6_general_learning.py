#!/usr/bin/env python3
"""Focused tests for the opt-in Phase 6D2A general learning path."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact  # noqa: E402
from test_phase6_chapter import _phase6_fixture  # noqa: E402


def _module(testcase: unittest.TestCase, name: str):
    testcase.assertIsNotNone(importlib.util.find_spec(name), f"{name} is missing")
    return importlib.import_module(name)


def _empty_refs():
    return {
        "graph_node_ids": [],
        "graph_edge_ids": [],
        "evidence_ids": [],
        "file_paths": [],
    }


def _unit(origin="CANDIDATE", scope="GENERAL_LEARNING"):
    identifier_hex = {
        ("CANDIDATE", "GENERAL_LEARNING"): "a",
        ("PREREQUISITE_PRIMER", "GENERAL_LEARNING"): "b",
        ("PREREQUISITE_PRIMER", "PROJECT_SPECIFIC"): "c",
    }[(origin, scope)]
    result = {
        "id": "CURRICULUM-UNIT-" + identifier_hex * 64,
        "order": 1,
        "title": "How a request is handled",
        "kind": "workflow",
        "scope": scope,
        "depth": 1,
        "content_status": "OUTLINE_ONLY",
        "epistemic_status": "UNVERIFIED_TEACHING",
        "prerequisite_ids": [],
        "introduces_concept_keys": ["request.lifecycle"],
        "requires_concept_keys": ["request.basics"],
        "project_refs": _empty_refs(),
        "origin": origin,
    }
    if origin == "CANDIDATE":
        result["unit_key"] = "request-lifecycle"
    else:
        result.update({
            "primer_concept_key": "request.basics",
            "prerequisite_node_id": "PREREQ-NODE-" + "b" * 64,
            "concept_epistemic_status": "UNVERIFIED_TEACHING",
        })
    return result


def _candidate(facts):
    unit = facts["selected_unit"]
    candidate = {
        "artifact_kind": "general-unit-candidate",
        "schema_version": "1.0.0",
        "curriculum_unit_id": unit["id"],
        "facts_sha256": hashlib.sha256(dumps_artifact(facts).encode("utf-8")).hexdigest(),
        "origin": unit["origin"],
        "scope": unit["scope"],
        "writer_alias": "writer-local",
        "lesson": {
            "intuition": "A request is a question sent to another part of a system.",
            "prerequisite_explanations": [
                {"concept_key": key, "explanation": f"{key} names an idea used before this lesson."}
                for key in unit["requires_concept_keys"]
            ],
            "concept_explanations": [
                {"concept_key": key, "explanation": f"{key} is the idea introduced here."}
                for key in unit["introduces_concept_keys"]
            ],
            "worked_example": "A person asks a librarian for a book; the librarian finds it and replies.",
            "common_misconception": "A request is not the same thing as the reply.",
            "learning_check": {
                "question": "A reader asks for one book. What is the request, and what is the response?",
                "reference_answer": "The request is the reader's question for the book. The response is the librarian's answer, such as the book or an explanation that it is unavailable.",
                "hints": [
                    "Separate what the reader sends from what the librarian sends back.",
                    "Name the outgoing question first, then the returned answer.",
                ],
                "rubric": [
                    "Identifies the reader's question as the request.",
                    "Identifies the returned answer or result as the response.",
                ],
            },
        },
    }
    for field in ("unit_key", "primer_concept_key", "prerequisite_node_id", "concept_epistemic_status"):
        if field in unit:
            candidate[field] = unit[field]
    return candidate


class GeneralUnitSelectionTests(unittest.TestCase):
    def test_accepts_only_the_two_empty_reference_origin_rules(self):
        api = _module(self, "phase6_general_learning")
        curriculum = {"units": [
            _unit(),
            _unit("PREREQUISITE_PRIMER", "GENERAL_LEARNING"),
            _unit("PREREQUISITE_PRIMER", "PROJECT_SPECIFIC"),
        ]}
        for unit in curriculum["units"]:
            with self.subTest(origin=unit["origin"], scope=unit["scope"]):
                self.assertEqual(unit, api.select_general_learning_unit(curriculum, unit["id"]))

        invalid_units = []
        project_candidate = _unit()
        project_candidate["scope"] = "PROJECT_SPECIFIC"
        invalid_units.append(project_candidate)
        referenced_primer = _unit("PREREQUISITE_PRIMER", "PROJECT_SPECIFIC")
        referenced_primer["project_refs"]["evidence_ids"] = ["EVID-1"]
        invalid_units.append(referenced_primer)
        primer_with_unit_key = _unit("PREREQUISITE_PRIMER")
        primer_with_unit_key["unit_key"] = "unexpected"
        invalid_units.append(primer_with_unit_key)
        candidate_with_primer_metadata = _unit()
        candidate_with_primer_metadata["primer_concept_key"] = "request.basics"
        invalid_units.append(candidate_with_primer_metadata)

        for invalid in invalid_units:
            with self.subTest(unit=invalid):
                with self.assertRaises(api.Phase6GeneralLearningError):
                    api.select_general_learning_unit({"units": [invalid]}, invalid["id"])

    def test_projection_preserves_primer_scope_and_all_authenticated_input_digests(self):
        api = _module(self, "phase6_general_learning")
        context, inputs, _unit_id, _output = _phase6_fixture(self)
        curriculum_tests = importlib.import_module("test_phase5_curriculum")
        candidate_units = curriculum_tests._starter_units(context)
        workflow = next(unit for unit in candidate_units if unit["unit_key"] == "workflow-map")
        workflow["introduces_concept_keys"] = []
        candidates = curriculum_tests._curriculum_candidates(context, candidate_units)
        curriculum_output = context["fixture"]["work"] / "curriculum-with-project-primer.json"
        context["curriculum_output_path"] = curriculum_output
        inputs = replace(inputs, curriculum_path=curriculum_output)
        context["curriculum"] = curriculum_tests._build(self, context, candidates)
        authenticated, reads, prerequisite_graph, curriculum = importlib.import_module(
            "phase6_chapter"
        )._capture_bundle(inputs)
        primer = next(
            unit for unit in curriculum["units"]
            if unit["origin"] == "PREREQUISITE_PRIMER" and unit["scope"] == "PROJECT_SPECIFIC"
        )

        facts = api.project_general_unit_facts(
            primer["id"], reads, prerequisite_graph, curriculum, authenticated,
        )

        self.assertEqual(primer, facts["selected_unit"])
        self.assertEqual("PROJECT_SPECIFIC", facts["selected_unit"]["scope"])
        self.assertEqual("PREREQUISITE_PRIMER", facts["selected_unit"]["origin"])
        self.assertEqual(0, sum(len(values) for values in primer["project_refs"].values()))
        self.assertEqual(context["curriculum"]["source_status"], facts["source_status"])
        self.assertEqual(context["graph"]["source_metadata"]["unknown_files"], facts["unknown_files"])
        self.assertEqual(10, len(facts["input_digests"]))
        self.assertEqual(
            {"phase4_base_graph", "phase4_base_evidence", "phase4_semantic_proposals",
             "phase4_claim_candidates", "phase4_claim_evidence", "phase4_claim_evidence_graph",
             "phase5_prerequisite_candidates", "phase5_prerequisite_graph",
             "phase5_curriculum_candidates", "phase5_curriculum"},
            {item["role"] for item in facts["input_digests"]},
        )
        validate_artifact(facts)


class GeneralUnitCandidateTests(unittest.TestCase):
    def test_duplicate_keys_are_rejected_and_noncanonical_json_binds_raw_bytes(self):
        api = _module(self, "phase6_general_learning")
        with self.assertRaises(api.Phase6GeneralLearningError):
            api.parse_general_unit_candidate(b'{"artifact_kind":"x","artifact_kind":"y"}')

        context, inputs, _unit_id, _output = _phase6_fixture(self)
        chapter = importlib.import_module("phase6_chapter")
        authenticated, reads, prerequisites, curriculum = chapter._capture_bundle(inputs)
        unit = next(
            unit for unit in curriculum["units"]
            if unit["origin"] == "PREREQUISITE_PRIMER" and unit["scope"] == "GENERAL_LEARNING"
        )
        facts = api.project_general_unit_facts(unit["id"], reads, prerequisites, curriculum, authenticated)
        candidate = _candidate(facts)
        raw = json.dumps(candidate, ensure_ascii=False, indent=2).encode("utf-8")
        parsed = api.parse_general_unit_candidate(raw)
        api.validate_general_unit_candidate(parsed, facts)
        self.assertEqual(candidate, parsed)

        invalid = copy.deepcopy(candidate)
        invalid["lesson"]["concept_explanations"] = []
        with self.assertRaises(api.Phase6GeneralLearningError):
            api.validate_general_unit_candidate(invalid, facts)

    def test_lesson_and_answer_book_share_question_but_only_book_has_answer(self):
        api = _module(self, "phase6_general_learning")
        context, inputs, _unit_id, _output = _phase6_fixture(self)
        chapter = importlib.import_module("phase6_chapter")
        authenticated, reads, prerequisites, curriculum = chapter._capture_bundle(inputs)
        unit = next(
            unit for unit in curriculum["units"]
            if unit["origin"] == "PREREQUISITE_PRIMER" and unit["scope"] == "GENERAL_LEARNING"
        )
        facts = api.project_general_unit_facts(unit["id"], reads, prerequisites, curriculum, authenticated)
        candidate = _candidate(facts)

        lesson = api.render_general_learning_unit(facts, candidate)
        answer_book = api.render_general_answer_book(facts, candidate)
        question = candidate["lesson"]["learning_check"]["question"]
        answer = candidate["lesson"]["learning_check"]["reference_answer"]

        self.assertIn(question, lesson)
        self.assertNotIn(answer, lesson)
        self.assertNotIn("Hints", lesson)
        self.assertIn(question, answer_book)
        self.assertIn(answer, answer_book)
        self.assertTrue(all(hint in answer_book for hint in candidate["lesson"]["learning_check"]["hints"]))
        draft_notice = "Draft teaching material - not independently reviewed or verified."
        self.assertIn(draft_notice, lesson)
        self.assertIn(draft_notice, answer_book)

    def test_candidate_and_artifact_contracts_are_closed_and_keep_reviews_unrun(self):
        api = _module(self, "phase6_general_learning")
        context, inputs, _unit_id, _output = _phase6_fixture(self)
        chapter = importlib.import_module("phase6_chapter")
        authenticated, reads, prerequisites, curriculum = chapter._capture_bundle(inputs)
        unit = next(
            unit for unit in curriculum["units"]
            if unit["origin"] == "PREREQUISITE_PRIMER" and unit["scope"] == "GENERAL_LEARNING"
        )
        facts = api.project_general_unit_facts(unit["id"], reads, prerequisites, curriculum, authenticated)
        candidate = _candidate(facts)
        candidate["facts_sha256"] = hashlib.sha256(dumps_artifact(facts).encode("utf-8")).hexdigest()
        validate_artifact(candidate)
        candidate["project_refs"] = _empty_refs()
        with self.assertRaises(ArtifactValidationError):
            validate_artifact(candidate)

        candidate = _candidate(facts)
        raw = json.dumps(candidate, ensure_ascii=False, indent=2).encode("utf-8")
        candidate = api.parse_general_unit_candidate(raw)
        api.validate_general_unit_candidate(candidate, facts)
        lesson = api.render_general_learning_unit(facts, candidate).encode("utf-8")
        answer = api.render_general_answer_book(facts, candidate).encode("utf-8")
        artifact = api.build_general_learning_unit(facts, candidate, raw, lesson, answer)
        self.assertEqual("1.1.0", artifact["schema_version"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["candidate_sha256"])
        self.assertEqual("DRAFT", artifact["lesson_status"])
        self.assertEqual("DRAFT", artifact["answer_book_status"])
        self.assertEqual("PARTIAL", artifact["overall_status"])
        self.assertEqual("UNVERIFIED_TEACHING", artifact["epistemic_status"])
        self.assertEqual({"NOT_RUN"}, {
            artifact["general_fact_review"], artifact["beginner_review"], artifact["answer_book_review"],
        })
        validate_artifact(artifact)

    def test_v11_renderer_hides_internal_metadata_and_omits_empty_concept_sections(self):
        api = _module(self, "phase6_general_learning")
        context, inputs, _unit_id, _output = _phase6_fixture(self)
        chapter = importlib.import_module("phase6_chapter")
        authenticated, reads, prerequisites, curriculum = chapter._capture_bundle(inputs)
        unit = next(
            row for row in curriculum["units"]
            if row["origin"] == "PREREQUISITE_PRIMER" and row["scope"] == "GENERAL_LEARNING"
        )
        unit["requires_concept_keys"] = []
        unit["introduces_concept_keys"] = []
        facts = api.project_general_unit_facts(unit["id"], reads, prerequisites, curriculum, authenticated)
        candidate = _candidate(facts)

        lesson = api.render_general_learning_unit(facts, candidate)
        answer_book = api.render_general_answer_book(facts, candidate)

        self.assertIn("Draft teaching material", lesson)
        self.assertIn("not independently reviewed or verified", lesson)
        for internal in (
            "Curriculum origin:", "Curriculum scope:", "Prerequisite unit IDs:",
            "PREREQUISITE_PRIMER", "GENERAL_LEARNING", "CURRICULUM-PRIMER-",
            "Prerequisite concepts", "Concepts in this unit", "request.basics", "request.lifecycle",
        ):
            with self.subTest(internal=internal):
                self.assertNotIn(internal, lesson)
        self.assertNotIn("Curriculum origin:", answer_book)
        self.assertNotIn("Curriculum scope:", answer_book)
        for internal in ("PREREQUISITE_PRIMER", "GENERAL_LEARNING", "CURRICULUM-PRIMER-", "request.basics", "request.lifecycle"):
            with self.subTest(answer_book_internal=internal):
                self.assertNotIn(internal, answer_book)
        for heading in ("## Question", "## Answer", "## Hints", "## Rubric"):
            with self.subTest(heading=heading):
                self.assertIn(heading, answer_book)

    def test_v11_renderer_orders_explanations_without_machine_keys(self):
        api = _module(self, "phase6_general_learning")
        context, inputs, _unit_id, _output = _phase6_fixture(self)
        chapter = importlib.import_module("phase6_chapter")
        authenticated, reads, prerequisites, curriculum = chapter._capture_bundle(inputs)
        unit = next(
            row for row in curriculum["units"]
            if row["origin"] == "PREREQUISITE_PRIMER" and row["scope"] == "GENERAL_LEARNING"
        )
        unit["requires_concept_keys"] = ["alpha.before", "beta.before"]
        unit["introduces_concept_keys"] = ["gamma.new", "omega.new"]
        facts = api.project_general_unit_facts(unit["id"], reads, prerequisites, curriculum, authenticated)
        candidate = _candidate(facts)
        candidate["lesson"]["prerequisite_explanations"] = [
            {"concept_key": "beta.before", "explanation": "Second prerequisite explanation."},
            {"concept_key": "alpha.before", "explanation": "First prerequisite explanation."},
        ]
        candidate["lesson"]["concept_explanations"] = [
            {"concept_key": "omega.new", "explanation": "Second new-concept explanation."},
            {"concept_key": "gamma.new", "explanation": "First new-concept explanation."},
        ]

        lesson = api.render_general_learning_unit(facts, candidate)

        self.assertLess(lesson.index("First prerequisite explanation."), lesson.index("Second prerequisite explanation."))
        self.assertLess(lesson.index("First new-concept explanation."), lesson.index("Second new-concept explanation."))
        self.assertIn("### Prerequisite idea 1", lesson)
        self.assertIn("### New concept 1", lesson)
        for key in ("alpha.before", "beta.before", "gamma.new", "omega.new"):
            with self.subTest(key=key):
                self.assertNotIn(key, lesson)

    def test_v10_rendered_bytes_remain_the_recorded_output(self):
        api = _module(self, "phase6_general_learning")
        context, inputs, _unit_id, _output = _phase6_fixture(self)
        chapter = importlib.import_module("phase6_chapter")
        authenticated, reads, prerequisites, curriculum = chapter._capture_bundle(inputs)
        unit = next(
            row for row in curriculum["units"]
            if row["origin"] == "PREREQUISITE_PRIMER" and row["scope"] == "GENERAL_LEARNING"
        )
        facts = api.project_general_unit_facts(unit["id"], reads, prerequisites, curriculum, authenticated)
        candidate = _candidate(facts)

        lesson = api.render_general_learning_unit(facts, candidate, schema_version="1.0.0").encode("utf-8")
        answer_book = api.render_general_answer_book(facts, candidate, schema_version="1.0.0").encode("utf-8")

        self.assertEqual("0b2aa6efcece40527d79913b91e52dc5a66e6a0004318feca797bede0a2435e0", hashlib.sha256(lesson).hexdigest())
        self.assertEqual("c52b245cb02ea759d3040cfcfc76878212fb250691d0077d4e539349ab0ab17b", hashlib.sha256(answer_book).hexdigest())

#!/usr/bin/env python3
"""Focused tests for the opt-in Phase 5B beginner curriculum outline."""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact, validate_artifact  # noqa: E402
from phase4_graph import canonical_tuple_sha256  # noqa: E402
from test_phase5_prerequisites import (  # noqa: E402
    _candidate as _prerequisite_candidate,
    _edge as _prerequisite_edge,
    _e1_reference,
    _e6_reference,
    _node as _prerequisite_node,
    _phase4c_context,
    _project as _project_prerequisites,
)


def _module(testcase: unittest.TestCase, name: str):
    testcase.assertIsNotNone(importlib.util.find_spec(name), f"{name} is missing")
    return importlib.import_module(name)


def _phase5_context(testcase: unittest.TestCase, *, source_files=None):
    context = _phase4c_context(testcase, source_files=source_files)
    refs = _e1_reference(context)
    network = _prerequisite_node(context, "network-basics", scope="GENERAL_LEARNING")
    request_response = _prerequisite_node(
        context, "request-response", scope="GENERAL_LEARNING",
    )
    http_client = _prerequisite_node(
        context,
        "http-client",
        scope="PROJECT_SPECIFIC",
        kind="FrameworkMechanism",
        refs=refs,
    )
    unrelated = _prerequisite_node(context, "database-basics", scope="GENERAL_LEARNING")
    prerequisite_candidates = _prerequisite_candidate(
        context,
        [network, request_response, http_client, unrelated],
        [
            _prerequisite_edge(context, http_client, request_response),
            _prerequisite_edge(context, request_response, network),
        ],
    )
    prerequisite_graph = _project_prerequisites(testcase, context, prerequisite_candidates)
    context["prerequisite_candidates"] = prerequisite_candidates
    context["prerequisite_graph"] = prerequisite_graph
    context["prerequisite_candidates_path"] = context["fixture"]["work"] / "prerequisite-candidates.json"
    context["prerequisite_graph_path"] = context["fixture"]["work"] / "prerequisite-graph.json"
    context["curriculum_candidates_path"] = context["fixture"]["work"] / "curriculum-candidates.json"
    context["curriculum_output_path"] = context["fixture"]["work"] / "curriculum.json"
    return context


def _refs_empty():
    return {
        "graph_node_ids": [],
        "graph_edge_ids": [],
        "evidence_ids": [],
        "file_paths": [],
    }


def _unit(
    unit_key: str,
    stage: str,
    title: str,
    *,
    scope: str = "GENERAL_LEARNING",
    introduces=(),
    requires=(),
    project_refs=None,
):
    return {
        "unit_key": unit_key,
        "stage": stage,
        "title": title,
        "scope": scope,
        "introduces_concept_keys": list(introduces),
        "requires_concept_keys": list(requires),
        "project_refs": copy.deepcopy(project_refs if project_refs is not None else _refs_empty()),
    }


def _curriculum_candidates(context, units):
    phase4_inputs = copy.deepcopy(context["prerequisite_candidates"]["phase4_inputs"])
    prerequisite_candidate_bytes = dumps_artifact(context["prerequisite_candidates"]).encode("utf-8")
    prerequisite_graph_bytes = dumps_artifact(context["prerequisite_graph"]).encode("utf-8")
    return {
        "artifact_kind": "curriculum-candidates",
        "schema_version": "1.0.0",
        "repository_revision": context["prerequisite_graph"]["repository_revision"],
        "generated_at": context["prerequisite_graph"]["generated_at"],
        "phase4_inputs": phase4_inputs,
        "prerequisite_inputs": [
            {
                "path": "prerequisite-candidates.json",
                "artifact_kind": "prerequisite-candidates",
                "schema_version": "1.0.0",
                "sha256": hashlib.sha256(prerequisite_candidate_bytes).hexdigest(),
            },
            {
                "path": "prerequisite-graph.json",
                "artifact_kind": "prerequisite-graph",
                "schema_version": "1.0.0",
                "sha256": hashlib.sha256(prerequisite_graph_bytes).hexdigest(),
            },
        ],
        "units": copy.deepcopy(units),
    }


def _project(testcase, context, candidates):
    api = _module(testcase, "phase5_curriculum")
    raw = dumps_artifact(candidates).encode("utf-8")
    return api.project_curriculum_outline(
        candidates,
        graph=context["graph"],
        evidence=context["evidence"],
        prerequisite_graph=context["prerequisite_graph"],
        candidate_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _write_builder_inputs(context, curriculum_candidates):
    context["prerequisite_candidates_path"].write_bytes(
        dumps_artifact(context["prerequisite_candidates"]).encode("utf-8"),
    )
    context["prerequisite_graph_path"].write_bytes(
        dumps_artifact(context["prerequisite_graph"]).encode("utf-8"),
    )
    context["curriculum_candidates_path"].write_bytes(
        dumps_artifact(curriculum_candidates).encode("utf-8"),
    )


def _build(testcase, context, curriculum_candidates):
    _write_builder_inputs(context, curriculum_candidates)
    api = _module(testcase, "build_curriculum")
    return api.build_curriculum(
        context["curriculum_candidates_path"],
        prerequisite_candidates_path=context["prerequisite_candidates_path"],
        prerequisite_graph_path=context["prerequisite_graph_path"],
        package_dir=context["package"],
        run_dir=context["run_dir"],
        root=context["fixture"]["root"],
        out=context["curriculum_output_path"],
    )


def _starter_units(context):
    refs = _e1_reference(context)
    return [
        _unit(
            "project-purpose", "project-purpose", "What this project is for",
            scope="PROJECT_SPECIFIC", project_refs=refs,
        ),
        _unit(
            "workflow-map", "workflow", "Follow one real workflow",
            scope="PROJECT_SPECIFIC", introduces=("http-client",), project_refs=refs,
        ),
        _unit(
            "request-path", "source", "Trace the request path",
            scope="PROJECT_SPECIFIC", requires=("http-client",), project_refs=refs,
        ),
    ]


class Phase5CurriculumProjectionTests(unittest.TestCase):
    def test_beginner_outline_inserts_only_transitive_prerequisites_before_first_use(self):
        context = _phase5_context(self)
        result = _project(self, context, _curriculum_candidates(context, _starter_units(context)))

        self.assertEqual("curriculum", result["artifact_kind"])
        self.assertEqual("1.1.0", result["schema_version"])
        self.assertEqual("BEGINNER", result["learner_profile"])
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual(context["graph"]["status"], result["source_status"])
        self.assertEqual(context["graph"]["source_metadata"], result["source_metadata"])
        self.assertEqual(
            ["project-purpose", "prerequisite", "prerequisite", "workflow", "source"],
            [unit["kind"] for unit in result["units"]],
        )
        primers = [unit for unit in result["units"] if unit["origin"] == "PREREQUISITE_PRIMER"]
        self.assertEqual(["network-basics", "request-response"], [item["primer_concept_key"] for item in primers])
        self.assertNotIn("database-basics", [item["primer_concept_key"] for item in primers])
        self.assertEqual(list(range(1, 6)), [unit["order"] for unit in result["units"]])
        self.assertTrue(all(unit["depth"] == 1 for unit in result["units"]))
        self.assertTrue(all(unit["content_status"] == "OUTLINE_ONLY" for unit in result["units"]))
        self.assertTrue(all(unit["epistemic_status"] == "UNVERIFIED_TEACHING" for unit in result["units"]))

    def test_project_purpose_map_precedes_general_introduction_without_added_concepts(self):
        context = _phase5_context(self)
        refs = _e1_reference(context)
        units = [
            _unit(
                "why-prepare-before-retrieval", "project-purpose",
                "Why systems prepare documents before retrieval",
            ),
            _unit(
                "ragent-purpose-and-project-map", "project-purpose",
                "Ragent purpose and project map", scope="PROJECT_SPECIFIC", project_refs=refs,
            ),
            _unit("workflow-map", "workflow", "Follow one real workflow", scope="PROJECT_SPECIFIC", project_refs=refs),
        ]

        result = _project(self, context, _curriculum_candidates(context, units))
        candidate_units = [unit for unit in result["units"] if unit["origin"] == "CANDIDATE"]

        self.assertEqual(
            ["ragent-purpose-and-project-map", "why-prepare-before-retrieval", "workflow-map"],
            [unit["unit_key"] for unit in candidate_units],
        )
        purpose_map = candidate_units[0]
        self.assertEqual("project-purpose", purpose_map["kind"])
        self.assertEqual("Ragent purpose and project map", purpose_map["title"])
        self.assertEqual("PROJECT_SPECIFIC", purpose_map["scope"])
        self.assertEqual([], purpose_map["introduces_concept_keys"])
        self.assertEqual([], purpose_map["requires_concept_keys"])
        self.assertEqual(refs, purpose_map["project_refs"])

    def test_unit_ids_use_stable_snapshot_and_unit_keys(self):
        context = _phase5_context(self)
        candidate_units = _starter_units(context)
        result = _project(self, context, _curriculum_candidates(context, candidate_units))
        snapshot_key = canonical_tuple_sha256((
            context["graph"]["repository_revision"],
            context["graph"]["snapshot_kind"],
            context["graph"]["source_metadata"],
        ))
        purpose = next(unit for unit in result["units"] if unit.get("unit_key") == "project-purpose")
        self.assertEqual(
            "CURRICULUM-UNIT-" + canonical_tuple_sha256(("curriculum-unit", snapshot_key, "project-purpose")),
            purpose["id"],
        )
        request_node = next(
            node for node in context["prerequisite_graph"]["nodes"]
            if node["concept_key"] == "network-basics"
        )
        primer = next(unit for unit in result["units"] if unit.get("primer_concept_key") == "network-basics")
        self.assertEqual(
            "CURRICULUM-PRIMER-" + canonical_tuple_sha256(("curriculum-primer", snapshot_key, request_node["id"])),
            primer["id"],
        )
        renamed = copy.deepcopy(candidate_units)
        for unit in renamed:
            unit["title"] += " — revised title"
        renamed_result = _project(self, context, _curriculum_candidates(context, renamed))
        original_ids = {
            (unit.get("unit_key"), unit.get("primer_concept_key")): unit["id"]
            for unit in result["units"]
        }
        renamed_ids = {
            (unit.get("unit_key"), unit.get("primer_concept_key")): unit["id"]
            for unit in renamed_result["units"]
        }
        self.assertEqual(original_ids, renamed_ids)

    def test_same_unit_required_concept_is_rejected(self):
        context = _phase5_context(self)
        units = _starter_units(context)
        units[1]["requires_concept_keys"] = ["http-client"]
        with self.assertRaises(_module(self, "phase5_curriculum").Phase5CurriculumError) as caught:
            _project(self, context, _curriculum_candidates(context, units))
        self.assertEqual("ORDER_INVALID", caught.exception.code)

    def test_same_unit_transitive_prerequisite_pair_is_rejected(self):
        context = _phase5_context(self)
        units = _starter_units(context)
        units[1]["introduces_concept_keys"] = ["http-client", "network-basics"]
        with self.assertRaises(_module(self, "phase5_curriculum").Phase5CurriculumError) as caught:
            _project(self, context, _curriculum_candidates(context, units))
        self.assertEqual("ORDER_INVALID", caught.exception.code)

    def test_candidate_dependency_cycle_is_rejected(self):
        context = _phase5_context(self)
        units = [
            _unit("project-purpose", "project-purpose", "Project purpose"),
            _unit(
                "workflow-a", "workflow", "Workflow A",
                introduces=("http-client",), requires=("request-response",),
            ),
            _unit(
                "workflow-b", "workflow", "Workflow B",
                introduces=("request-response",), requires=("http-client",),
            ),
        ]
        with self.assertRaises(_module(self, "phase5_curriculum").Phase5CurriculumError) as caught:
            _project(self, context, _curriculum_candidates(context, units))
        self.assertEqual("ORDER_INVALID", caught.exception.code)

    def test_candidate_repository_revision_must_match_authenticated_source(self):
        context = _phase5_context(self)
        candidate = _curriculum_candidates(context, _starter_units(context))
        candidate["repository_revision"] = "f" * 40
        with self.assertRaises(_module(self, "phase5_curriculum").Phase5CurriculumError) as caught:
            _project(self, context, candidate)
        self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

    def test_business_and_workflow_cannot_replace_project_purpose(self):
        context = _phase5_context(self)
        refs = _e1_reference(context)
        units = [
            _unit("business-context", "business", "Business context", scope="PROJECT_SPECIFIC", project_refs=refs),
            _unit("workflow-map", "workflow", "A workflow", scope="PROJECT_SPECIFIC", project_refs=refs),
            _unit("architecture-map", "architecture", "Architecture", scope="PROJECT_SPECIFIC", project_refs=refs),
        ]
        with self.assertRaises(_module(self, "phase5_curriculum").Phase5CurriculumError) as caught:
            _project(self, context, _curriculum_candidates(context, units))
        self.assertEqual("ORDER_INVALID", caught.exception.code)

    def test_candidate_array_reordering_preserves_projection_but_changes_raw_digest(self):
        context = _phase5_context(self)
        first_candidate = _curriculum_candidates(context, _starter_units(context))
        second_candidate = copy.deepcopy(first_candidate)
        second_candidate["units"].reverse()
        first = _project(self, context, first_candidate)
        second = _project(self, context, second_candidate)

        first_units = [{key: value for key, value in unit.items()} for unit in first["units"]]
        second_units = [{key: value for key, value in unit.items()} for unit in second["units"]]
        self.assertEqual(first_units, second_units)
        self.assertNotEqual(first["candidate_input"]["sha256"], second["candidate_input"]["sha256"])
        self.assertNotEqual(dumps_artifact(first), dumps_artifact(second))

    def test_e6_primer_status_is_not_promoted_to_unit_epistemic_status(self):
        context = _phase4c_context(self)
        source_node, source_evidence = _e6_reference(context)
        e6_node = _prerequisite_node(
            context,
            "provisional-mechanism",
            scope="PROJECT_SPECIFIC",
            status="E6_PROVISIONAL",
            refs={
                "graph_node_ids": [source_node["id"]],
                "graph_edge_ids": [],
                "evidence_ids": [source_evidence["id"]],
                "file_paths": [],
            },
        )
        prerequisite_candidates = _prerequisite_candidate(context, [e6_node])
        context["prerequisite_candidates"] = prerequisite_candidates
        context["prerequisite_graph"] = _project_prerequisites(self, context, prerequisite_candidates)
        units = [
            _unit(
                "project-purpose", "project-purpose", "Project purpose",
                scope="PROJECT_SPECIFIC", project_refs=_e1_reference(context),
            ),
            _unit(
                "workflow-map", "workflow", "Workflow",
                scope="PROJECT_SPECIFIC", requires=("provisional-mechanism",),
                project_refs=_e1_reference(context),
            ),
        ]
        result = _project(self, context, _curriculum_candidates(context, units))
        primer = next(unit for unit in result["units"] if unit["origin"] == "PREREQUISITE_PRIMER")
        self.assertEqual("E6_PROVISIONAL", primer["concept_epistemic_status"])
        self.assertEqual("UNVERIFIED_TEACHING", primer["epistemic_status"])

    def test_final_order_validator_rejects_source_concept_used_before_late_primer(self):
        context = _phase5_context(self)
        result = _project(self, context, _curriculum_candidates(context, _starter_units(context)))
        mutated = copy.deepcopy(result)
        network_primer = next(
            unit for unit in mutated["units"]
            if unit.get("primer_concept_key") == "network-basics"
        )
        request_primer = next(
            unit for unit in mutated["units"]
            if unit.get("primer_concept_key") == "request-response"
        )
        source_unit = next(unit for unit in mutated["units"] if unit["kind"] == "source")
        source_unit["requires_concept_keys"] = ["network-basics"]
        request_primer["requires_concept_keys"] = []
        request_primer["prerequisite_ids"] = []
        mutated["units"].remove(network_primer)
        mutated["units"].append(network_primer)
        for order, unit in enumerate(mutated["units"], 1):
            unit["order"] = order

        self.assertIs(mutated, validate_artifact(mutated))
        api = _module(self, "phase5_curriculum")
        with self.assertRaises(api.Phase5CurriculumError) as caught:
            api._validate_output_order(mutated)
        self.assertEqual("ORDER_INVALID", caught.exception.code)


class Phase5CurriculumBuildTests(unittest.TestCase):
    def test_profile_intake_rejects_mixed_versions_and_mismatched_digests(self):
        context = _phase5_context(self)
        api = _module(self, "build_curriculum")
        curriculum_api = _module(self, "phase5_curriculum")
        legacy = _curriculum_candidates(context, _starter_units(context))
        phase4_digests = {
            name: hashlib.sha256((context["package"] / name).read_bytes()).hexdigest()
            for name in ("evidence.json", "knowledge-graph.json", "semantic-proposals.json")
        }
        prerequisite_candidate_raw = dumps_artifact(context["prerequisite_candidates"]).encode("utf-8")
        prerequisite_graph_raw = dumps_artifact(context["prerequisite_graph"]).encode("utf-8")
        api._check_phase4_digests(legacy, phase4_digests)
        api._check_prerequisite_digests(legacy, prerequisite_candidate_raw, prerequisite_graph_raw)

        inverse_phase4_mix = copy.deepcopy(legacy)
        documentation_profile = curriculum_api.phase5_input_profile({"schema_version": "1.1.0"})
        for row, (_path, _kind, version) in zip(
            inverse_phase4_mix["phase4_inputs"], documentation_profile["phase4_inputs"],
        ):
            row["schema_version"] = version
        with self.assertRaises(curriculum_api.Phase5CurriculumError) as caught:
            api._check_phase4_digests(inverse_phase4_mix, phase4_digests)
        self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

        inverse_prerequisite_mix = copy.deepcopy(legacy)
        for row, (_path, _kind, version) in zip(
            inverse_prerequisite_mix["prerequisite_inputs"], documentation_profile["prerequisite_inputs"],
        ):
            row["schema_version"] = version
        with self.assertRaises(curriculum_api.Phase5CurriculumError) as caught:
            api._check_prerequisite_digests(
                inverse_prerequisite_mix, prerequisite_candidate_raw, prerequisite_graph_raw,
            )
        self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

        documentation = copy.deepcopy(legacy)
        documentation["schema_version"] = "1.1.0"
        profile = curriculum_api.phase5_input_profile(documentation)
        for row, (_path, _kind, version) in zip(documentation["phase4_inputs"], profile["phase4_inputs"]):
            row["schema_version"] = version
        for row, (_path, _kind, version) in zip(documentation["prerequisite_inputs"], profile["prerequisite_inputs"]):
            row["schema_version"] = version
        documentation["prerequisite_inputs"][0]["sha256"] = hashlib.sha256(prerequisite_candidate_raw).hexdigest()
        documentation["prerequisite_inputs"][1]["sha256"] = hashlib.sha256(prerequisite_graph_raw).hexdigest()
        api._check_phase4_digests(documentation, phase4_digests)
        api._check_prerequisite_digests(documentation, prerequisite_candidate_raw, prerequisite_graph_raw)

        for mutate in (
            lambda candidate: candidate["phase4_inputs"][0].update(schema_version="1.2.0"),
            lambda candidate: candidate["prerequisite_inputs"][1].update(schema_version="1.0.0"),
            lambda candidate: candidate["phase4_inputs"][1].update(sha256="0" * 64),
            lambda candidate: candidate["prerequisite_inputs"][1].update(sha256="0" * 64),
        ):
            mixed = copy.deepcopy(documentation)
            mutate(mixed)
            with self.subTest(candidate=mixed), self.assertRaises(curriculum_api.Phase5CurriculumError) as caught:
                if mixed["phase4_inputs"] != documentation["phase4_inputs"]:
                    api._check_phase4_digests(mixed, phase4_digests)
                else:
                    api._check_prerequisite_digests(mixed, prerequisite_candidate_raw, prerequisite_graph_raw)
            self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

        with self.assertRaises(curriculum_api.Phase5CurriculumError) as caught:
            curriculum_api.project_curriculum_outline(
                documentation,
                graph=context["graph"],
                evidence=context["evidence"],
                prerequisite_graph=context["prerequisite_graph"],
                candidate_sha256="a" * 64,
            )
        self.assertEqual("INPUT_INVALID", caught.exception.code)

    def test_builder_replays_source_once_and_publishes_canonical_partial_outline(self):
        context = _phase5_context(self)
        candidate = _curriculum_candidates(context, _starter_units(context))
        _write_builder_inputs(context, candidate)
        phase4 = _module(self, "phase4_proposals")
        api = _module(self, "build_curriculum")
        with patch.object(
            phase4, "reproject_phase4c_artifacts", wraps=phase4.reproject_phase4c_artifacts,
        ) as replay:
            result = api.build_curriculum(
                context["curriculum_candidates_path"],
                prerequisite_candidates_path=context["prerequisite_candidates_path"],
                prerequisite_graph_path=context["prerequisite_graph_path"],
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
                out=context["curriculum_output_path"],
            )
        self.assertEqual(1, replay.call_count)
        self.assertEqual(result, load_artifact(context["curriculum_output_path"]))
        self.assertEqual(dumps_artifact(result).encode("utf-8"), context["curriculum_output_path"].read_bytes())
        self.assertEqual("PARTIAL", result["status"])

    def test_noncanonical_phase5a_graph_bytes_fail_closed(self):
        context = _phase5_context(self)
        candidate = _curriculum_candidates(context, _starter_units(context))
        _write_builder_inputs(context, candidate)
        context["prerequisite_graph_path"].write_bytes(
            context["prerequisite_graph_path"].read_bytes() + b" \n",
        )
        candidate["prerequisite_inputs"][1]["sha256"] = hashlib.sha256(
            context["prerequisite_graph_path"].read_bytes(),
        ).hexdigest()
        context["curriculum_candidates_path"].write_bytes(dumps_artifact(candidate).encode("utf-8"))
        api = _module(self, "build_curriculum")
        with self.assertRaises(api.Phase5CurriculumError) as caught:
            api.build_curriculum(
                context["curriculum_candidates_path"],
                prerequisite_candidates_path=context["prerequisite_candidates_path"],
                prerequisite_graph_path=context["prerequisite_graph_path"],
                package_dir=context["package"],
                run_dir=context["run_dir"],
                root=context["fixture"]["root"],
                out=context["curriculum_output_path"],
            )
        self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)
        self.assertFalse(context["curriculum_output_path"].exists())

    def test_candidate_mutation_during_final_freshness_fails_without_output(self):
        context = _phase5_context(self)
        candidate = _curriculum_candidates(context, _starter_units(context))
        _write_builder_inputs(context, candidate)
        phase4 = _module(self, "phase4_proposals")
        api = _module(self, "build_curriculum")
        original = phase4.verify_phase4c_source_freshness

        def mutate_after_freshness(*args, **kwargs):
            original(*args, **kwargs)
            context["curriculum_candidates_path"].write_bytes(
                context["curriculum_candidates_path"].read_bytes() + b"\n",
            )

        with patch.object(phase4, "verify_phase4c_source_freshness", side_effect=mutate_after_freshness):
            with self.assertRaises(api.Phase5CurriculumError) as caught:
                api.build_curriculum(
                    context["curriculum_candidates_path"],
                    prerequisite_candidates_path=context["prerequisite_candidates_path"],
                    prerequisite_graph_path=context["prerequisite_graph_path"],
                    package_dir=context["package"],
                    run_dir=context["run_dir"],
                    root=context["fixture"]["root"],
                    out=context["curriculum_output_path"],
                )
        self.assertEqual("INPUT_CHANGED", caught.exception.code)
        self.assertFalse(context["curriculum_output_path"].exists())

    def test_phase4_package_member_mutation_during_final_freshness_fails_without_output(self):
        context = _phase5_context(self)
        _write_builder_inputs(context, _curriculum_candidates(context, _starter_units(context)))
        phase4 = _module(self, "phase4_proposals")
        api = _module(self, "build_curriculum")
        original = phase4.verify_phase4c_source_freshness
        proposal_path = context["package"] / "semantic-proposals.json"

        def mutate_after_freshness(*args, **kwargs):
            original(*args, **kwargs)
            proposal_path.write_bytes(proposal_path.read_bytes() + b"\n")

        with patch.object(phase4, "verify_phase4c_source_freshness", side_effect=mutate_after_freshness):
            with self.assertRaises(api.Phase5CurriculumError) as caught:
                api.build_curriculum(
                    context["curriculum_candidates_path"],
                    prerequisite_candidates_path=context["prerequisite_candidates_path"],
                    prerequisite_graph_path=context["prerequisite_graph_path"],
                    package_dir=context["package"],
                    run_dir=context["run_dir"],
                    root=context["fixture"]["root"],
                    out=context["curriculum_output_path"],
                )
        self.assertEqual("INPUT_CHANGED", caught.exception.code)
        self.assertFalse(context["curriculum_output_path"].exists())

    def test_publication_failure_leaves_no_output_or_staging_directory(self):
        context = _phase5_context(self)
        _write_builder_inputs(context, _curriculum_candidates(context, _starter_units(context)))
        api = _module(self, "build_curriculum")
        with patch.object(api.os, "link", side_effect=OSError):
            with self.assertRaises(api.Phase5CurriculumError) as caught:
                api.build_curriculum(
                    context["curriculum_candidates_path"],
                    prerequisite_candidates_path=context["prerequisite_candidates_path"],
                    prerequisite_graph_path=context["prerequisite_graph_path"],
                    package_dir=context["package"],
                    run_dir=context["run_dir"],
                    root=context["fixture"]["root"],
                    out=context["curriculum_output_path"],
                )
        self.assertEqual("OUTPUT_FAILED", caught.exception.code)
        self.assertFalse(context["curriculum_output_path"].exists())
        self.assertEqual(
            [],
            list(context["curriculum_output_path"].parent.glob(
                f".{context['curriculum_output_path'].name}.phase5-curriculum-staging-*",
            )),
        )


if __name__ == "__main__":
    unittest.main()

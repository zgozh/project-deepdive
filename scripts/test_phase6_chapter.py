#!/usr/bin/env python3
"""Focused tests for the authenticated Phase 6A Markdown chapter draft."""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_contract import dumps_artifact, load_artifact, validate_artifact  # noqa: E402
from phase4_graph import canonical_tuple_sha256  # noqa: E402
from test_phase4_claim_evidence_4c import _claim, _claim_id  # noqa: E402
from test_phase4_claim_evidence_4c import _save_candidate as _save_claim_candidate  # noqa: E402
from test_phase5_curriculum import (  # noqa: E402
    _build as _build_curriculum,
    _curriculum_candidates,
    _phase5_context,
    _starter_units,
    _write_builder_inputs,
)
from test_phase5_prerequisites import _e1_reference  # noqa: E402


HEADINGS = (
    "Intuition", "Prerequisite", "Project use", "Architecture", "Source",
    "Mechanism", "Failure and debugging", "Extension", "Learning checks",
)


def _phase4c_git_tree_context(testcase: unittest.TestCase, source_files):
    from test_phase3_run import _git_tree_run, _write_inputs
    import test_phase4_proposals as proposal_tests
    import test_phase5_prerequisites as prerequisite_tests

    fixture, run_dir, _manifest = _git_tree_run(testcase, source_files)
    from coverage_audit import audit_coverage
    from phase3_bundle_audit import _build_snapshot_inputs
    from phase3_run import assemble_phase3_run
    from python_static_analysis import analyze_python_artifacts_v11

    g01 = audit_coverage(fixture["project_index"], fixture["coverage"], fixture["root"])
    source_reader, regular_paths = _build_snapshot_inputs(fixture["root"], fixture["project_index"])
    fixture["python_analysis"], fixture["python_evidence"] = analyze_python_artifacts_v11(
        fixture["project_index"],
        fixture["coverage"],
        fixture["stack_profile"],
        fixture["phase3a_evidence"],
        source_reader,
        regular_paths,
        g01_status=g01.status,
        unknown_files=g01.measurements["unknown_files"],
    )
    input_paths, _contents = _write_inputs(testcase, fixture)
    run_dir = fixture["work"] / "git-tree-run-with-python"
    assemble_phase3_run(root=fixture["root"], input_paths=input_paths, out=run_dir)

    graph_api = importlib.import_module("phase4_graph")
    pair_dir = fixture["work"] / "phase4a"
    graph_api.build_phase4_graph(run_dir, root=fixture["root"], out=pair_dir)
    graph_path = pair_dir / "knowledge-graph.json"
    evidence_path = pair_dir / "evidence.json"
    graph = load_artifact(graph_path)
    evidence = load_artifact(evidence_path)
    base_payload = {
        "category": "concept",
        "kind": "node",
        "label": "A provisional concept",
        "node_type": "Concept",
    }
    proposal = {"id": proposal_tests._proposal_id(graph, base_payload), **base_payload}
    proposal_inputs = [
        {
            "path": "phase4a/evidence.json",
            "artifact_kind": "evidence",
            "schema_version": "1.2.0",
            "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        },
        {
            "path": "phase4a/knowledge-graph.json",
            "artifact_kind": "knowledge-graph",
            "schema_version": "1.1.0",
            "sha256": hashlib.sha256(graph_path.read_bytes()).hexdigest(),
        },
    ]
    proposal_candidate = {
        "artifact_kind": "semantic-proposals",
        "schema_version": "1.0.0",
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_run": copy.deepcopy(graph["source_run"]),
        "phase4_inputs": proposal_inputs,
        "proposals": [proposal],
    }
    proposals_path = fixture["work"] / "semantic-proposals.json"
    proposals_path.write_bytes(dumps_artifact(proposal_candidate).encode("utf-8"))
    package = fixture["work"] / "phase4c-output"
    importlib.import_module("phase4_proposals").import_semantic_proposals(
        proposals_path,
        graph_path=graph_path,
        evidence_path=evidence_path,
        run_dir=run_dir,
        root=fixture["root"],
        out=package,
    )

    context = {
        "fixture": fixture,
        "run_dir": run_dir,
        "graph": load_artifact(package / "knowledge-graph.json"),
        "evidence": load_artifact(package / "evidence.json"),
        "package": package,
        "candidate_path": fixture["work"] / "prerequisite-candidates.json",
        "result_path": fixture["work"] / "prerequisite-graph.json",
    }
    refs = prerequisite_tests._e1_reference(context)
    network = prerequisite_tests._node(context, "network-basics", scope="GENERAL_LEARNING")
    request_response = prerequisite_tests._node(context, "request-response", scope="GENERAL_LEARNING")
    http_client = prerequisite_tests._node(
        context, "http-client", scope="PROJECT_SPECIFIC", kind="FrameworkMechanism", refs=refs,
    )
    unrelated = prerequisite_tests._node(context, "database-basics", scope="GENERAL_LEARNING")
    prerequisite_candidates = prerequisite_tests._candidate(
        context,
        [network, request_response, http_client, unrelated],
        [
            prerequisite_tests._edge(context, http_client, request_response),
            prerequisite_tests._edge(context, request_response, network),
        ],
    )
    context.update({
        "prerequisite_candidates": prerequisite_candidates,
        "prerequisite_graph": importlib.import_module("phase5_prerequisites").project_prerequisite_graph(
            prerequisite_candidates,
            graph=context["graph"],
            evidence=context["evidence"],
            candidate_sha256=hashlib.sha256(dumps_artifact(prerequisite_candidates).encode("utf-8")).hexdigest(),
        ),
        "prerequisite_candidates_path": fixture["work"] / "prerequisite-candidates.json",
        "prerequisite_graph_path": fixture["work"] / "prerequisite-graph.json",
        "curriculum_candidates_path": fixture["work"] / "curriculum-candidates.json",
        "curriculum_output_path": fixture["work"] / "curriculum.json",
    })
    return context


def _module(testcase: unittest.TestCase):
    testcase.assertIsNotNone(
        importlib.util.find_spec("phase6_chapter"),
        "the Phase 6A chapter compiler is required",
    )
    return importlib.import_module("phase6_chapter")


def _phase6_fixture(testcase: unittest.TestCase, *, source_files=None, git_tree=False):
    context = (
        _phase4c_git_tree_context(
            testcase, source_files or {"src/module.py": b"def main():\n    return 1\n"},
        )
        if git_tree
        else _phase5_context(testcase, source_files=source_files)
    )
    graph = context["graph"]
    evidence = context["evidence"]
    references = _e1_reference(context)
    evidence_id = references["evidence_ids"][0]
    symbol_id = references["graph_node_ids"][0]
    claim_text = "The selected source symbol is linked to this evidence item."
    claim = _claim(
        graph,
        text=claim_text,
        evidence_ids=[evidence_id],
        graph_node_ids=[symbol_id],
        critical=False,
    )
    claim_candidate = {
        "artifact_kind": "claim-candidates",
        "schema_version": "1.1.0",
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_run": copy.deepcopy(graph["source_run"]),
        "phase4_inputs": copy.deepcopy(context["prerequisite_candidates"]["phase4_inputs"]),
        "claims": [claim],
    }
    claims_path = context["fixture"]["work"] / "phase6-claim-candidates.json"
    claims_path.write_bytes(dumps_artifact(claim_candidate).encode("utf-8"))
    claim_module = importlib.import_module("phase4_claim_evidence")
    report = claim_module.audit_claim_candidates_4c(
        claims_path,
        package_dir=context["package"],
        run_dir=context["run_dir"],
        root=context["fixture"]["root"],
    )
    report_path = context["fixture"]["work"] / "phase6-claim-evidence.json"
    report_path.write_bytes(dumps_artifact(report).encode("utf-8"))
    overlay_path = context["fixture"]["work"] / "phase6-claim-evidence-graph.json"
    claim_module.build_claim_evidence_graph_4c(
        claims_path,
        report_path,
        package_dir=context["package"],
        run_dir=context["run_dir"],
        root=context["fixture"]["root"],
        out=overlay_path,
    )

    candidates = _curriculum_candidates(context, _starter_units(context))
    _write_builder_inputs(context, candidates)
    curriculum = _build_curriculum(testcase, context, candidates)
    context.update({
        "claim": claim,
        "claim_candidate": claim_candidate,
        "claims_path": claims_path,
        "report_path": report_path,
        "overlay_path": overlay_path,
        "curriculum_candidates": candidates,
        "curriculum": curriculum,
    })

    chapter_api = _module(testcase)
    inputs = chapter_api.ChapterInputPaths(
        phase4c_package=context["package"],
        claim_candidates_path=claims_path,
        claim_evidence_path=report_path,
        claim_evidence_graph_path=overlay_path,
        prerequisite_candidates_path=context["prerequisite_candidates_path"],
        prerequisite_graph_path=context["prerequisite_graph_path"],
        curriculum_candidates_path=context["curriculum_candidates_path"],
        curriculum_path=context["curriculum_output_path"],
        run_dir=context["run_dir"],
        root=context["fixture"]["root"],
    )
    selected_unit = next(unit for unit in curriculum["units"] if unit["kind"] == "project-purpose")
    output_dir = context["fixture"]["work"] / "phase6-chapter-output"
    return context, inputs, selected_unit["id"], output_dir


def _add_v2_excerpt_evidence(context, facts):
    source = next(
        item for item in context["evidence"]["items"]
        if item["level"] == "E1"
        and item["locator"].get("path")
        and item["locator"].get("line_start")
        and item["locator"].get("line_end")
    )
    locator = {
        key: source["locator"][key]
        for key in ("path", "symbol", "line_start", "line_end")
        if key in source["locator"]
    }
    excerpt_evidence = {
        "id": source["id"],
        "level": "E1",
        "provisional": False,
        "locator": locator,
        "source_members": sorted(set(source["source_members"])),
    }
    facts["evidence"].append(excerpt_evidence)
    return excerpt_evidence


def _footnote_line(facts, claim_id):
    evidence_by_id = {item["id"]: item for item in facts["evidence"]}
    claim = next(row for row in facts["claims"] if row["id"] == claim_id)
    cited = []
    for evidence_id in claim["evidence_ids"]:
        item = evidence_by_id[evidence_id]
        locator = {
            key: item["locator"][key]
            for key in ("path", "symbol", "line_start", "line_end")
            if key in item["locator"]
        }
        cited.append({"id": evidence_id, "level": item["level"], "locator": locator})
    payload = json.dumps({"evidence": cited}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"[^CLAIM-{claim_id.removeprefix('CLAIM-')}]: {payload}"


def _valid_chapter(facts, claim_text):
    selected = facts["selected_unit"]
    claim = facts["claims"][0]
    evidence_id = claim["evidence_ids"][0]
    chapter_id = selected["chapter_id"]
    concepts = ",".join(selected["prerequisite_concept_keys"]) or "none"
    claim_refs = claim["id"]
    evidence_refs = ",".join(claim["evidence_ids"]) or "none"
    lines = ["# Chapter"]
    for heading in HEADINGS[:-1]:
        lines.extend(["", f"## {heading}"])
        if heading == "Project use":
            lines.append(f"Claim: {claim_text} [^CLAIM-{claim['id'].removeprefix('CLAIM-')}]")
        elif heading in {"Architecture", "Source", "Mechanism", "Failure and debugging", "Extension"}:
            lines.append("> Evidence gap — NOT ESTABLISHED IN THIS SLICE")
        else:
            lines.append("General: Start by asking what problem the system is intended to solve.")
    lines.extend(["", "## Learning checks"])
    for key, kind, prompt in (
        ("recall-purpose", "recall", "Which source-backed statement appears in this chapter?"),
        ("trace-next", "trace_predict", "Which evidence locator would you inspect next?"),
    ):
        lines.extend([
            "",
            f":::exercise id=EX-{chapter_id}-{key} type={kind} prerequisites={concepts} claims={claim_refs} evidence={evidence_refs}",
            f"Question: {prompt}",
            ":::",
        ])
    lines.extend(["", _footnote_line(facts, claim["id"]), ""])
    return "\n".join(lines)


def _v2_excerpt(api, facts, inputs, *, first=None, last=None, evidence=None):
    evidence = evidence or next(
        item for item in facts["evidence"]
        if item["level"] == "E1"
        and item["locator"].get("path")
        and item["locator"].get("line_start")
        and item["locator"].get("line_end")
    )
    locator = evidence["locator"]
    first = locator["line_start"] if first is None else first
    last = first if last is None else last
    source = api._read_snapshot_source(locator["path"], facts=facts, run_dir=inputs.run_dir, root=inputs.root)
    selected = b"".join(source.splitlines(keepends=True)[first - 1:last])
    body = selected.decode("utf-8")
    if not body.endswith("\n"):
        body += "\n"
    encoded_path = api.quote(locator["path"], safe="/-._~")
    caption = f"Source location: {locator['path']}:{first}-{last}"
    marker = (
        f"<!-- SOURCE-EXCERPT evidence={evidence['id']} path={encoded_path} "
        f"lines={first}-{last} sha256={hashlib.sha256(selected).hexdigest()} -->"
    )
    return f"{caption}\n{marker}\n```text\n{body}```\n<!-- /SOURCE-EXCERPT -->"


def _valid_v2_chapter(facts, claim_text, inputs):
    api = importlib.import_module("phase6_chapter")
    # The focused fixture provides one authenticated E1 source range.
    return _valid_v2_chapter_with_excerpt(api, facts, claim_text, _v2_excerpt(api, facts, inputs))


def _valid_v2_chapter_with_excerpt(api, facts, claim_text, excerpt):
    claim = facts["claims"][0]
    marker = f"[^CLAIM-{claim['id'].removeprefix('CLAIM-')}]"
    chapter_id = facts["selected_unit"]["chapter_id"]
    concepts = ",".join(facts["selected_unit"]["prerequisite_concept_keys"]) or "none"
    claim_refs = claim["id"]
    evidence_refs = ",".join(claim["evidence_ids"]) or "none"
    lines = ["# Chapter", "<!-- project-deepdive-chapter-format: v2 -->"]
    for heading in HEADINGS[:-1]:
        lines.extend(["", f"## {heading}"])
        if heading == "Intuition":
            lines.append("A request can cross several layers. This chapter follows one selected source unit.")
        elif heading == "Prerequisite":
            lines.append("A repository snapshot is a fixed view of files. A locator names one bounded range。")
        elif heading == "Project use":
            lines.append(f"{claim_text} {marker}")
        elif heading == "Architecture":
            lines.append(f"- {claim_text} {marker}")
        elif heading == "Source":
            lines.extend(excerpt.splitlines())
        elif heading == "Mechanism":
            lines.append("Each function can be read as a small input-to-output transformation！")
        elif heading == "Failure and debugging":
            lines.append("A focused test changes one input at a time. Its result can be compared with the expected behavior?")
        elif heading == "Extension":
            lines.append("A hypothetical change can be checked with a focused test. A failure narrows where the change broke.")
    lines.extend(["", "## Learning checks"])
    for key, kind, prompt in (
        ("recall-purpose", "recall", "Which source-backed statement appears in this chapter?"),
        ("trace-next", "trace_predict", "Which evidence locator would you inspect next?"),
    ):
        lines.extend([
            "",
            f":::exercise id=EX-{chapter_id}-{key} type={kind} prerequisites={concepts} claims={claim_refs} evidence={evidence_refs}",
            f"Question: {prompt}",
            ":::",
        ])
    lines.extend(["", _footnote_line(facts, claim["id"]), ""])
    return "\n".join(lines)


class Phase6ChapterTests(unittest.TestCase):
    def test_authenticated_digest_inventory_preserves_legacy_and_selects_exact_documentation_profile(self):
        api = _module(self)
        profiles = {
            "1.0.0": {
                "phase4": (
                    ("phase4_base_graph", "knowledge-graph", "1.1.0", "knowledge-graph"),
                    ("phase4_base_evidence", "evidence", "1.2.0", "evidence"),
                    ("phase4_semantic_proposals", "semantic-proposals", "1.0.0", "semantic-proposals"),
                    ("phase4_claim_candidates", "claim-candidates", "1.1.0", "claim_candidates"),
                    ("phase4_claim_evidence", "claim-evidence", "1.1.0", "claim_evidence"),
                    ("phase4_claim_evidence_graph", "claim-evidence-graph", "1.0.0", "claim_evidence_graph"),
                ),
                "phase5": (
                    ("phase5_prerequisite_candidates", "prerequisite-candidates", "1.0.0", "prerequisite_candidates"),
                    ("phase5_prerequisite_graph", "prerequisite-graph", "1.0.0", "prerequisite_graph"),
                    ("phase5_curriculum_candidates", "curriculum-candidates", "1.0.0", "curriculum_candidates"),
                    ("phase5_curriculum", "curriculum", "1.1.0", "curriculum"),
                ),
            },
            "1.1.0": {
                "phase4": (
                    ("phase4_base_graph", "knowledge-graph", "1.2.0", "knowledge-graph"),
                    ("phase4_base_evidence", "evidence", "1.4.0", "evidence"),
                    ("phase4_semantic_proposals", "semantic-proposals", "1.1.0", "semantic-proposals"),
                    ("phase4_claim_candidates", "claim-candidates", "1.2.0", "claim_candidates"),
                    ("phase4_claim_evidence", "claim-evidence", "1.2.0", "claim_evidence"),
                    ("phase4_claim_evidence_graph", "claim-evidence-graph", "1.1.0", "claim_evidence_graph"),
                ),
                "phase5": (
                    ("phase5_prerequisite_candidates", "prerequisite-candidates", "1.1.0", "prerequisite_candidates"),
                    ("phase5_prerequisite_graph", "prerequisite-graph", "1.1.0", "prerequisite_graph"),
                    ("phase5_curriculum_candidates", "curriculum-candidates", "1.1.0", "curriculum_candidates"),
                    ("phase5_curriculum", "curriculum", "1.2.0", "curriculum"),
                ),
            },
        }

        def bundle(candidate_version, authenticated_version):
            expected = profiles[authenticated_version]
            phase4 = {role: (kind, version) for role, kind, version, _lookup in expected["phase4"]}
            phase5 = {lookup: (kind, version) for _role, kind, version, lookup in expected["phase5"]}
            graph = {
                "artifact_kind": "knowledge-graph",
                "schema_version": phase4["phase4_base_graph"][1],
                "derived_inputs": [{
                    "artifact_kind": "semantic-proposals",
                    "schema_version": phase4["phase4_semantic_proposals"][1],
                }],
            }
            evidence = {
                "artifact_kind": "evidence",
                "schema_version": phase4["phase4_base_evidence"][1],
            }
            overlay_inputs = [
                {
                    "role": "claim_candidates",
                    "artifact_kind": phase4["phase4_claim_candidates"][0],
                    "schema_version": phase4["phase4_claim_candidates"][1],
                },
                {
                    "role": "claim_evidence",
                    "artifact_kind": phase4["phase4_claim_evidence"][0],
                    "schema_version": phase4["phase4_claim_evidence"][1],
                },
            ]
            report = {
                "artifact_kind": phase4["phase4_claim_evidence"][0],
                "schema_version": phase4["phase4_claim_evidence"][1],
            }
            overlay = {
                "artifact_kind": phase4["phase4_claim_evidence_graph"][0],
                "schema_version": phase4["phase4_claim_evidence_graph"][1],
                "input_digests": overlay_inputs,
            }
            phase4_sha = {
                lookup: f"{index + 1:064x}"
                for index, (_role, _kind, _version, lookup) in enumerate(expected["phase4"])
            }
            reads = {
                lookup: SimpleNamespace(
                    artifact={"artifact_kind": kind, "schema_version": version},
                    sha256=f"{index + 20:064x}",
                )
                for index, (_role, kind, version, lookup) in enumerate(expected["phase5"])
            }
            reads["curriculum_candidates"].artifact["schema_version"] = candidate_version
            authenticated = SimpleNamespace(
                graph=graph,
                evidence=evidence,
                report=report,
                overlay=overlay,
                input_sha256=phase4_sha,
            )
            return reads, authenticated, expected, phase4_sha

        for version in ("1.0.0", "1.1.0"):
            with self.subTest(candidate_version=version):
                reads, authenticated, expected, phase4_sha = bundle(version, version)
                records = api._authenticated_input_digest_records(reads, authenticated)
                self.assertEqual(10, len(records))
                self.assertEqual(sorted(row[0] for row in (*expected["phase4"], *expected["phase5"])), [row["role"] for row in records])
                expected_versions = {
                    role: (kind, schema_version)
                    for role, kind, schema_version, _lookup in (*expected["phase4"], *expected["phase5"])
                }
                self.assertEqual(
                    expected_versions,
                    {row["role"]: (row["artifact_kind"], row["schema_version"]) for row in records},
                )
                expected_hashes = {
                    role: phase4_sha[lookup]
                    for role, _kind, _schema_version, lookup in expected["phase4"]
                }
                expected_hashes.update({
                    role: reads[lookup].sha256
                    for role, _kind, _schema_version, lookup in expected["phase5"]
                })
                self.assertEqual(expected_hashes, {row["role"]: row["sha256"] for row in records})

        for candidate_version, authenticated_version in (("1.1.0", "1.0.0"), ("1.0.0", "1.1.0")):
            with self.subTest(candidate_version=candidate_version, authenticated_version=authenticated_version):
                reads, authenticated, _expected, _phase4_sha = bundle(candidate_version, authenticated_version)
                with self.assertRaises(api.Phase6ChapterError) as caught:
                    api._authenticated_input_digest_records(reads, authenticated)
                self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

        reads, authenticated, _expected, _phase4_sha = bundle("1.1.0", "1.1.0")
        reads["curriculum"].artifact["schema_version"] = "1.1.0"
        with self.assertRaises(api.Phase6ChapterError) as caught:
            api._authenticated_input_digest_records(reads, authenticated)
        self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

        reads, authenticated, _expected, _phase4_sha = bundle("1.1.0", "1.1.0")
        reads["prerequisite_candidates"].artifact["artifact_kind"] = "curriculum-candidates"
        with self.assertRaises(api.Phase6ChapterError) as caught:
            api._authenticated_input_digest_records(reads, authenticated)
        self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

        reads, authenticated, _expected, _phase4_sha = bundle("1.1.0", "1.1.0")
        reads["curriculum"].sha256 = "z" * 64
        with self.assertRaises(api.Phase6ChapterError) as caught:
            api._authenticated_input_digest_records(reads, authenticated)
        self.assertEqual("PROVENANCE_MISMATCH", caught.exception.code)

    def test_writer_packet_matches_pre_v2_compatibility_fixture(self):
        api = _module(self)
        facts = {
            "selected_unit": {
                "chapter_id": "CHAPTER-COMPAT",
                "curriculum_unit_id": "CURRICULUM-COMPAT",
                "title": "Compatibility fixture",
            },
            "repository_revision": "revision-123",
            "snapshot_kind": "worktree",
            "source_metadata": {"fixture": "packet-compat"},
            "source_run_manifest_sha256": "a" * 64,
            "source_status": "PASS",
            "unknown_files": 0,
            "claims": [],
            "evidence": [],
        }
        expected = (
            "# Phase 6A Writer Packet\n"
            "\n"
            "Status: DRAFT only. Referential coverage is not semantic entailment.\n"
            "Chapter: CHAPTER-COMPAT\n"
            "Curriculum unit: CURRICULUM-COMPAT — Compatibility fixture\n"
            "Snapshot key: 5fae403cdb3e735208c97b8917080e2ef274f3d0ed58911fb92727c3aca47e1d\n"
            "Repository revision: revision-123\n"
            "Snapshot kind: worktree\n"
            f"Phase3 manifest SHA-256: {'a' * 64}\n"
            "Source status: PASS; unknown tracked files: 0\n"
            "\n"
            "## Allowed source selection\n"
            "Inspect only the listed relative locators from the authenticated worktree snapshot; do not follow links or run target code.\n"
            "\n"
            "Use the checked-in prompt at `skills/project-deepdive/prompts/phase6a-markdown-chapter-writer.md`.\n"
            "The exact authenticated claim-candidates input may be read for its user-supplied statement text; never copy that text into sidecars.\n"
            "Copy only an eligible claim's exact canonical footnote definition from the packet's canonical footnote section; do not derive or edit its JSON.\n"
            "Use the required headings and line grammar from the prompt. A `Claim:` line may use only a SUPPORTED claim with E0–E5 and no E6.\n"
            "E6 is inference, and unresolved or unsupported claims are not project facts. Prefer the fixed evidence-gap callout.\n"
            "Learning checks use only question blocks; provide no answers and do not ask the learner to respond.\n"
        )

        self.assertEqual(expected, api._writer_packet(facts))

    def test_prepare_authenticates_once_and_emits_compact_canonical_facts(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        claim_module = importlib.import_module("phase4_claim_evidence")
        real_replay = claim_module.reproject_phase4c_artifacts

        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            facts, packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)

        self.assertEqual(1, replay.call_count)
        facts_path = output_dir / "chapter-facts.json"
        self.assertEqual(dumps_artifact(facts).encode("utf-8"), facts_path.read_bytes())
        self.assertEqual({"chapter-facts.json", "writer-packet.md"}, {path.name for path in output_dir.iterdir()})
        self.assertFalse((output_dir / "chapter.md").exists())
        self.assertEqual(context["curriculum"]["source_status"], facts["source_status"])
        self.assertEqual(context["graph"]["source_metadata"]["unknown_files"], facts["unknown_files"])
        self.assertEqual([context["claim"]["id"]], [row["id"] for row in facts["claims"]])
        normalized = unicodedata.normalize("NFC", context["claim"]["text"].strip())
        self.assertEqual(hashlib.sha256(normalized.encode("utf-8")).hexdigest(), facts["claims"][0]["statement_sha256"])
        snapshot_key = canonical_tuple_sha256((
            facts["repository_revision"], facts["snapshot_kind"], facts["source_metadata"],
        ))
        self.assertIn(f"Snapshot key: {snapshot_key}", packet)
        expected_chapter_id = "CHAPTER-" + canonical_tuple_sha256((
            "phase6-chapter", snapshot_key, unit_id, sorted([context["claim"]["id"]]),
        ))
        self.assertEqual(expected_chapter_id, facts["selected_unit"]["chapter_id"])
        self.assertNotIn(context["claim"]["text"].encode("utf-8"), facts_path.read_bytes())
        self.assertNotIn(context["claim"]["text"], packet)
        self.assertIn(_footnote_line(facts, context["claim"]["id"]), packet)
        validate_artifact(facts)

    def test_bounded_capture_scope_reuses_git_proof_and_isolates_returned_values(self):
        api = _module(self)
        context, inputs, _unit_id, _output_dir = _phase6_fixture(self, git_tree=True)
        self.assertEqual("git-tree", context["curriculum"]["snapshot_kind"])
        original_authentication = api.authenticate_claim_evidence_bundle_4c

        with patch.object(api, "authenticate_claim_evidence_bundle_4c", wraps=original_authentication) as authenticate:
            with api._authenticated_input_reuse_scope():
                first = api._capture_bundle(inputs)
                expected_phase5_reads = copy.deepcopy(first[1])
                expected_graph = copy.deepcopy(first[2])
                expected_curriculum = copy.deepcopy(first[3])

                first[0].graph.clear()
                first[1]["curriculum"].artifact["source_status"] = "MUTATED"
                first[1].clear()
                first[2].clear()
                first[3].clear()

                second = api._capture_bundle(inputs)

        self.assertEqual(1, authenticate.call_count)
        self.assertEqual(expected_phase5_reads, second[1])
        self.assertEqual(expected_graph, second[2])
        self.assertEqual(expected_curriculum, second[3])
        self.assertTrue(second[0].graph)

    def test_bounded_capture_scope_rechecks_phase5_and_source_before_publication(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self, git_tree=True)
        self.assertEqual("git-tree", context["curriculum"]["snapshot_kind"])
        original_authentication = api.authenticate_claim_evidence_bundle_4c
        curriculum_raw = inputs.curriculum_path.read_bytes()
        source_path = context["fixture"]["root"] / "src/module.py"
        source_raw = source_path.read_bytes()
        run_manifest_path = inputs.run_dir / "phase3-run.json"
        run_manifest_raw = run_manifest_path.read_bytes()
        stale_output_dir = context["fixture"]["work"] / "phase6-chapter-after-run-drift"

        try:
            with patch.object(api, "authenticate_claim_evidence_bundle_4c", wraps=original_authentication) as authenticate:
                with api._authenticated_input_reuse_scope():
                    api._capture_bundle(inputs)
                    inputs.curriculum_path.write_bytes(curriculum_raw + b" ")
                    with self.assertRaises(api.Phase6ChapterError) as phase5_changed:
                        api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
                    self.assertEqual("INPUT_CHANGED", phase5_changed.exception.code)
                    self.assertFalse(output_dir.exists())

                    inputs.curriculum_path.write_bytes(curriculum_raw)
                    source_path.write_bytes(source_raw + b"# stale snapshot\n")
                    api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
                    self.assertTrue(output_dir.is_dir())

                    run_manifest_path.write_bytes(run_manifest_raw + b" ")
                    with self.assertRaises(api.Phase6ChapterError):
                        api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=stale_output_dir)
                    self.assertFalse(stale_output_dir.exists())
            self.assertEqual(1, authenticate.call_count)
        finally:
            inputs.curriculum_path.write_bytes(curriculum_raw)
            source_path.write_bytes(source_raw)
            run_manifest_path.write_bytes(run_manifest_raw)

    def test_bounded_capture_scope_is_discarded_after_normal_and_exception_exit(self):
        api = _module(self)
        context, inputs, _unit_id, _output_dir = _phase6_fixture(self, git_tree=True)
        self.assertEqual("git-tree", context["curriculum"]["snapshot_kind"])
        original_authentication = api.authenticate_claim_evidence_bundle_4c

        with patch.object(api, "authenticate_claim_evidence_bundle_4c", wraps=original_authentication) as authenticate:
            with api._authenticated_input_reuse_scope():
                api._capture_bundle(inputs)
                api._capture_bundle(inputs)

            with self.assertRaisesRegex(RuntimeError, "batch failure"):
                with api._authenticated_input_reuse_scope():
                    api._capture_bundle(inputs)
                    api._capture_bundle(inputs)
                    raise RuntimeError("batch failure")

            with api._authenticated_input_reuse_scope():
                api._capture_bundle(inputs)

        self.assertEqual(3, authenticate.call_count)

    def test_bounded_capture_scope_preserves_worktree_full_replay(self):
        api = _module(self)
        context, inputs, _unit_id, _output_dir = _phase6_fixture(self)
        self.assertEqual("worktree", context["curriculum"]["snapshot_kind"])
        original_authentication = api.authenticate_claim_evidence_bundle_4c

        with patch.object(api, "authenticate_claim_evidence_bundle_4c", wraps=original_authentication) as authenticate:
            with api._authenticated_input_reuse_scope():
                api._capture_bundle(inputs)
                api._capture_bundle(inputs)

        self.assertEqual(2, authenticate.call_count)

    def test_valid_markdown_emits_question_only_bank_and_partial_status(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        markdown = _valid_chapter(facts, context["claim"]["text"])
        markdown_path = output_dir / "chapter.md"
        markdown_path.write_text(markdown, encoding="utf-8", newline="\n")

        claim_module = importlib.import_module("phase4_claim_evidence")
        real_replay = claim_module.reproject_phase4c_artifacts
        with patch.object(claim_module, "reproject_phase4c_artifacts", wraps=real_replay) as replay:
            bank, status = api.verify_chapter_draft(
                inputs,
                unit_id=unit_id,
                facts_path=output_dir / "chapter-facts.json",
                markdown_path=markdown_path,
                out_dir=output_dir,
            )

        self.assertEqual(1, replay.call_count)
        self.assertEqual("PASS", status["structural_status"], status["errors"])
        self.assertEqual("PASS", status["check_results"]["reference_integrity"])
        self.assertEqual("DRAFT", status["chapter_status"])
        self.assertEqual("PARTIAL", status["overall_status"])
        self.assertEqual("NOT_RUN", status["semantic_entailment"])
        self.assertEqual("NOT_RUN", status["beginner_critic"])
        self.assertEqual("NOT_AUTHORED", bank["records"][0]["answer_status"])
        self.assertEqual(2, len(bank["records"]))
        self.assertEqual(dumps_artifact(bank).encode("utf-8"), (output_dir / "exercise-bank.json").read_bytes())
        self.assertEqual(hashlib.sha256((output_dir / "exercise-bank.json").read_bytes()).hexdigest(), status["exercise_bank_sha256"])
        self.assertFalse((output_dir / "ANSWER-BOOK.md").exists())

    def test_repeated_supported_claim_across_sections_uses_one_canonical_footnote(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        marker = f"[^CLAIM-{context['claim']['id'].removeprefix('CLAIM-')}]"
        repeated_claim = f"Claim: {context['claim']['text']} {marker}"
        markdown = _valid_chapter(facts, context["claim"]["text"]).replace(
            "> Evidence gap — NOT ESTABLISHED IN THIS SLICE", repeated_claim, 1,
        )
        self.assertEqual(2, markdown.count(repeated_claim))
        self.assertIn(f"## Project use\n{repeated_claim}", markdown)
        self.assertIn(f"## Architecture\n{repeated_claim}", markdown)
        self.assertEqual(1, markdown.splitlines().count(_footnote_line(facts, context["claim"]["id"])))

        bank, checks, errors = api._verify_markdown(markdown.encode("utf-8"), facts, inputs)

        self.assertEqual([], errors)
        self.assertIsNotNone(bank)
        self.assertEqual("PASS", checks["claim_footnotes"])

    def test_v2_accepts_natural_paragraphs_bullets_chinese_punctuation_and_repeated_claims(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        _add_v2_excerpt_evidence(context, facts)
        markdown = _valid_v2_chapter(facts, context["claim"]["text"], inputs)

        bank, checks, errors = api._verify_markdown(markdown.encode("utf-8"), facts, inputs)

        self.assertEqual([], errors)
        self.assertIsNotNone(bank)
        self.assertEqual("PASS", checks["claim_footnotes"])
        self.assertEqual(2, markdown.count(context["claim"]["text"]))
        self.assertEqual(3, markdown.count(f"[^CLAIM-{context['claim']['id'].removeprefix('CLAIM-')}]"))
        self.assertEqual("DRAFT", api._draft_status(
            facts,
            chapter_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            facts_sha256="a" * 64,
            exercise_sha256=None,
            checks=checks,
            errors=errors,
        )["chapter_status"])

    def test_markdown_format_preflight_checks_only_bytes_and_footnote_tail(self):
        api = _module(self)
        footnote = b"[^CLAIM-" + b"a" * 64 + b"]: definition\n"
        valid = b"# Chapter\n" + footnote
        cases = {
            "invalid UTF-8": (b"\xff\n", ("MARKDOWN_FORMAT_INVALID",)),
            "BOM": (b"\xef\xbb\xbf# Chapter\n", ("MARKDOWN_FORMAT_INVALID",)),
            "CR": (b"# Chapter\r\n", ("MARKDOWN_FORMAT_INVALID",)),
            "missing final LF": (b"# Chapter", ("MARKDOWN_FORMAT_INVALID",)),
            "tab": (b"#\tChapter\n", ("MARKDOWN_FORMAT_INVALID",)),
            "literal backslash-n tail": (valid + b"\\n\n", ("FOOTNOTE_INVALID",)),
        }

        with (
            patch.object(api, "_capture_bundle", side_effect=AssertionError("authentication must not run")) as capture,
            patch.object(api, "_read_snapshot_source", side_effect=AssertionError("source reads must not run")) as source_read,
        ):
            self.assertEqual((), api.markdown_format_preflight(valid))
            for label, (raw, expected) in cases.items():
                with self.subTest(case=label):
                    self.assertEqual(expected, api.markdown_format_preflight(raw))
        capture.assert_not_called()
        source_read.assert_not_called()

    def test_preflight_cli_needs_only_markdown_and_emits_fixed_diagnostics(self):
        api = _module(self)
        workflow = importlib.import_module("chapter_workflow")
        footnote = b"[^CLAIM-" + b"a" * 64 + b"]: definition\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            valid_path = root / "valid.md"
            valid_path.write_bytes(b"# Chapter\n" + footnote)
            bad_path = root / "bad.md"
            bad_path.write_bytes(b"# Chapter\xff\n")

            with (
                patch.object(api, "_capture_bundle", side_effect=AssertionError("authentication must not run")) as capture,
                patch.object(api, "_read_snapshot_source", side_effect=AssertionError("source reads must not run")) as source_read,
            ):
                output = io.StringIO()
                with patch("sys.stdout", output):
                    result = workflow.main(["preflight", "--markdown", str(valid_path)])
                self.assertEqual(0, result)
                self.assertEqual("format_preflight=PASS authentication=NOT_RUN errors=NONE\n", output.getvalue())

                output = io.StringIO()
                with patch("sys.stdout", output):
                    result = workflow.main(["preflight", "--markdown", str(bad_path)])
                self.assertEqual(1, result)
                self.assertEqual(
                    "format_preflight=FAIL authentication=NOT_RUN errors=MARKDOWN_FORMAT_INVALID\n",
                    output.getvalue(),
                )

                output = io.StringIO()
                with patch("sys.stdout", output):
                    result = workflow.main(["preflight", "--markdown", str(root)])
                self.assertEqual(2, result)
                self.assertEqual("format_preflight=FAIL authentication=NOT_RUN errors=INPUT_INVALID\n", output.getvalue())

            capture.assert_not_called()
            source_read.assert_not_called()

    def test_v2_requires_bounded_authenticated_e1_excerpt_inside_source(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        _add_v2_excerpt_evidence(context, facts)
        valid = _valid_v2_chapter(facts, context["claim"]["text"], inputs)
        excerpt = _v2_excerpt(api, facts, inputs)
        caption, _marker, *_rest = excerpt.splitlines()
        range_start = int(caption.rsplit(":", 1)[1].split("-", 1)[0])
        range_end = int(caption.rsplit("-", 1)[1])
        source_location_block = "\n" + excerpt
        cases = {
            "missing excerpt": valid.replace(source_location_block, "", 1),
            "tampered excerpt": valid.replace(excerpt.splitlines()[3], "tampered source line", 1),
            "caption range mismatch": valid.replace(
                caption,
                caption.replace(f":{range_start}-{range_end}", f":{range_start + 1}-{range_end + 1}"),
                1,
            ),
            "unsafe path": valid.replace(excerpt.splitlines()[1], excerpt.splitlines()[1].replace("path=", "path=..%2F", 1), 1),
            "excerpt outside Source": valid.replace(
                "## Source\n" + excerpt + "\n\n## Mechanism\n",
                "## Source\n\n## Mechanism\n" + excerpt + "\n\n",
                1,
            ),
        }
        for label, markdown in cases.items():
            with self.subTest(excerpt=label):
                bank, _checks, errors = api._verify_markdown(markdown.encode("utf-8"), facts, inputs)
                self.assertIsNone(bank)
                self.assertTrue(errors)

        non_e1_facts = copy.deepcopy(facts)
        evidence_id = excerpt.splitlines()[1].split("evidence=", 1)[1].split(" ", 1)[0]
        next(item for item in non_e1_facts["evidence"] if item["id"] == evidence_id)["level"] = "E2"
        bank, _checks, errors = api._verify_markdown(valid.encode("utf-8"), non_e1_facts, inputs)
        self.assertIsNone(bank)
        self.assertIn("EXCERPT_INVALID", errors)

        locator_facts = copy.deepcopy(facts)
        evidence = next(item for item in locator_facts["evidence"] if item["id"] == evidence_id)
        evidence["locator"]["line_end"] = range_start - 1
        bank, _checks, errors = api._verify_markdown(valid.encode("utf-8"), locator_facts, inputs)
        self.assertIsNone(bank)
        self.assertIn("EXCERPT_INVALID", errors)

        long_facts = copy.deepcopy(facts)
        long_item = next(item for item in long_facts["evidence"] if item["id"] == evidence_id)
        long_item["locator"]["line_start"] = 1
        long_item["locator"]["line_end"] = 21
        long_source = b"source line\n" * 21
        long_body = long_source.decode("utf-8")
        long_path = api.quote(long_item["locator"]["path"], safe="/-._~")
        fence = chr(96) * 3
        long_excerpt = (
            f"Source location: {long_item['locator']['path']}:1-21\n"
            f"<!-- SOURCE-EXCERPT evidence={evidence_id} path={long_path} lines=1-21 "
            f"sha256={hashlib.sha256(long_source).hexdigest()} -->\n"
            f"{fence}text\n{long_body}{fence}\n<!-- /SOURCE-EXCERPT -->"
        )
        long_markdown = _valid_v2_chapter_with_excerpt(api, long_facts, context["claim"]["text"], long_excerpt)
        with patch.object(api, "_read_snapshot_source", return_value=long_source):
            bank, _checks, errors = api._verify_markdown(long_markdown.encode("utf-8"), long_facts, inputs)
        self.assertIsNone(bank)
        self.assertIn("EXCERPT_INVALID", errors)

    def test_v2_source_headings_are_not_chapter_structure_and_eof_lf_is_framing(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        _add_v2_excerpt_evidence(context, facts)
        evidence = next(item for item in facts["evidence"] if item["level"] == "E1")
        evidence["locator"]["line_start"] = 1
        evidence["locator"]["line_end"] = 2
        source = b"# source comment\n## Example"
        path = evidence["locator"]["path"]
        encoded_path = api.quote(path, safe="/-._~")
        fence = chr(96) * 3
        excerpt = (
            f"Source location: {path}:1-2\n"
            f"<!-- SOURCE-EXCERPT evidence={evidence['id']} path={encoded_path} "
            f"lines=1-2 sha256={hashlib.sha256(source).hexdigest()} -->\n"
            f"{fence}text\n{source.decode('utf-8')}\n{fence}\n<!-- /SOURCE-EXCERPT -->"
        )
        markdown = _valid_v2_chapter_with_excerpt(api, facts, context["claim"]["text"], excerpt)
        raw = markdown.encode("utf-8")

        with patch.object(api, "_read_snapshot_source", return_value=source):
            bank, _checks, errors = api._verify_markdown(raw, facts, inputs)

        self.assertEqual([], errors)
        self.assertIsNotNone(bank)
        tampered = raw.replace(b"## Example", b"## Changed")
        self.assertEqual((), api.markdown_format_preflight(tampered))
        with patch.object(api, "_read_snapshot_source", return_value=source):
            tampered_bank, _checks, tampered_errors = api._verify_markdown(tampered, facts, inputs)
        self.assertIsNone(tampered_bank)
        self.assertIn("EXCERPT_INVALID", tampered_errors)

        review = importlib.import_module("phase6_review")
        occurrences = review.expected_occurrences(SimpleNamespace(
            facts=facts,
            chapter_raw=raw,
            chapter_sha256=hashlib.sha256(raw).hexdigest(),
        ))
        marker = f"[^CLAIM-{context['claim']['id'].removeprefix('CLAIM-')}]"
        expected_lines = [
            "A request can cross several layers. This chapter follows one selected source unit.",
            "A repository snapshot is a fixed view of files. A locator names one bounded range。",
            f"{context['claim']['text']} {marker}",
            f"- {context['claim']['text']} {marker}",
            "Each function can be read as a small input-to-output transformation！",
            "A focused test changes one input at a time. Its result can be compared with the expected behavior?",
            "A hypothetical change can be checked with a focused test. A failure narrows where the change broke.",
        ]
        physical_lines = markdown.splitlines()
        self.assertEqual(
            [physical_lines.index(line) + 1 for line in expected_lines],
            [row["line_number"] for row in occurrences],
        )

    def test_v2_rejects_malformed_markers_raw_html_arbitrary_fences_and_unrecognized_lines(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        _add_v2_excerpt_evidence(context, facts)
        valid = _valid_v2_chapter(facts, context["claim"]["text"], inputs)
        cases = {
            "misplaced claim marker": valid.replace(". [^CLAIM-", " [^CLAIM-", 1),
            "unsupported marker": valid.replace("[^CLAIM-", "[^CLAIM-" + "f" * 64 + "] [^CLAIM-", 1),
            "raw html": valid.replace("A request can cross several layers.", "<span>A request can cross several layers.</span>"),
            "raw html in exercise": valid.replace(
                "Question: Which source-backed statement appears in this chapter?",
                "Question: Which <span>source-backed</span> statement appears in this chapter?",
                1,
            ),
            "arbitrary fence": valid.replace("A request can cross several layers.", "```text\nA request can cross several layers.\n```"),
            "unknown line": valid.replace(
                "A hypothetical change can be checked with a focused test.",
                "not a prose line\nA hypothetical change can be checked with a focused test.",
                1,
            ),
            "misplaced format marker": valid.replace("## Intuition", "<!-- project-deepdive-chapter-format: v2 -->\n\n## Intuition"),
        }
        for label, markdown in cases.items():
            with self.subTest(grammar=label):
                bank, _checks, errors = api._verify_markdown(markdown.encode("utf-8"), facts, inputs)
                self.assertIsNone(bank)
                self.assertTrue(errors)

    def test_repeated_claim_still_requires_one_well_formed_definition(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        marker = f"[^CLAIM-{context['claim']['id'].removeprefix('CLAIM-')}]"
        repeated_claim = f"Claim: {context['claim']['text']} {marker}"
        markdown = _valid_chapter(facts, context["claim"]["text"]).replace(
            "> Evidence gap — NOT ESTABLISHED IN THIS SLICE", repeated_claim, 1,
        )
        footnote = next(line for line in markdown.splitlines() if line.startswith(marker + ":"))
        malformed = marker + " missing colon"
        cases = {
            "missing definition": markdown.replace(footnote + "\n", "", 1),
            "malformed definition": markdown.replace(footnote, footnote + "\n" + malformed, 1),
            "duplicate definition": markdown.replace(footnote, footnote + "\n" + footnote, 1),
        }

        for label, candidate in cases.items():
            with self.subTest(definition=label):
                bank, _checks, errors = api._verify_markdown(candidate.encode("utf-8"), facts, inputs)
                self.assertIsNone(bank)
                self.assertIn("FOOTNOTE_INVALID", errors)

    def test_structural_fail_publishes_status_only_and_keeps_writer_draft(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        malformed = _valid_chapter(facts, context["claim"]["text"]).replace("## Mechanism", "## Source")
        markdown_path = output_dir / "chapter.md"
        markdown_path.write_text(malformed, encoding="utf-8", newline="\n")
        original_digest = hashlib.sha256(markdown_path.read_bytes()).hexdigest()

        bank, status = api.verify_chapter_draft(
            inputs,
            unit_id=unit_id,
            facts_path=output_dir / "chapter-facts.json",
            markdown_path=markdown_path,
            out_dir=output_dir,
        )

        self.assertEqual({}, bank)
        self.assertEqual("FAIL", status["structural_status"])
        self.assertIn("HEADING_ORDER_INVALID", status["errors"])
        self.assertFalse((output_dir / "exercise-bank.json").exists())
        self.assertEqual(original_digest, hashlib.sha256(markdown_path.read_bytes()).hexdigest())
        self.assertTrue((output_dir / "chapter-draft-status.json").is_file())
        self.assertNotIn("exercise_bank_sha256", status)

    def test_unknown_claim_footnote_is_structural_fail_not_provenance_failure(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        markdown = _valid_chapter(facts, context["claim"]["text"])
        markdown += "[^CLAIM-" + "0" * 64 + "]: {\"evidence\":[]}\n"
        markdown_path = output_dir / "chapter.md"
        markdown_path.write_text(markdown, encoding="utf-8", newline="\n")

        bank, status = api.verify_chapter_draft(
            inputs,
            unit_id=unit_id,
            facts_path=output_dir / "chapter-facts.json",
            markdown_path=markdown_path,
            out_dir=output_dir,
        )

        self.assertEqual({}, bank)
        self.assertEqual("FAIL", status["structural_status"])
        self.assertEqual("FAIL", status["check_results"]["reference_integrity"])
        self.assertIn("FOOTNOTE_INVALID", status["errors"])
        self.assertFalse((output_dir / "exercise-bank.json").exists())
        self.assertTrue((output_dir / "chapter-draft-status.json").is_file())

    def test_dangling_exercise_evidence_reference_fails_reference_integrity(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        claim_id = context["claim"]["id"]
        evidence_id = context["claim"]["evidence_ids"][0]
        valid = _valid_chapter(facts, context["claim"]["text"])
        dangling_excerpt = (
            "<!-- SOURCE-EXCERPT evidence=EVID-UNKNOWN path=src%2Fmissing.py lines=1-1 sha256="
            + "0" * 64
            + " -->\n```text\nplaceholder\n```\n<!-- /SOURCE-EXCERPT -->\n"
        )
        cases = {
            "exercise evidence": valid.replace(f"evidence={evidence_id}", "evidence=EVID-UNKNOWN"),
            "claim": valid.replace(claim_id, "CLAIM-" + "f" * 64),
            "excerpt evidence": valid.replace("## Source\n", "## Source\n\n" + dangling_excerpt, 1),
        }
        for label, markdown in cases.items():
            with self.subTest(reference=label):
                bank, checks, errors = api._verify_markdown(markdown.encode("utf-8"), facts, inputs)
                self.assertIsNone(bank)
                self.assertEqual("FAIL", checks.get("reference_integrity"))
                self.assertTrue(errors)

    def test_status_publish_race_rolls_back_owned_bank_without_deleting_status_owner(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        markdown_path = output_dir / "chapter.md"
        markdown_path.write_text(_valid_chapter(facts, context["claim"]["text"]), encoding="utf-8", newline="\n")
        status_path = output_dir / "chapter-draft-status.json"
        bank_path = output_dir / "exercise-bank.json"
        foreign_bank_path = output_dir.parent / "concurrent-bank-owner.tmp"
        self.assertFalse(foreign_bank_path.exists())
        foreign_bank = b"concurrent exercise-bank owner"
        foreign_bank_path.write_bytes(foreign_bank)
        foreign_bank_identity = foreign_bank_path.stat()
        concurrent_status = b"concurrent status owner"
        real_link = api.os.link

        def race_status_publish(source, destination):
            if Path(destination) == status_path:
                foreign_bank_path.replace(bank_path)
                status_path.write_bytes(concurrent_status)
                raise FileExistsError("simulated concurrent status owner")
            return real_link(source, destination)

        with patch.object(api.os, "link", side_effect=race_status_publish):
            with self.assertRaises(api.Phase6ChapterError) as raised:
                api.verify_chapter_draft(
                    inputs,
                    unit_id=unit_id,
                    facts_path=output_dir / "chapter-facts.json",
                    markdown_path=markdown_path,
                    out_dir=output_dir,
                )

        self.assertEqual("OUTPUT_EXISTS", raised.exception.code)
        self.assertEqual(foreign_bank, bank_path.read_bytes())
        bank_identity = bank_path.stat()
        self.assertEqual((foreign_bank_identity.st_dev, foreign_bank_identity.st_ino), (bank_identity.st_dev, bank_identity.st_ino))
        self.assertEqual(concurrent_status, status_path.read_bytes())

    def test_status_publish_failure_removes_owned_bank_and_publishes_nothing(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        markdown_path = output_dir / "chapter.md"
        markdown_path.write_text(_valid_chapter(facts, context["claim"]["text"]), encoding="utf-8", newline="\n")
        status_path = output_dir / "chapter-draft-status.json"
        bank_path = output_dir / "exercise-bank.json"
        real_link = api.os.link

        def fail_status_publish(source, destination):
            if Path(destination) == status_path:
                raise OSError("simulated status publication failure")
            return real_link(source, destination)

        with patch.object(api.os, "link", side_effect=fail_status_publish):
            with self.assertRaises(api.Phase6ChapterError) as raised:
                api.verify_chapter_draft(
                    inputs,
                    unit_id=unit_id,
                    facts_path=output_dir / "chapter-facts.json",
                    markdown_path=markdown_path,
                    out_dir=output_dir,
                )

        self.assertEqual("OUTPUT_FAILED", raised.exception.code)
        self.assertFalse(bank_path.exists())
        self.assertFalse(status_path.exists())

    def test_snapshot_excerpt_rejects_authenticated_index_size_over_limit(self):
        api = _module(self)
        _context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        path = facts["evidence"][0]["locator"]["path"]
        index_path = inputs.run_dir / "phase2" / "project-index.json"
        index = copy.deepcopy(api._read_artifact(index_path).artifact)
        indexed_file = next(row for row in index["files"] if row["path"] == path)
        self.assertEqual("text", indexed_file["content_kind"])
        self.assertLessEqual(indexed_file["bytes"], 8 * 1024 * 1024)
        raw = api._read_snapshot_source(path, facts=facts, run_dir=inputs.run_dir, root=inputs.root)
        self.assertEqual(indexed_file["bytes"], len(raw))
        self.assertEqual(indexed_file["sha256"], hashlib.sha256(raw).hexdigest())

        for field, value in (("bytes", 8 * 1024 * 1024 + 1), ("bytes", indexed_file["bytes"] + 1), ("content_kind", "binary")):
            with self.subTest(field=field, value=value):
                altered_index = copy.deepcopy(index)
                altered_index["files"] = [
                    {**row, field: value} if row["path"] == path else row
                    for row in index["files"]
                ]
                with patch.object(api, "_read_artifact", return_value=SimpleNamespace(artifact=altered_index)):
                    with self.assertRaises(api.Phase6ChapterError) as raised:
                        api._read_snapshot_source(
                            path,
                            facts=facts,
                            run_dir=inputs.run_dir,
                            root=inputs.root,
                        )
                self.assertEqual("EXCERPT_INVALID", raised.exception.code)

    def test_git_snapshot_excerpt_reader_stops_after_bounded_output(self):
        api = _module(self)

        class FakeProcess:
            def __init__(self):
                self.stdout = io.BytesIO(b"012345678")
                self.killed = False
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.killed = True

            def poll(self):
                return self.returncode

        process = FakeProcess()
        with (
            patch.object(api, "MAX_EXCERPT_SOURCE_BYTES", 8),
            patch.object(api.subprocess, "Popen", return_value=process),
        ):
            with self.assertRaises(api.Phase6ChapterError) as raised:
                api._read_git_object_bounded(Path("."), "a" * 40, "src/example.py")

        self.assertEqual("EXCERPT_INVALID", raised.exception.code)
        self.assertTrue(process.killed)

    def test_provenance_change_during_verify_publishes_no_verifier_artifacts(self):
        api = _module(self)
        context, inputs, unit_id, output_dir = _phase6_fixture(self)
        facts, _packet = api.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=output_dir)
        markdown_path = output_dir / "chapter.md"
        markdown_path.write_text(_valid_chapter(facts, context["claim"]["text"]), encoding="utf-8", newline="\n")
        context["report_path"].write_bytes(context["report_path"].read_bytes() + b" ")

        with self.assertRaises(api.Phase6ChapterError) as raised:
            api.verify_chapter_draft(
                inputs,
                unit_id=unit_id,
                facts_path=output_dir / "chapter-facts.json",
                markdown_path=markdown_path,
                out_dir=output_dir,
            )

        self.assertEqual("PROVENANCE_MISMATCH", raised.exception.code)
        self.assertFalse((output_dir / "exercise-bank.json").exists())
        self.assertFalse((output_dir / "chapter-draft-status.json").exists())


if __name__ == "__main__":
    unittest.main()

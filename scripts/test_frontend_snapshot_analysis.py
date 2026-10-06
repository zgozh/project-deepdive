#!/usr/bin/env python3
"""Focused contract tests for Phase 3D2A frontend snapshot artifacts."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from artifact_contract import dumps_artifact  # noqa: E402
from repository_scan import ScanOptions, scan_repository  # noqa: E402
from stack_detection import build_stack_artifacts  # noqa: E402
from test_repository_scan import init_git_repo  # noqa: E402


FIXED_TIME = "2026-09-25T00:00:00Z"


def _prepare(work: Path, sources: dict[str, bytes], *, snapshot="worktree"):
    root = work / "repo"
    init_git_repo(root, sources)
    scanned = scan_repository(ScanOptions(
        root=root,
        snapshot_kind=snapshot,
        generated_at=FIXED_TIME,
    ))
    project_index = copy.deepcopy(scanned.project_index)
    coverage = copy.deepcopy(scanned.coverage)
    source_by_path = dict(sources)
    unknown_files = coverage["unknown_count"]
    g01_status = "PARTIAL" if unknown_files else "PASS"
    stack_profile, evidence = build_stack_artifacts(
        project_index,
        coverage,
        lambda path, _entry: source_by_path[path],
        generated_at=FIXED_TIME,
        regular_source_paths=frozenset(source_by_path),
        g01_status=g01_status,
        unknown_files=unknown_files,
    )
    return root, project_index, coverage, stack_profile, evidence, source_by_path


def _api(testcase):
    try:
        return importlib.import_module("frontend_snapshot_analysis")
    except ImportError as exc:
        testcase.fail(f"frontend snapshot artifact builder is not implemented: {exc}")


def _analyze(
    api, root, project_index, coverage, stack_profile, evidence, sources,
    *, analysis_version="1.4.0",
):
    unknown_files = coverage["unknown_count"]
    g01_status = "PARTIAL" if unknown_files else "PASS"
    return api.analyze_frontend_artifacts(
        project_index,
        coverage,
        stack_profile,
        evidence,
        lambda path, _entry: sources[path],
        frozenset(sources),
        g01_status=g01_status,
        unknown_files=unknown_files,
        analysis_version=analysis_version,
    )


class FrontendSnapshotAnalysisTests(unittest.TestCase):
    def test_v15_emits_only_source_bound_next_zustand_and_direct_api_roles(self):
        sources = {
            "frontend/package.json": (
                b'{"name":"PACKAGE_MANIFEST_CANARY","dependencies":{"next":"15.0.0","zustand":"4.5.5"}}\n'
            ),
            "frontend/src/app/page.tsx": (
                b"export function Page() { return <main />; }\n"
            ),
            "frontend/src/pages/about.tsx": (
                b"export default function About() { return <main />; }\n"
            ),
            "frontend/src/app/unexported/page.tsx": (
                b"function PrivatePage() { return <main />; }\n"
            ),
            "frontend/src/components/Settings.tsx": (
                b"export function Settings() { return <main />; }\n"
            ),
            "frontend/src/stores/use-app-store.ts": (
                b'import { create as createStore } from "zustand";\n'
                b"export const useAppStore = createStore(() => ({ count: 0 }));\n"
            ),
            "frontend/src/stores/shadowed.ts": (
                b'import { create } from "zustand";\n'
                b"function local(create: any) { return create(() => ({})); }\n"
                b"export const useShadowedStore = create(() => ({}));\n"
            ),
            "frontend/src/store-lookalike.ts": (
                b"export const useFilenameOnlyStore = create(() => ({}));\n"
            ),
            "frontend/src/core/use-remote-data.ts": (
                b"export function useRemoteData() {\n"
                b'  return fetch("https://ROLE_URL_CANARY", { body: "ROLE_BODY_CANARY" });\n'
                b"}\n"
            ),
            "frontend/src/core/transport.ts": (
                b'fetch(dynamicUrl("MODULE_URL_CANARY"));\n'
            ),
            "frontend/src/core/request-wrapper.ts": (
                b'function load(client: any) { return client.get("WRAPPER_URL_CANARY"); }\n'
            ),
            "frontend/vite/package.json": (
                b'{"dependencies":{"react":"18.0.0"},"devDependencies":{"vite":"5.0.0"}}\n'
            ),
            "frontend/vite/src/pages/login.tsx": (
                b"export function Login() { return <main />; }\n"
            ),
        }
        with tempfile.TemporaryDirectory(prefix="pd-frontend-v15-") as temporary:
            root, index, coverage, profile, evidence, source_bytes = _prepare(
                Path(temporary), sources,
            )
            api = _api(self)
            analysis, merged = _analyze(
                api, root, index, coverage, profile, evidence, source_bytes,
                analysis_version="1.5.0",
            )

        self.assertEqual("1.5.0", analysis["schema_version"])
        roles = analysis["roles"]
        by_path_and_kind = {
            (role["path"], role["kind"]): role for role in roles
        }
        self.assertIn(
            ("frontend/src/app/page.tsx", "page_candidate"),
            by_path_and_kind,
        )
        self.assertIn(
            ("frontend/src/pages/about.tsx", "page_candidate"),
            by_path_and_kind,
        )
        self.assertNotIn(
            ("frontend/src/app/unexported/page.tsx", "page_candidate"),
            by_path_and_kind,
        )
        self.assertNotIn(
            ("frontend/src/components/Settings.tsx", "page_candidate"),
            by_path_and_kind,
        )
        self.assertNotIn(
            ("frontend/vite/src/pages/login.tsx", "page_candidate"),
            by_path_and_kind,
        )
        self.assertIn(
            ("frontend/src/stores/use-app-store.ts", "store_candidate"),
            by_path_and_kind,
        )
        store_symbol_id = by_path_and_kind[(
            "frontend/src/stores/use-app-store.ts", "store_candidate",
        )]["symbol_id"]
        store_symbol = next(symbol for symbol in analysis["symbols"] if symbol["id"] == store_symbol_id)
        self.assertTrue(any(
            relation["kind"] == "EXPORTS"
            and relation["path"] == store_symbol["path"]
            and relation["line_start"] == store_symbol["line_start"]
            for relation in analysis["relations"]
        ))
        self.assertNotIn(
            ("frontend/src/stores/shadowed.ts", "store_candidate"),
            by_path_and_kind,
        )
        self.assertNotIn(
            ("frontend/src/store-lookalike.ts", "store_candidate"),
            by_path_and_kind,
        )

        api_roles = [role for role in roles if role["kind"] == "api_client_candidate"]
        functions = {
            (symbol["path"], symbol["name"]): symbol
            for symbol in analysis["symbols"]
            if symbol["kind"] == "function"
        }
        use_remote = functions[("frontend/src/core/use-remote-data.ts", "useRemoteData")]
        self.assertTrue(any(role["symbol_id"] == use_remote["id"] for role in api_roles))
        self.assertTrue(any(
            role["symbol_id"] == use_remote["id"] and role["kind"] == "hook_candidate"
            for role in roles
        ))
        module_symbols = {
            symbol["path"]: symbol
            for symbol in analysis["symbols"] if symbol["kind"] == "module"
        }
        self.assertTrue(any(
            role["symbol_id"] == module_symbols["frontend/src/core/transport.ts"]["id"]
            for role in api_roles
        ))
        self.assertFalse(any(
            role["path"] == "frontend/src/core/request-wrapper.ts"
            for role in api_roles
        ))
        self.assertTrue(all(role["certainty"] == "CANDIDATE" for role in roles))

        merged_by_id = {item["id"]: item for item in merged["items"]}
        for role in roles:
            citation = merged_by_id[role["evidence_ids"][0]]
            self.assertEqual("E1", citation["level"])
            self.assertEqual(role["path"], citation["locator"]["path"])
            self.assertEqual(role["line_start"], citation["locator"]["line_start"])
            self.assertEqual(role["line_end"], citation["locator"]["line_end"])
        serialized = json.dumps({"analysis": analysis, "evidence": merged}, sort_keys=True)
        self.assertNotIn("ROLE_URL_CANARY", serialized)
        self.assertNotIn("ROLE_BODY_CANARY", serialized)
        self.assertNotIn("MODULE_URL_CANARY", serialized)
        self.assertNotIn("WRAPPER_URL_CANARY", serialized)
        self.assertNotIn("PACKAGE_MANIFEST_CANARY", serialized)

        drifted_sources = dict(source_bytes)
        drifted_sources["frontend/package.json"] = (
            b'{"dependencies":{"next":"15.0.0"}}\n'
        )
        with self.assertRaises(api.FrontendSnapshotAnalysisError) as drifted:
            _analyze(
                api, root, index, coverage, profile, evidence, drifted_sources,
                analysis_version="1.5.0",
            )
        self.assertEqual("SOURCE_DRIFT", drifted.exception.code)

        tampered = copy.deepcopy(merged)
        role_evidence = next(
            item for item in tampered["items"]
            if item["id"] == by_path_and_kind[(
                "frontend/src/stores/use-app-store.ts", "store_candidate",
            )]["evidence_ids"][0]
        )
        role_evidence["locator"]["observation"] = "revision=wrong; sha256=" + "0" * 64
        with self.assertRaises(api.FrontendSnapshotAnalysisError) as raised:
            api.validate_frontend_analysis_bundle(
                index,
                coverage,
                profile,
                evidence,
                tampered,
                analysis,
                g01_status="PARTIAL" if coverage["unknown_count"] else "PASS",
                unknown_files=coverage["unknown_count"],
                regular_source_paths=frozenset(source_bytes),
                source_reader=lambda path, _entry: source_bytes[path],
            )
        self.assertEqual("EVIDENCE_LOCATOR_MISMATCH", raised.exception.code)

    def test_v15_leaves_page_unresolved_without_same_snapshot_next_marker(self):
        sources = {
            "frontend/src/pages/login.tsx": (
                b"export function Login() { return <main />; }\n"
            ),
        }
        with tempfile.TemporaryDirectory(prefix="pd-frontend-v15-no-next-") as temporary:
            root, index, coverage, profile, evidence, source_bytes = _prepare(
                Path(temporary), sources,
            )
            api = _api(self)
            analysis, _merged = _analyze(
                api, root, index, coverage, profile, evidence, source_bytes,
                analysis_version="1.5.0",
            )

        self.assertTrue(any(
            role["kind"] == "component_candidate"
            and role["path"] == "frontend/src/pages/login.tsx"
            for role in analysis["roles"]
        ))
        self.assertFalse(any(
            role["kind"] == "page_candidate"
            and role["path"] == "frontend/src/pages/login.tsx"
            for role in analysis["roles"]
        ))

    def test_parser_batches_respect_file_count_and_total_source_bytes(self):
        api = _api(self)
        many_paths = [f"frontend/src/{index}.ts" for index in range(33)]
        tiny_files = {path: {"bytes": 1} for path in many_paths}
        many_batches = list(api._batches(many_paths, tiny_files))
        self.assertEqual([32, 1], [len(batch) for batch in many_batches])
        self.assertTrue(all(len(batch) <= 32 for batch in many_batches))

        large_paths = [f"frontend/src/large-{index}.ts" for index in range(9)]
        large_files = {
            path: {"bytes": api.MAX_FILE_BYTES}
            for path in large_paths
        }
        large_batches = list(api._batches(large_paths, large_files))
        self.assertEqual([8, 1], [len(batch) for batch in large_batches])
        self.assertTrue(all(
            sum(large_files[path]["bytes"] for path in batch) <= api.MAX_TOTAL_BYTES
            for batch in large_batches
        ))

    def test_bundle_maps_only_supported_syntax_and_binds_source_evidence(self):
        source = (
            'import api from "MODULE_CANARY";\n'
            "export function useItems() { return null; }\n"
            "export const Dashboard = () => <main>BODY_CANARY</main>;\n"
            "export { Dashboard };\n"
            "class ApiClient {\n"
            "  load() {\n"
            '    return globalThis.fetch("https://URL_CANARY", { body: "ARG_CANARY" });\n'
            "  }\n"
            "}\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="pd-frontend-core-") as temporary:
            root, index, coverage, profile, evidence, sources = _prepare(
                Path(temporary), {"frontend/src/Dashboard.tsx": source},
            )
            api = _api(self)
            analysis, merged = _analyze(api, root, index, coverage, profile, evidence, sources)
            second, second_merged = _analyze(api, root, index, coverage, profile, evidence, sources)

        self.assertEqual(analysis, second)
        self.assertEqual(merged, second_merged)
        self.assertEqual("1.4.0", analysis["schema_version"])
        self.assertEqual("PASS", analysis["analysis_status"])
        self.assertEqual(profile["source_metadata"], analysis["source_metadata"])
        self.assertEqual(
            hashlib.sha256(dumps_artifact(profile).encode("utf-8")).hexdigest(),
            analysis["stack_profile_sha256"],
        )
        file_record = analysis["languages"][0]["files"][0]
        self.assertEqual("frontend/src/Dashboard.tsx", file_record["path"])
        self.assertEqual(hashlib.sha256(source).hexdigest(), file_record["sha256"])
        self.assertEqual("ANALYZED", file_record["status"])

        symbols = analysis["symbols"]
        modules = [row for row in symbols if row["kind"] == "module"]
        classes = [row for row in symbols if row["kind"] == "class"]
        methods = [row for row in symbols if row["kind"] == "method"]
        functions = [row for row in symbols if row["kind"] == "function"]
        self.assertEqual(1, len(modules))
        self.assertEqual(1, len(classes))
        self.assertEqual("ApiClient", classes[0]["name"])
        self.assertEqual(["load"], [row["name"] for row in methods])
        by_name = {row["name"]: row for row in functions}
        self.assertIn("Dashboard", by_name)
        self.assertIn("useItems", by_name)

        relations = analysis["relations"]
        self.assertTrue(any(
            row["kind"] == "DEFINES"
            and row["source_id"] == modules[0]["id"]
            and row["target_id"] == classes[0]["id"]
            for row in relations
        ))
        self.assertTrue(any(
            row["kind"] == "DEFINES"
            and row["source_id"] == classes[0]["id"]
            and row["target_id"] == methods[0]["id"]
            for row in relations
        ))
        api_relation = next(row for row in relations if row["kind"] == "USES_API")
        self.assertEqual(methods[0]["id"], api_relation["source_id"])
        self.assertIsNone(api_relation["target_id"])
        self.assertEqual("frontend_api_target_unresolved", api_relation["unresolved_target"])
        self.assertEqual("CANDIDATE", api_relation["certainty"])
        self.assertTrue(any(row["kind"] == "IMPORTS" for row in relations))
        self.assertTrue(any(row["kind"] == "EXPORTS" for row in relations))

        roles = analysis["roles"]
        self.assertEqual(
            {"component_candidate", "hook_candidate"},
            {row["kind"] for row in roles},
        )
        self.assertEqual(by_name["Dashboard"]["id"], next(
            row["symbol_id"] for row in roles if row["kind"] == "component_candidate"
        ))
        self.assertEqual(by_name["useItems"]["id"], next(
            row["symbol_id"] for row in roles if row["kind"] == "hook_candidate"
        ))

        evidence_by_id = {row["id"]: row for row in merged["items"]}
        self.assertEqual(len(merged["items"]), len(evidence_by_id))
        for fact in symbols + relations + roles:
            citation = evidence_by_id[fact["evidence_ids"][0]]
            self.assertEqual("E1", citation["level"])
            self.assertEqual(fact["path"], citation["locator"]["path"])
            self.assertGreaterEqual(citation["locator"]["line_start"], 1)
            self.assertEqual(
                analysis["repository_revision"],
                citation["locator"]["observation"].split(";", 1)[0].removeprefix("revision="),
            )
            self.assertIn(
                file_record["sha256"],
                citation["locator"]["observation"],
            )

        serialized = json.dumps([analysis, merged], sort_keys=True)
        for canary in ("MODULE_CANARY", "BODY_CANARY", "URL_CANARY", "ARG_CANARY", "https://"):
            self.assertNotIn(canary, serialized)

    def test_relevant_generated_vendor_ignored_binary_and_syntax_error_paths_are_reported(self):
        sources = {
            "frontend/src/Good.tsx": b"export function Good() { return <div />; }\n",
            "frontend/src/Broken.tsx": b"export function Broken( { return 1; }\n",
            "frontend/src/Binary.js": b"\xff\xfe\x00\x01",
            "frontend/dist/output.js": b"function generated() {}\n",
            "frontend/vendor/library.js": b"function vendored() {}\n",
            "frontend/ignored/legacy.js": b"function ignored() {}\n",
        }
        with tempfile.TemporaryDirectory(prefix="pd-frontend-status-") as temporary:
            root, index, coverage, _profile, _evidence, source_by_path = _prepare(
                Path(temporary), sources,
            )
            entries = {row["path"]: row for row in coverage["entries"]}
            for path in ("frontend/dist/output.js", "frontend/vendor/library.js"):
                entries[path]["secondary_surfaces"] = ["frontend"]
            ignored = entries["frontend/ignored/legacy.js"]
            ignored["classification"] = "IGNORED_WITH_REASON"
            ignored["reason"] = "fixture exclusion"
            ignored["rule_id"] = "override:exact-path"
            stack_profile, evidence = build_stack_artifacts(
                index,
                coverage,
                lambda path, _entry: source_by_path[path],
                generated_at=FIXED_TIME,
                regular_source_paths=frozenset(source_by_path),
                g01_status="PASS",
                unknown_files=0,
            )
            api = _api(self)
            analysis, _merged = _analyze(
                api, root, index, coverage, stack_profile, evidence, source_by_path,
            )

        file_records = {
            row["path"]: row
            for language in analysis["languages"]
            for row in language["files"]
        }
        self.assertEqual(set(sources), set(file_records))
        self.assertEqual("ANALYZED", file_records["frontend/src/Good.tsx"]["status"])
        self.assertEqual("PARTIAL", file_records["frontend/src/Broken.tsx"]["status"])
        self.assertEqual("SYNTAX_ERROR", file_records["frontend/src/Broken.tsx"]["limitations"][0]["code"])
        self.assertEqual("SKIPPED", file_records["frontend/src/Binary.js"]["status"])
        self.assertEqual("UNSUPPORTED_ENCODING", file_records["frontend/src/Binary.js"]["limitations"][0]["code"])
        self.assertEqual("SKIPPED", file_records["frontend/dist/output.js"]["status"])
        self.assertEqual("EXCLUDED_CLASSIFICATION", file_records["frontend/dist/output.js"]["limitations"][0]["code"])
        self.assertEqual("SKIPPED", file_records["frontend/vendor/library.js"]["status"])
        self.assertEqual("SKIPPED", file_records["frontend/ignored/legacy.js"]["status"])
        self.assertEqual("PARTIAL", analysis["analysis_status"])
        fact_paths = {
            row["path"]
            for row in analysis["symbols"] + analysis["relations"] + analysis["roles"]
        }
        self.assertNotIn("frontend/src/Broken.tsx", fact_paths)
        self.assertNotIn("frontend/src/Binary.js", fact_paths)
        self.assertNotIn("frontend/dist/output.js", fact_paths)
        self.assertNotIn("frontend/vendor/library.js", fact_paths)
        self.assertNotIn("frontend/ignored/legacy.js", fact_paths)

    def test_input_metadata_g01_and_snapshot_source_bytes_are_enforced(self):
        source = b"export function Stable() {}\n"
        with tempfile.TemporaryDirectory(prefix="pd-frontend-input-") as temporary:
            root, index, coverage, profile, evidence, sources = _prepare(
                Path(temporary), {"frontend/src/Stable.ts": source},
            )
            api = _api(self)
            forged_profile = copy.deepcopy(profile)
            forged_profile["source_metadata"]["coverage_sha256"] = "f" * 64
            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                _analyze(api, root, index, coverage, forged_profile, evidence, sources)

            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                api.analyze_frontend_artifacts(
                    index,
                    coverage,
                    profile,
                    evidence,
                    lambda _path, _entry: source + b"// drift\n",
                    frozenset(sources),
                    g01_status="FAIL",
                    unknown_files=0,
                )

            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                api.analyze_frontend_artifacts(
                    index,
                    coverage,
                    profile,
                    evidence,
                    lambda _path, _entry: source + b"// drift\n",
                    frozenset(sources),
                    g01_status="PASS",
                    unknown_files=0,
                )

    def test_legacy_phase2_without_content_kind_is_reported_not_parsed(self):
        source = b"export function Legacy() { return 1; }\n"
        with tempfile.TemporaryDirectory(prefix="pd-frontend-legacy-index-") as temporary:
            root, index, coverage, _profile, _evidence, sources = _prepare(
                Path(temporary), {"frontend/src/Legacy.ts": source},
            )
            index["schema_version"] = "1.0.0"
            for row in index["files"]:
                for field in ("content_kind", "media_type", "extension", "vcs_object_id"):
                    row.pop(field, None)
            coverage["schema_version"] = "1.0.0"
            for row in coverage["entries"]:
                row.pop("secondary_surfaces", None)
                row.pop("rule_id", None)
            profile, evidence = build_stack_artifacts(
                index,
                coverage,
                lambda path, _entry: sources[path],
                generated_at=FIXED_TIME,
                regular_source_paths=frozenset(sources),
                g01_status="PASS",
                unknown_files=0,
            )
            api = _api(self)
            analysis, _merged = _analyze(api, root, index, coverage, profile, evidence, sources)

        file_record = analysis["languages"][0]["files"][0]
        self.assertEqual("SKIPPED", file_record["status"])
        self.assertEqual("UNSUPPORTED_CONTENT_KIND", file_record["limitations"][0]["code"])
        self.assertEqual([], analysis["symbols"])
        self.assertEqual("PARTIAL", analysis["analysis_status"])

    def test_bundle_validator_rejects_duplicate_fact_ids_and_dangling_evidence(self):
        source = b"export function One() {} export function Two() {} export function useHook() {}\n"
        with tempfile.TemporaryDirectory(prefix="pd-frontend-validator-") as temporary:
            root, index, coverage, profile, evidence, sources = _prepare(
                Path(temporary), {"frontend/src/Two.ts": source},
            )
            api = _api(self)
            analysis, merged = _analyze(api, root, index, coverage, profile, evidence, sources)
            validate = api.validate_frontend_analysis_bundle
            common = {
                "g01_status": "PASS",
                "unknown_files": 0,
                "regular_source_paths": frozenset(sources),
            }

            duplicate = copy.deepcopy(analysis)
            duplicate["symbols"][1]["id"] = duplicate["symbols"][0]["id"]
            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                validate(index, coverage, profile, evidence, merged, duplicate, **common)

            dangling = copy.deepcopy(analysis)
            dangling["symbols"][0]["evidence_ids"] = ["EVID-FRONTEND-missing"]
            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                validate(index, coverage, profile, evidence, merged, dangling, **common)

            dangling_relation = copy.deepcopy(analysis)
            defining = next(row for row in dangling_relation["relations"] if row["kind"] == "DEFINES")
            defining["source_id"] = "SYM-" + "0" * 24
            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                validate(index, coverage, profile, evidence, merged, dangling_relation, **common)

            dangling_role = copy.deepcopy(analysis)
            role = next(row for row in dangling_role["roles"] if row["kind"] == "hook_candidate")
            role["symbol_id"] = "SYM-" + "0" * 24
            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                validate(index, coverage, profile, evidence, merged, dangling_role, **common)

            duplicate_evidence = copy.deepcopy(merged)
            duplicate_evidence["items"].append(copy.deepcopy(duplicate_evidence["items"][-1]))
            with self.assertRaises(api.FrontendSnapshotAnalysisError):
                validate(index, coverage, profile, evidence, duplicate_evidence, analysis, **common)

    def test_bundle_validator_requires_one_module_symbol_for_each_parsed_file(self):
        source = b"export function Parsed() {}\n"
        with tempfile.TemporaryDirectory(prefix="pd-frontend-module-invariant-") as temporary:
            root, index, coverage, profile, evidence, sources = _prepare(
                Path(temporary), {"frontend/src/Parsed.ts": source},
            )
            api = _api(self)
            analysis, merged = _analyze(api, root, index, coverage, profile, evidence, sources)
            validate = api.validate_frontend_analysis_bundle
            common = {
                "g01_status": "PASS",
                "unknown_files": 0,
                "regular_source_paths": frozenset(sources),
            }

            without_module = copy.deepcopy(analysis)
            module = next(row for row in without_module["symbols"] if row["kind"] == "module")
            without_module["symbols"].remove(module)
            without_module_evidence = copy.deepcopy(merged)
            without_module_evidence["items"] = [
                item for item in without_module_evidence["items"]
                if item["id"] != module["evidence_ids"][0]
            ]
            with self.assertRaises(api.FrontendSnapshotAnalysisError) as caught:
                validate(
                    index,
                    coverage,
                    profile,
                    evidence,
                    without_module_evidence,
                    without_module,
                    **common,
                )
            self.assertEqual("MODULE_SYMBOL_COUNT_INVALID", caught.exception.code)

    def test_bundle_validator_rejects_module_symbols_on_skipped_and_syntax_error_files(self):
        sources = {
            "frontend/src/Parsed.ts": b"export function Parsed() {}\n",
            "frontend/src/Broken.tsx": b"export function Broken( {\n",
            "frontend/dist/output.js": b"export function Generated() {}\n",
        }
        with tempfile.TemporaryDirectory(prefix="pd-frontend-module-status-") as temporary:
            root, index, coverage, profile, evidence, source_by_path = _prepare(
                Path(temporary), sources,
            )
            api = _api(self)
            analysis, merged = _analyze(
                api, root, index, coverage, profile, evidence, source_by_path,
            )
            validate = api.validate_frontend_analysis_bundle
            common = {
                "g01_status": "PASS",
                "unknown_files": 0,
                "regular_source_paths": frozenset(source_by_path),
            }
            module = next(row for row in analysis["symbols"] if row["kind"] == "module")

            for path in ("frontend/src/Broken.tsx", "frontend/dist/output.js"):
                with self.subTest(path=path):
                    tampered = copy.deepcopy(analysis)
                    spurious_module = copy.deepcopy(module)
                    spurious_module["id"] = api._stable_id("SYM", "spurious-module", path)
                    spurious_module["path"] = path
                    spurious_module["language"] = "typescript" if path.endswith(".tsx") else "javascript"
                    spurious_module["evidence_ids"] = ["EVID-FRONTEND-spurious"]
                    tampered["symbols"].append(spurious_module)
                    with self.assertRaises(api.FrontendSnapshotAnalysisError) as caught:
                        validate(index, coverage, profile, evidence, merged, tampered, **common)
                    self.assertEqual("MODULE_SYMBOL_COUNT_INVALID", caught.exception.code)

    def test_bundle_validator_requires_fixed_language_limitations(self):
        source = b"export function Parsed() {}\n"
        with tempfile.TemporaryDirectory(prefix="pd-frontend-language-limitations-") as temporary:
            root, index, coverage, profile, evidence, sources = _prepare(
                Path(temporary), {"frontend/src/Parsed.ts": source},
            )
            api = _api(self)
            analysis, merged = _analyze(api, root, index, coverage, profile, evidence, sources)
            without_limitations = copy.deepcopy(analysis)
            without_limitations["languages"][0]["limitations"] = []
            with self.assertRaises(api.FrontendSnapshotAnalysisError) as caught:
                api.validate_frontend_analysis_bundle(
                    index,
                    coverage,
                    profile,
                    evidence,
                    merged,
                    without_limitations,
                    g01_status="PASS",
                    unknown_files=0,
                    regular_source_paths=frozenset(sources),
                )
            self.assertEqual("LANGUAGE_LIMITATIONS_MISMATCH", caught.exception.code)


if __name__ == "__main__":
    unittest.main()

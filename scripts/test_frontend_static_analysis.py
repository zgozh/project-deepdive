from __future__ import annotations

import importlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
CLI = SCRIPT_DIR / "analyze_frontend.py"


class FrontendStaticAnalysisTests(unittest.TestCase):
    def _api(self):
        try:
            return importlib.import_module("frontend_static_analysis")
        except ImportError as exc:
            self.fail(f"frontend parse-only API is not implemented: {exc}")

    def _analyze(self, sources: dict[str, str], *, protocol_version: int = 1):
        api = self._api()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, source in sources.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            return api.analyze_paths(root, list(sources), protocol_version=protocol_version)

    def test_same_line_import_export_and_declarations_have_stable_one_based_locations(self):
        source = (
            '/*🔎*/ import React from "react"; export { Card } from "./Card"; '
            'export function CardView() { return <Card />; } class Store {}\n'
        )
        first = self._analyze({"src/App.tsx": source})
        second = self._analyze({"src/App.tsx": source})
        self.assertEqual(first, second)
        self.assertEqual("6.0.3", first["parser"]["version"])
        file_record = first["files"][0]
        self.assertEqual("ANALYZED", file_record["status"])
        facts = file_record["facts"]
        imports = [fact for fact in facts if fact["kind"] == "IMPORT"]
        exports = [fact for fact in facts if fact["kind"] == "EXPORT"]
        self.assertEqual(1, len(imports))
        self.assertEqual(1, len(exports))
        self.assertEqual(1, imports[0]["location"]["start_line"])
        import_prefix = source[: source.index("import React")]
        self.assertEqual(len(import_prefix.encode("utf-16-le")) // 2 + 1, imports[0]["location"]["start_column"])
        self.assertEqual("SYNTAX", imports[0]["certainty"])
        self.assertEqual(1, exports[0]["location"]["start_line"])
        export_prefix = source[: source.index("export {")]
        self.assertEqual(len(export_prefix.encode("utf-16-le")) // 2 + 1, exports[0]["location"]["start_column"])
        self.assertEqual("SYNTAX", exports[0]["certainty"])
        self.assertNotEqual(imports[0]["id"], exports[0]["id"])
        self.assertIn("CardView", {fact.get("name") for fact in facts})
        self.assertIn("Store", {fact.get("name") for fact in facts})
        self.assertEqual(
            [fact["id"] for fact in facts],
            [fact["id"] for fact in second["files"][0]["facts"]],
        )

    def test_js_ts_jsx_tsx_extensions_select_parser_script_kind(self):
        samples = {
            "a.js": "function plain() {}\n",
            "b.jsx": "const Tile = () => <i />;\n",
            "c.ts": "export function typed(value: number): number { return value; }\n",
            "d.tsx": "export const Panel = (): JSX.Element => <div />;\n",
        }
        report = self._analyze(samples)
        kinds = {item["path"]: item["script_kind"] for item in report["files"]}
        self.assertEqual(
            {"a.js": "JS", "b.jsx": "JSX", "c.ts": "TS", "d.tsx": "TSX"},
            kinds,
        )

    def test_component_hook_and_api_candidates_are_source_backed_without_argument_values(self):
        source = (
            'import axios from "axios";\n'
            'export const Dashboard = () => <section>BODY_CANARY</section>;\n'
            'export function useUsers() {\n'
            '  const endpoint = "https://example.invalid/users?token=URL_CANARY";\n'
            '  return axios.get(endpoint, { headers: { password: "PARAM_CANARY" } });\n'
            '}\n'
            'function helper(client: any) { return client.get("/not-an-api"); }\n'
        )
        report = self._analyze({"src/Dashboard.tsx": source})
        facts = report["files"][0]["facts"]
        kinds = [fact["kind"] for fact in facts]
        self.assertIn("COMPONENT_CANDIDATE", kinds)
        self.assertIn("HOOK_CANDIDATE", kinds)
        component = next(fact for fact in facts if fact["kind"] == "COMPONENT_CANDIDATE")
        hook = next(fact for fact in facts if fact["kind"] == "HOOK_CANDIDATE")
        self.assertEqual("CANDIDATE", component["certainty"])
        self.assertEqual("CANDIDATE", hook["certainty"])
        api_calls = [fact for fact in facts if fact["kind"] == "API_CALL_CANDIDATE"]
        self.assertEqual(1, len(api_calls))
        self.assertEqual("axios.get", api_calls[0]["callee"])
        self.assertEqual("CANDIDATE", api_calls[0]["certainty"])
        self.assertEqual("UNRESOLVED", api_calls[0]["target_state"])
        self.assertNotIn("client.get", [fact.get("callee") for fact in api_calls])
        serialized = json.dumps(report, sort_keys=True)
        for secret in ("BODY_CANARY", "URL_CANARY", "PARAM_CANARY", "https://", "endpoint"):
            self.assertNotIn(secret, serialized)

    def test_dynamic_import_and_global_fetch_keep_targets_unresolved_and_values_redacted(self):
        source = (
            "const SECRET_MODULE_PATH = getPath();\n"
            "const pending = import(SECRET_MODULE_PATH);\n"
            'globalThis.fetch(SECRET_URL, { body: "SECRET_PARAMETER" });\n'
        )
        report = self._analyze({"src/dynamic.js": source})
        facts = report["files"][0]["facts"]
        imports = [fact for fact in facts if fact["kind"] == "IMPORT"]
        calls = [fact for fact in facts if fact["kind"] == "API_CALL_CANDIDATE"]
        self.assertEqual(1, len(imports))
        self.assertEqual("UNRESOLVED", imports[0]["specifier_state"])
        self.assertNotIn("specifier", imports[0])
        self.assertEqual(1, len(calls))
        self.assertEqual("globalThis.fetch", calls[0]["callee"])
        self.assertEqual("UNRESOLVED", calls[0]["target_state"])
        serialized = json.dumps(report, sort_keys=True)
        for secret in ("SECRET_MODULE_PATH", "SECRET_URL", "SECRET_PARAMETER", "getPath"):
            self.assertNotIn(secret, serialized)

    def test_type_only_axios_import_does_not_support_runtime_api_candidate(self):
        report = self._analyze({
            "src/types.ts": 'import type axios from "axios"; axios.get("/users");\n'
        })
        facts = report["files"][0]["facts"]
        self.assertFalse(any(fact["kind"] == "API_CALL_CANDIDATE" for fact in facts))

    def test_pdjs2_detects_only_unshadowed_bare_fetch_and_keeps_arguments_private(self):
        safe_source = (
            'import axios from "axios";\n'
            'export function useRows() { return fetch(dynamicUrl("FETCH_URL_CANARY"), '
            '{ body: "FETCH_BODY_CANARY" }); }\n'
            'globalThis.fetch("GLOBAL_URL_CANARY"); axios.post("AXIOS_URL_CANARY");\n'
        )
        report = self._analyze({"src/api.ts": safe_source}, protocol_version=2)
        calls = [
            fact for fact in report["files"][0]["facts"]
            if fact["kind"] == "API_CALL_CANDIDATE"
        ]
        self.assertEqual(
            {"fetch", "globalThis.fetch", "axios.post"},
            {fact["callee"] for fact in calls},
        )
        bare = next(fact for fact in calls if fact["callee"] == "fetch")
        self.assertEqual("UNRESOLVED", bare["target_state"])
        self.assertEqual("bare_fetch_call_syntax", bare["basis"])
        self.assertEqual("frontend-parse-report/2", report["report_version"])
        serialized = json.dumps(report, sort_keys=True)
        for value in (
            "FETCH_URL_CANARY", "FETCH_BODY_CANARY", "GLOBAL_URL_CANARY",
            "AXIOS_URL_CANARY", "dynamicUrl",
        ):
            self.assertNotIn(value, serialized)

    def test_pdjs2_suppresses_bare_fetch_when_any_scope_binds_fetch(self):
        sources = {
            "src/parameter.ts": 'function request(fetch: any) { return fetch("PARAM_URL_CANARY"); }\n',
            "src/local.ts": 'function request() { const fetch = local; return fetch("LOCAL_URL_CANARY"); }\n',
            "src/destructured.ts": 'const { fetch } = client; fetch("DESTRUCTURED_URL_CANARY");\n',
            "src/imported.ts": 'import fetch from "fetch-shim"; fetch("IMPORT_URL_CANARY");\n',
            "src/nested.ts": (
                'function local(fetch: any) { return fetch("NESTED_URL_CANARY"); }\n'
                'fetch("GLOBAL_BUT_CONSERVATIVELY_OMITTED_CANARY");\n'
            ),
        }
        report = self._analyze(sources, protocol_version=2)
        calls = [
            fact for file_record in report["files"] for fact in file_record["facts"]
            if fact["kind"] == "API_CALL_CANDIDATE" and fact["callee"] == "fetch"
        ]
        self.assertEqual([], calls)
        serialized = json.dumps(report, sort_keys=True)
        for value in (
            "PARAM_URL_CANARY", "LOCAL_URL_CANARY", "DESTRUCTURED_URL_CANARY",
            "IMPORT_URL_CANARY", "NESTED_URL_CANARY",
        ):
            self.assertNotIn(value, serialized)

    def test_pdjs2_reports_zustand_variable_export_status(self):
        report = self._analyze({
            "src/stores.ts": (
                'import { create } from "zustand";\n'
                'export const usePublicStore = create(() => ({}));\n'
                'const privateStore = create(() => ({}));\n'
            ),
        }, protocol_version=2)
        stores = [
            fact for fact in report["files"][0]["facts"]
            if fact["kind"] == "STORE_DECLARATION_CANDIDATE"
        ]
        self.assertEqual(
            {"usePublicStore": True, "privateStore": False},
            {fact["name"]: fact["exported"] for fact in stores},
        )

    def test_pdjs1_default_report_stays_on_the_original_protocol(self):
        report = self._api().analyze_sources({
            "src/api.ts": b'fetch("PDJS1_CANARY");\n',
        })
        self.assertEqual("frontend-parse-report/1", report["report_version"])
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("ascii")
        self.assertEqual(
            "b541dc99b037ee80330d248378a116fd04271cc067cb9f0b3afb9be575ee68fe",
            hashlib.sha256(encoded).hexdigest(),
        )
        self.assertFalse(any(
            fact["kind"] == "API_CALL_CANDIDATE" and fact.get("callee") == "fetch"
            for fact in report["files"][0]["facts"]
        ))

    def test_parse_error_is_partial_with_fixed_diagnostic_and_no_source_echo(self):
        report = self._analyze({
            "broken.ts": 'const secret = "SYNTAX_CANARY"; function broken( { return secret; }\n'
        })
        file_record = report["files"][0]
        self.assertEqual("PARTIAL", file_record["status"])
        self.assertEqual([], file_record["facts"])
        self.assertTrue(file_record["diagnostics"])
        self.assertTrue(all(item["code"] == "SYNTAX_ERROR" for item in file_record["diagnostics"]))
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("SYNTAX_CANARY", serialized)
        self.assertNotIn("secret", serialized)

    def test_parser_protocol_rejects_unlisted_file_fields_that_could_echo_source(self):
        api = self._api()
        source = b"export const safe = 1;\n"
        fake_response = {
            "protocol": "PDJS1",
            "parser_version": "6.0.3",
            "files": [{
                "path": "sample.ts",
                "sha256": hashlib.sha256(source).hexdigest(),
                "script_kind": "TS",
                "status": "ANALYZED",
                "facts": [],
                "diagnostics": [],
                "source_text": "SOURCE_ECHO_CANARY",
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.ts").write_bytes(source)
            with patch.object(api, "_parser_command", return_value=(['node'], SCRIPT_DIR)):
                with patch.object(
                    api,
                    "_run_bounded_process",
                    return_value=(0, json.dumps(fake_response).encode("utf-8"), b""),
                ):
                    with self.assertRaises(api.FrontendAnalysisError) as raised:
                        api.analyze_paths(root, ["sample.ts"])
        self.assertEqual("PARSER_PROTOCOL_ERROR", raised.exception.code)

    def _protocol_fixture(self):
        source = (
            'import axios from "axios";\n'
            'export { Value } from "./value";\n'
            'export function useUsers() { return axios.get("/users"); }\n'
            'export const Panel = () => <div/>;\n'
            'export class Store { constructor() {} read() {} }\n'
        )
        path = "src/sample.tsx"
        report = self._analyze({path: source})
        source_text = source.replace("\n", os.linesep)
        source_bytes = source_text.encode("utf-8")
        sources = [{"path": path, "sha256": hashlib.sha256(source_bytes).hexdigest()}]
        source_texts = {path: source_text}
        response = {
            "protocol": "PDJS1",
            "parser_version": "6.0.3",
            "files": report["files"],
        }
        return source, sources, source_texts, response

    def _assert_protocol_rejected(self, api, response, sources, source_texts, payload=None):
        if payload is None:
            payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
        with self.assertRaises(api.FrontendAnalysisError) as raised:
            api._validate_response(payload, b"", 0, sources, source_texts)
        self.assertEqual("PARSER_PROTOCOL_ERROR", raised.exception.code)

    def test_parser_protocol_rejects_extra_root_keys(self):
        api = self._api()
        _source, sources, source_texts, base = self._protocol_fixture()
        extra_root = copy.deepcopy(base)
        extra_root["candidate_description"] = "ROOT_DESCRIPTION_CANARY"
        self._assert_protocol_rejected(api, extra_root, sources, source_texts)

    def test_parser_protocol_rejects_missing_required_field_for_each_fact_kind(self):
        api = self._api()
        _source, sources, source_texts, base = self._protocol_fixture()
        kinds = {fact["kind"] for fact in base["files"][0]["facts"]}
        self.assertEqual(
            {
                "IMPORT", "EXPORT", "SYMBOL_FUNCTION", "SYMBOL_CLASS",
                "COMPONENT_CANDIDATE", "HOOK_CANDIDATE", "API_CALL_CANDIDATE",
            },
            kinds,
        )
        missing_fields = {
            "IMPORT": "form",
            "EXPORT": "form",
            "SYMBOL_FUNCTION": "function_kind",
            "SYMBOL_CLASS": "name",
            "COMPONENT_CANDIDATE": "basis",
            "HOOK_CANDIDATE": "basis",
            "API_CALL_CANDIDATE": "basis",
        }
        for kind, field in missing_fields.items():
            with self.subTest(kind=kind, field=field):
                tampered = copy.deepcopy(base)
                fact = next(item for item in tampered["files"][0]["facts"] if item["kind"] == kind)
                fact.pop(field)
                self._assert_protocol_rejected(api, tampered, sources, source_texts)

    def test_parser_protocol_rejects_invalid_enums_candidate_explanations_and_forged_ids(self):
        api = self._api()
        _source, sources, source_texts, base = self._protocol_fixture()

        mutations = [
            ("IMPORT", "form", "invented-form"),
            ("IMPORT", "form", ["dynamic"]),
            ("SYMBOL_FUNCTION", "function_kind", "candidate-description-canary"),
            ("COMPONENT_CANDIDATE", "basis", "unreviewed explanation"),
            ("COMPONENT_CANDIDATE", "basis", {"description": "candidate-canary"}),
            ("API_CALL_CANDIDATE", "basis", "candidate-description-canary"),
            ("API_CALL_CANDIDATE", "callee", []),
        ]
        for kind, field, value in mutations:
            with self.subTest(kind=kind, field=field):
                tampered = copy.deepcopy(base)
                fact = next(item for item in tampered["files"][0]["facts"] if item["kind"] == kind)
                fact[field] = value
                self._assert_protocol_rejected(api, tampered, sources, source_texts)

        tampered = copy.deepcopy(base)
        candidate = next(
            item for item in tampered["files"][0]["facts"]
            if item["kind"] == "COMPONENT_CANDIDATE"
        )
        candidate["description"] = "CANDIDATE_DESCRIPTION_CANARY"
        self._assert_protocol_rejected(api, tampered, sources, source_texts)

        tampered = copy.deepcopy(base)
        tampered["files"][0]["facts"][0]["id"] = "FE_" + ("0" * 24)
        self._assert_protocol_rejected(api, tampered, sources, source_texts)

    def test_parser_protocol_rejects_offsets_that_disagree_with_source_line_and_column(self):
        api = self._api()
        _source, sources, source_texts, base = self._protocol_fixture()

        wrong_offset = copy.deepcopy(base)
        fact = wrong_offset["files"][0]["facts"][0]
        fact["location"]["start_offset"] += 1
        location = fact["location"]
        id_seed = "\0".join((
            sources[0]["path"], sources[0]["sha256"], fact["kind"],
            str(location["start_offset"]), str(location["end_offset"]),
        ))
        fact["id"] = "FE_" + hashlib.sha256(id_seed.encode("utf-8")).hexdigest()[:24]
        self._assert_protocol_rejected(api, wrong_offset, sources, source_texts)

    def test_parser_protocol_rejects_reversed_columns_on_one_line(self):
        api = self._api()
        _source, sources, source_texts, base = self._protocol_fixture()
        reversed_columns = copy.deepcopy(base)
        fact = next(
            item for item in reversed_columns["files"][0]["facts"]
            if item["location"]["start_line"] == item["location"]["end_line"]
        )
        fact["location"]["start_column"] = fact["location"]["end_column"] + 1
        self._assert_protocol_rejected(api, reversed_columns, sources, source_texts)

    def test_parser_protocol_rejects_duplicate_json_keys(self):
        api = self._api()
        _source, sources, source_texts, response = self._protocol_fixture()
        payload = json.dumps(response, separators=(",", ":"))
        payload = payload.replace(
            '"protocol":"PDJS1"',
            '"protocol":"UNTRUSTED","protocol":"PDJS1"',
            1,
        ).encode("utf-8")
        self._assert_protocol_rejected(api, response, sources, source_texts, payload)

    def test_cli_never_echoes_a_candidate_explanation_from_a_tampered_bridge(self):
        api = self._api()
        source, _sources, _source_texts, response = self._protocol_fixture()
        sentinel = "CANDIDATE_DESCRIPTION_SECRET_CANARY"
        tampered = copy.deepcopy(response)
        candidate = next(
            item for item in tampered["files"][0]["facts"]
            if item["kind"] == "HOOK_CANDIDATE"
        )
        candidate["description"] = sentinel
        api_cli = importlib.import_module("analyze_frontend")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "src" / "sample.tsx"
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8")
            output = io.StringIO()
            errors = io.StringIO()
            with patch.object(api, "_parser_command", return_value=(["node"], SCRIPT_DIR)):
                with patch.object(
                    api,
                    "_run_bounded_process",
                    return_value=(0, json.dumps(tampered).encode("utf-8"), b""),
                ):
                    with redirect_stdout(output), redirect_stderr(errors):
                        status = api_cli.main(["--root", str(root), "--file", "src/sample.tsx"])
        self.assertEqual(2, status)
        self.assertEqual("", output.getvalue())
        self.assertIn("PARSER_PROTOCOL_ERROR", errors.getvalue())
        self.assertNotIn(sentinel, output.getvalue() + errors.getvalue())

    def test_static_import_string_literal_is_absent_from_report_cli_and_diagnostics(self):
        canary = "CANARYVALUE123456"
        export_canary = "EXPORTSPECIFIER_CANARY456"
        source = (
            f'import value from "{canary}";\n'
            f'export {{ value }} from "{export_canary}";\n'
            'export const ready = true;\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.ts").write_text(source, encoding="utf-8")
            api = self._api()
            report = api.analyze_paths(root, ["sample.ts"])
            report_text = json.dumps(report, sort_keys=True)
            result = subprocess.run(
                [sys.executable, str(CLI), "--root", str(root), "--file", "sample.ts"],
                cwd=SCRIPT_DIR.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            broken_source = f'import hidden from "{canary}";\nconst invalid: = 1;\n'
            (root / "broken.ts").write_text(broken_source, encoding="utf-8")
            broken_report = api.analyze_paths(root, ["broken.ts"])
            broken_cli = subprocess.run(
                [sys.executable, str(CLI), "--root", str(root), "--file", "broken.ts"],
                cwd=SCRIPT_DIR.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(0, broken_cli.returncode, broken_cli.stderr)
        emitted = report_text + result.stdout + result.stderr
        diagnostics = (
            json.dumps(broken_report["files"][0]["diagnostics"], sort_keys=True)
            + broken_cli.stdout
            + broken_cli.stderr
        )
        for secret in (canary, export_canary):
            self.assertNotIn(secret, emitted)
            self.assertNotIn(secret, diagnostics)
        import_fact = next(
            fact for fact in report["files"][0]["facts"] if fact["kind"] == "IMPORT"
        )
        export_fact = next(
            fact for fact in report["files"][0]["facts"] if fact["kind"] == "EXPORT"
        )
        self.assertEqual("REDACTED", import_fact["specifier_state"])
        self.assertEqual("UNRESOLVED", import_fact["target_state"])
        self.assertNotIn("specifier", import_fact)
        self.assertEqual("REDACTED", export_fact["specifier_state"])
        self.assertEqual("UNRESOLVED", export_fact["target_state"])
        self.assertNotIn("specifier", export_fact)
        self.assertEqual("PARTIAL", broken_report["files"][0]["status"])

    def test_explicit_input_paths_reject_escape_duplicates_and_unsupported_extensions(self):
        api = self._api()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            (root / "ok.ts").write_text("export const ok = true;", encoding="utf-8")
            (root / "ignored.mjs").write_text("export const ignored = true;", encoding="utf-8")
            outside = Path(temporary) / "outside.ts"
            outside.write_text("export const outside = true;", encoding="utf-8")
            for paths, expected in [
                (["../outside.ts"], "PATH_ESCAPE"),
                (["ignored.mjs"], "UNSUPPORTED_EXTENSION"),
                (["ok.ts", "ok.ts"], "DUPLICATE_INPUT"),
            ]:
                with self.subTest(paths=paths):
                    with self.assertRaises(api.FrontendAnalysisError) as raised:
                        api.analyze_paths(root, paths)
                    self.assertEqual(expected, raised.exception.code)

    def test_file_size_and_count_limits_reject_before_parser_launch(self):
        api = self._api()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a.ts", "b.ts"):
                (root / name).write_bytes(b"1234")
            with patch.object(api, "MAX_FILE_BYTES", 3), patch.object(
                api, "_run_bounded_process", side_effect=AssertionError("bridge must not launch")
            ):
                with self.assertRaises(api.FrontendAnalysisError) as raised:
                    api.analyze_paths(root, ["a.ts"])
                self.assertEqual("SOURCE_FILE_TOO_LARGE", raised.exception.code)
            with patch.object(api, "MAX_FILES", 1), patch.object(
                api, "_run_bounded_process", side_effect=AssertionError("bridge must not launch")
            ):
                with self.assertRaises(api.FrontendAnalysisError) as raised:
                    api.analyze_paths(root, ["a.ts", "b.ts"])
                self.assertEqual("TOO_MANY_FILES", raised.exception.code)
            with patch.object(api, "MAX_TOTAL_BYTES", 5), patch.object(
                api, "_run_bounded_process", side_effect=AssertionError("bridge must not launch")
            ):
                with self.assertRaises(api.FrontendAnalysisError) as raised:
                    api.analyze_paths(root, ["a.ts", "b.ts"])
                self.assertEqual("SOURCE_TOTAL_TOO_LARGE", raised.exception.code)

    def test_subprocess_output_cap_terminates_child_while_draining(self):
        api = self._api()
        with self.assertRaises(api.FrontendAnalysisError) as raised:
            api._run_bounded_process(
                [sys.executable, "-c", "import os\nwhile True: os.write(1, b'x' * 8192)"],
                input_bytes=b"",
                cwd=Path(__file__).resolve().parent,
                timeout_seconds=10,
                max_output_bytes=4096,
            )
        self.assertEqual("PARSER_OUTPUT_LIMIT", raised.exception.code)

    def test_reaching_subprocess_output_cap_terminates_child_before_timeout(self):
        api = self._api()
        with self.assertRaises(api.FrontendAnalysisError) as raised:
            api._run_bounded_process(
                [sys.executable, "-c", "import os,time; os.write(1, b'x' * 4096); time.sleep(30)"],
                input_bytes=b"",
                cwd=Path(__file__).resolve().parent,
                timeout_seconds=2,
                max_output_bytes=4096,
            )
        self.assertEqual("PARSER_OUTPUT_LIMIT", raised.exception.code)

    def test_subprocess_timeout_terminates_child(self):
        api = self._api()
        with self.assertRaises(api.FrontendAnalysisError) as raised:
            api._run_bounded_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                input_bytes=b"",
                cwd=Path(__file__).resolve().parent,
                timeout_seconds=0.1,
                max_output_bytes=4096,
            )
        self.assertEqual("PARSER_TIMEOUT", raised.exception.code)

    def test_subprocess_output_cap_is_shared_by_stdout_and_stderr(self):
        api = self._api()
        with self.assertRaises(api.FrontendAnalysisError) as raised:
            api._run_bounded_process(
                [sys.executable, "-c", "import os,time; os.write(1, b'x' * 3072); os.write(2, b'y' * 3072); time.sleep(30)"],
                input_bytes=b"",
                cwd=Path(__file__).resolve().parent,
                timeout_seconds=2,
                max_output_bytes=4096,
            )
        self.assertEqual("PARSER_OUTPUT_LIMIT", raised.exception.code)

    def test_target_module_and_target_dependency_are_never_loaded_or_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "target-code-ran.marker"
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "node_modules" / "untrusted").mkdir(parents=True)
            (project / "package.json").write_text(
                '{"dependencies":{"untrusted":"file:node_modules/untrusted"}}',
                encoding="utf-8",
            )
            (project / "node_modules" / "untrusted" / "index.js").write_text(
                f'require("node:fs").writeFileSync("{marker.as_posix()}", "ran");',
                encoding="utf-8",
            )
            (project / "src" / "entry.ts").write_text(
                'import "untrusted";\n'
                'export class NeverLoad { static {{ const token = "TARGET_BODY_CANARY"; }} }\n',
                encoding="utf-8",
            )
            api = self._api()
            report = api.analyze_paths(project, ["src/entry.ts"])
            self.assertFalse(marker.exists())
            serialized = json.dumps(report, sort_keys=True)
            self.assertNotIn("TARGET_BODY_CANARY", serialized)
            self.assertNotIn(str(marker), serialized)
            self.assertNotIn("node_modules/untrusted/index.js", serialized)

    def test_cli_outputs_json_only_for_explicit_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "one.ts").write_text("export class One {}\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(CLI), "--root", str(root), "--file", "src/one.ts"],
                cwd=SCRIPT_DIR.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(["src/one.ts"], [item["path"] for item in report["files"]])
            self.assertEqual("", result.stderr)

    def test_cli_explicitly_selects_pdjs2_without_changing_default_protocol(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "one.ts").write_text(
                'fetch("CLI_PDJS2_ARGUMENT_CANARY");\n', encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable, str(CLI), "--root", str(root),
                    "--file", "src/one.ts", "--protocol-version", "2",
                ],
                cwd=SCRIPT_DIR.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual("frontend-parse-report/2", report["report_version"])
            self.assertTrue(any(
                fact.get("callee") == "fetch"
                for fact in report["files"][0]["facts"]
            ))
            self.assertNotIn("CLI_PDJS2_ARGUMENT_CANARY", result.stdout)
            self.assertEqual("", result.stderr)

    def test_in_memory_transport_parses_caller_bytes_without_opening_target_paths(self):
        api = self._api()
        source = b"export function SnapshotOnly() { return 7; }\n"
        report = api.analyze_sources({"src/not-on-disk.ts": source})

        file_record = report["files"][0]
        self.assertEqual("src/not-on-disk.ts", file_record["path"])
        self.assertEqual(hashlib.sha256(source).hexdigest(), file_record["sha256"])
        self.assertIn(
            "SnapshotOnly",
            {fact.get("name") for fact in file_record["facts"]},
        )

    def test_in_memory_transport_enforces_the_existing_per_file_limit(self):
        api = self._api()
        oversized = b"x" * (api.MAX_FILE_BYTES + 1)
        with self.assertRaises(api.FrontendAnalysisError) as raised:
            api.analyze_sources({"large.ts": oversized})
        self.assertEqual("SOURCE_FILE_TOO_LARGE", raised.exception.code)

    def test_in_memory_transport_enforces_file_count_and_aggregate_byte_limits_before_launch(self):
        api = self._api()
        too_many = {f"file-{index}.ts": b"" for index in range(api.MAX_FILES + 1)}
        too_large = {
            **{f"large-{index}.ts": b"x" * api.MAX_FILE_BYTES for index in range(8)},
            "large-last.ts": b"x",
        }
        with patch.object(api, "_parser_command", side_effect=AssertionError("parser must not start")):
            with self.assertRaises(api.FrontendAnalysisError) as count_error:
                api.analyze_sources(too_many)
            with self.assertRaises(api.FrontendAnalysisError) as size_error:
                api.analyze_sources(too_large)
        self.assertEqual("TOO_MANY_FILES", count_error.exception.code)
        self.assertEqual("SOURCE_TOTAL_TOO_LARGE", size_error.exception.code)

    def test_in_memory_transport_rejects_duplicate_normalized_paths_before_launch(self):
        api = self._api()
        sources = {"src\\entry.ts": b"", "src/entry.ts": b""}
        with patch.object(api, "_parser_command", side_effect=AssertionError("parser must not start")):
            with self.assertRaises(api.FrontendAnalysisError) as raised:
                api.analyze_sources(sources)
        self.assertEqual("DUPLICATE_INPUT", raised.exception.code)


if __name__ == "__main__":
    unittest.main()

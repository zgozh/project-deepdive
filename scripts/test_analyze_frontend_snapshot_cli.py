#!/usr/bin/env python3
"""Same-snapshot integration tests for the Phase 3D2A frontend CLI."""

from __future__ import annotations

import contextlib
import copy
import importlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from artifact_contract import dumps_artifact, load_artifact  # noqa: E402
from test_frontend_snapshot_analysis import _prepare  # noqa: E402


CLI = SKILL_ROOT / "scripts" / "analyze_frontend_snapshot.py"
_PARSER_READY = shutil.which("node") and (
    SKILL_ROOT / "scripts" / "frontend_parse_bridge" / "node_modules"
    / "typescript" / "package.json"
).is_file()


def _write_inputs(work: Path, index, coverage, profile, evidence):
    inputs = work / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, artifact in (
        ("project-index.json", index),
        ("coverage.json", coverage),
        ("stack-profile.json", profile),
        ("phase3a-evidence.json", evidence),
    ):
        path = inputs / name
        path.write_text(dumps_artifact(artifact), encoding="utf-8")
        paths[name] = path
    return paths


def _run(root, paths, out, *, analysis_version=None):
    command = [
        sys.executable, str(CLI),
        "--root", str(root),
        "--index", str(paths["project-index.json"]),
        "--coverage", str(paths["coverage.json"]),
        "--stack-profile", str(paths["stack-profile.json"]),
        "--phase3a-evidence", str(paths["phase3a-evidence.json"]),
        "--out", str(out),
    ]
    if analysis_version is not None:
        command.extend(["--analysis-version", analysis_version])
    return subprocess.run(
        command,
        cwd=SKILL_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@unittest.skipUnless(_PARSER_READY, "Node.js or the tool-local TypeScript parser is unavailable")
class AnalyzeFrontendSnapshotCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pd-frontend-cli-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def test_cli_publishes_a_deterministic_bundle_and_preserves_profile_bytes(self):
        source = (
            'import client from "MODULE_CLI_CANARY";\n'
            "export function useMessages() { return null; }\n"
            'export const Page = () => <main>BODY_CLI_CANARY</main>;\n'
            'globalThis.fetch("https://URL_CLI_CANARY", { body: "ARG_CLI_CANARY" });\n'
        ).encode("utf-8")
        root, index, coverage, profile, evidence, _sources = _prepare(
            self.work, {"frontend/src/Page.tsx": source},
        )
        paths = _write_inputs(self.work, index, coverage, profile, evidence)
        profile_bytes = paths["stack-profile.json"].read_bytes()
        first_out, second_out = self.work / "out-one", self.work / "out-two"

        first = _run(root, paths, first_out)
        second = _run(root, paths, second_out)

        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        names = {"stack-profile.json", "static-analysis.json", "evidence.json"}
        self.assertEqual(names, {path.name for path in first_out.iterdir()})
        for name in names:
            self.assertEqual((first_out / name).read_bytes(), (second_out / name).read_bytes())
        self.assertEqual(profile_bytes, (first_out / "stack-profile.json").read_bytes())
        analysis = load_artifact(first_out / "static-analysis.json")
        merged = load_artifact(first_out / "evidence.json")
        self.assertEqual("1.4.0", analysis["schema_version"])
        self.assertEqual(analysis["source_metadata"], merged["source_metadata"])
        self.assertIn("Page", {row["name"] for row in analysis["symbols"]})
        self.assertIn("useMessages", {row["name"] for row in analysis["symbols"]})
        serialized = (
            b"".join((first_out / name).read_bytes() for name in names)
            + first.stdout.encode("utf-8")
            + first.stderr.encode("utf-8")
        )
        for canary in (
            b"MODULE_CLI_CANARY", b"BODY_CLI_CANARY", b"URL_CLI_CANARY",
            b"ARG_CLI_CANARY", b"https://",
        ):
            self.assertNotIn(canary, serialized)

    def test_git_tree_cli_analyzes_committed_bytes_after_worktree_edit(self):
        committed_source = b"export function CommittedFrontend() {}\n"
        root, index, coverage, profile, evidence, _sources = _prepare(
            self.work, {"frontend/src/Entry.ts": committed_source}, snapshot="git-tree",
        )
        paths = _write_inputs(self.work, index, coverage, profile, evidence)
        (root / "frontend" / "src" / "Entry.ts").write_bytes(
            b"export function WorktreeOnlyFrontend() {}\n",
        )

        result = _run(root, paths, self.work / "git-tree-out")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        analysis = load_artifact(self.work / "git-tree-out" / "static-analysis.json")
        names = {row["name"] for row in analysis["symbols"]}
        self.assertIn("CommittedFrontend", names)
        self.assertNotIn("WorktreeOnlyFrontend", names)

    def test_cli_publishes_opt_in_v15_bare_fetch_without_echoing_arguments(self):
        source = (
            b"export function useRows() {\n"
            b'  return fetch("https://CLI_URL_CANARY", { body: "CLI_BODY_CANARY" });\n'
            b"}\n"
        )
        root, index, coverage, profile, evidence, _sources = _prepare(
            self.work, {"frontend/src/api.ts": source},
        )
        paths = _write_inputs(self.work, index, coverage, profile, evidence)
        out = self.work / "out-v15"

        result = _run(root, paths, out, analysis_version="1.5.0")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        analysis = load_artifact(out / "static-analysis.json")
        self.assertEqual("1.5.0", analysis["schema_version"])
        self.assertTrue(any(
            role["kind"] == "api_client_candidate" for role in analysis["roles"]
        ))
        serialized = b"".join(path.read_bytes() for path in out.iterdir())
        serialized += result.stdout.encode("utf-8") + result.stderr.encode("utf-8")
        self.assertNotIn(b"CLI_URL_CANARY", serialized)
        self.assertNotIn(b"CLI_BODY_CANARY", serialized)

    def test_g01_failure_and_missing_parser_publish_nothing(self):
        source = b"export function Safe() {}\n"
        root, index, coverage, profile, evidence, _sources = _prepare(
            self.work, {"frontend/src/Safe.ts": source},
        )
        bad_coverage = copy.deepcopy(coverage)
        bad_coverage["tracked_file_count"] += 1
        paths = _write_inputs(self.work, index, bad_coverage, profile, evidence)
        failed_out = self.work / "g01-fail-out"

        failed = _run(root, paths, failed_out)

        self.assertNotEqual(0, failed.returncode)
        self.assertFalse(failed_out.exists())

        good_paths = _write_inputs(self.work / "good-inputs", index, coverage, profile, evidence)
        cli = importlib.import_module("analyze_frontend_snapshot")
        frontend = importlib.import_module("frontend_static_analysis")
        from frontend_static_analysis import FrontendAnalysisError

        missing_out = self.work / "missing-parser-out"
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(
                frontend,
                "_parser_command",
                side_effect=FrontendAnalysisError("NODE_NOT_FOUND"),
            ) as parser_command,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main([
                "--root", str(root),
                "--index", str(good_paths["project-index.json"]),
                "--coverage", str(good_paths["coverage.json"]),
                "--stack-profile", str(good_paths["stack-profile.json"]),
                "--phase3a-evidence", str(good_paths["phase3a-evidence.json"]),
                "--out", str(missing_out),
            ])
        self.assertNotEqual(0, code, stdout.getvalue() + stderr.getvalue())
        parser_command.assert_called_once()
        self.assertFalse(missing_out.exists())
        self.assertIn("PARSER_NODE_NOT_FOUND", stdout.getvalue() + stderr.getvalue())

        publication_out = self.work / "publication-fail-out"
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(cli, "_publish", side_effect=OSError("PUBLICATION_CANARY")) as publish,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main([
                "--root", str(root),
                "--index", str(good_paths["project-index.json"]),
                "--coverage", str(good_paths["coverage.json"]),
                "--stack-profile", str(good_paths["stack-profile.json"]),
                "--phase3a-evidence", str(good_paths["phase3a-evidence.json"]),
                "--out", str(publication_out),
            ])
        self.assertEqual(cli.EXIT_OPERATIONAL, code)
        publish.assert_called_once()
        self.assertFalse(publication_out.exists())
        self.assertIn("PUBLICATION_FAILED", stderr.getvalue())
        self.assertNotIn("PUBLICATION_CANARY", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

"""Behavior and safety contracts for static VibeCoding compilation."""

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact
from phase7_vibecoding import STAGES, render_lab, render_prompts, validate_development_plan
from project_vibecode import compile_plan_files

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/artifacts/v1"
CLI = ROOT / "scripts/project_vibecode.py"


def inputs(language="python"):
    """Use actual frozen fixture bytes, without importing or executing them."""
    plan = json.loads((FIXTURES / "development-plan.json").read_text(encoding="utf-8"))
    index = json.loads((FIXTURES / "project-index.json").read_text(encoding="utf-8"))
    evidence = json.loads((FIXTURES / "evidence.json").read_text(encoding="utf-8"))
    target = ROOT / "tests/fixtures/repository_scanner" / language
    source, protected = (("src/sample/service.py", "pyproject.toml") if language == "python"
                         else ("src/main/java/example/OrderService.java", "pom.xml"))
    index["project"].update(name=language + "-fixture", root=str(target))
    index["files"] = []
    for path in (source, protected):
        raw = (target / path).read_bytes()
        index["files"].append({"path": path, "bytes": len(raw),
                               "sha256": hashlib.sha256(raw).hexdigest(), "tracked": True})
    index["file_count"] = len(index["files"])
    evidence["items"] = [copy.deepcopy(evidence["items"][0])]
    evidence["items"][0]["locator"] = {"path": source, "line_start": 1, "line_end": 1}
    evidence["items"][0]["summary"] = "Selected frozen fixture bytes; no runtime claim."
    plan["impact"]["existing_files"] = [source]
    plan["slices"][0].update(allowed_existing=[source], protected=[protected])
    return plan, index, evidence


def write_inputs(directory, data):
    paths = []
    for name, artifact in zip(("plan.json", "index.json", "evidence.json"), data):
        path = directory / name
        path.write_text(dumps_artifact(artifact), encoding="utf-8")
        paths.append(path)
    return paths


class PlanTests(unittest.TestCase):
    def test_python_and_java_frozen_inputs_compile_complete_content(self):
        for language in ("python", "java"):
            with self.subTest(language=language):
                plan, index, evidence = inputs(language)
                validate_development_plan(plan, index, evidence)
                lab = render_lab(plan, evidence)
                prompts = render_prompts(plan)
                for stage in plan["stages"]:
                    for key in ("explanation", "learner_actions", "agent_actions", "gate", "failure_recovery"):
                        self.assertIn(stage[key], lab)
                    for content in stage["prompt"].values():
                        self.assertIn(content, lab)
                        self.assertIn(content, prompts)
                self.assertIn(plan["interview_followup"][0]["answer"], lab)
                self.assertIn(plan["repository_revision"], lab)
                self.assertIn("NOT_RUN", lab)
                self.assertEqual(tuple(s["name"] for s in plan["stages"]), STAGES)
                # C1 can legitimately be used by a slice and several stages.
                self.assertEqual(plan["slices"][0]["tests"], ["C1"])

    def test_literal_markdown_prompt_and_command_text_are_not_rewritten(self):
        plan, index, evidence = inputs()
        content = "\n```python\nprint(1)\n```\n${literal}\n$(literal)\n| A | B |\n|---|---|\n\n初轮→人的核验→追问→修订→继续\n"
        plan["stages"][0]["agent_actions"] = content
        plan["stages"][0]["prompt"]["context"] = content
        plan["checks"][0]["command"] = 'python -c "print(${literal})"'
        validate_development_plan(plan, index, evidence)
        self.assertIn(content, render_lab(plan, evidence))
        self.assertIn(plan["checks"][0]["command"], render_lab(plan, evidence))
        self.assertIn(content, render_prompts(plan))

    def test_e6_inference_and_official_external_locator_are_preserved(self):
        plan, index, evidence = inputs()
        for ref, level, kind, locator in (
                ("EVID-inference", "E6", "inference", {"observation": "Unverified explanation"}),
                ("EVID-official", "E5", "official_documentation",
                 {"path": "https://example.org/official", "observation": "Author-supplied external reference"})):
            evidence["items"].append({"id": ref, "level": level, "kind": kind,
                                      "summary": "No project-source or runtime authentication.",
                                      "confidence": 0.5, "locator": locator})
            plan["evidence_refs"].append(ref)
        validate_development_plan(plan, index, evidence)
        lab = render_lab(plan, evidence)
        self.assertIn("EVID-inference E6（推断，不是已证事实）", lab)
        self.assertIn("https://example.org/official", lab)
        self.assertEqual(evidence["items"][1]["level"], "E6")

    def test_unknown_and_duplicate_references_and_identities_fail(self):
        mutations = (
            lambda p, i, e: p["evidence_refs"].append("EVID-unknown"),
            lambda p, i, e: p["evidence_refs"].append(p["evidence_refs"][0]),
            lambda p, i, e: e["items"].append(copy.deepcopy(e["items"][0])),
            lambda p, i, e: i.update(repository_revision="different"),
            lambda p, i, e: e.update(repository_revision="different"),
            lambda p, i, e: p["checks"].append(copy.deepcopy(p["checks"][0])),
            lambda p, i, e: p["slices"][0]["tests"].append("C-unknown"),
            lambda p, i, e: p["slices"][0]["acceptance_refs"].append("A-unknown"),
            lambda p, i, e: p["stages"][0]["checks"].append("C-unknown"),
        )
        for mutation in mutations:
            data = inputs()
            mutation(*data)
            with self.subTest(mutation=mutation), self.assertRaises(ArtifactValidationError):
                validate_development_plan(*data)

    def test_safe_posix_paths_and_root_cwd(self):
        validate_development_plan(*inputs())
        for path in ("../escape", "/absolute", "C:/drive", "//server/share",
                     "a\\b", ".", "a/./b", "a//b", "a/../b", "bad\x00path"):
            for where in ("planned_file", "cwd"):
                data = inputs()
                if where == "planned_file":
                    data[0]["impact"]["planned_new_files"] = [path]
                    data[0]["slices"][0]["allowed_new"] = [path]
                else:
                    data[0]["checks"][0]["cwd"] = path
                if where == "cwd" and path == ".":
                    validate_development_plan(*data)
                else:
                    with self.subTest(path=path, where=where), self.assertRaises(ArtifactValidationError):
                        validate_development_plan(*data)

    def test_stage_omission_duplicate_and_order_fail(self):
        for change in ("missing", "duplicate", "order"):
            data = inputs()
            if change == "missing":
                data[0]["stages"].pop()
            elif change == "duplicate":
                data[0]["stages"][-1] = copy.deepcopy(data[0]["stages"][0])
            else:
                data[0]["stages"][0], data[0]["stages"][1] = data[0]["stages"][1], data[0]["stages"][0]
            with self.subTest(change=change), self.assertRaises(ArtifactValidationError):
                validate_development_plan(*data)

    def test_impact_and_slice_scope_must_match_real_index(self):
        mutations = (
            lambda p, i, e: p["impact"]["existing_files"].append("missing.py"),
            lambda p, i, e: p["impact"]["planned_new_files"].append(p["impact"]["existing_files"][0]),
            lambda p, i, e: p["slices"][0]["allowed_existing"].append("other.py"),
            lambda p, i, e: p["slices"][0]["allowed_new"].append("other.py"),
            lambda p, i, e: p["slices"][0]["protected"].append(p["impact"]["existing_files"][0]),
            lambda p, i, e: p["slices"][0]["protected"].append("unknown.py"),
            lambda p, i, e: e["items"][0]["locator"].update(path="unknown.py"),
        )
        for mutation in mutations:
            data = inputs(); mutation(*data)
            with self.subTest(mutation=mutation), self.assertRaises(ArtifactValidationError):
                validate_development_plan(*data)

    def test_dependency_and_check_refs_are_bounded_but_reusable(self):
        data = inputs(); p = data[0]
        second = copy.deepcopy(p["slices"][0]); second.update(id="S2", depends_on=["S1"])
        p["slices"].append(second)
        validate_development_plan(*data)
        for deps in (["missing"], ["S2"], ["S1", "S1"]):
            bad = copy.deepcopy(data); bad[0]["slices"][1]["depends_on"] = deps
            with self.subTest(deps=deps), self.assertRaises(ArtifactValidationError):
                validate_development_plan(*bad)
        bad = copy.deepcopy(data);bad[0]["slices"][0]["depends_on"] = ["S2"]
        with self.assertRaises(ArtifactValidationError):
            validate_development_plan(*bad)

    def test_versions_and_statuses_fail_closed(self):
        for field, value in (("schema_version", "1.1.0"), ("implementation_status", "PASS"),
                             ("verification_status", "PASS"), ("review_status", "PASS")):
            data = inputs();data[0][field] = value
            with self.subTest(field=field), self.assertRaises(ArtifactValidationError):
                validate_development_plan(*data)


class CliTests(unittest.TestCase):
    def test_real_cli_success_and_rejected_input_do_not_execute_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); data = inputs(); sentinel = directory / "command-executed"
            data[0]["checks"][0]["command"] = f'python -c "from pathlib import Path; Path({str(sentinel)!r}).touch()"'
            paths = write_inputs(directory, data);out = directory / "output"
            command = [sys.executable, "-X", "utf8", str(CLI), "--plan", str(paths[0]),
                       "--project-index", str(paths[1]), "--evidence", str(paths[2]), "--out", str(out)]
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({p.name for p in out.iterdir()}, {"LAB.md", "PROMPTS.md", "development-plan.json"})
            self.assertEqual(load_artifact(out / "development-plan.json"), data[0])
            self.assertFalse(sentinel.exists())
            # Persisted plan can be reused without scanning the fixture repository.
            compile_plan_files(out / "development-plan.json", paths[1], paths[2], directory / "output2")
            data[0]["evidence_refs"].append("EVID-unknown");paths[0].write_text(dumps_artifact(data[0]), encoding="utf-8")
            command[-1] = str(directory / "bad")
            rejected = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(rejected.returncode, 2)
            self.assertFalse((directory / "bad").exists())
            self.assertFalse(sentinel.exists())

    def test_existing_file_directory_and_dangling_symlink_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp);paths = write_inputs(directory, inputs())
            for kind in ("file", "directory"):
                out = directory / kind
                if kind == "file":
                    out.write_text("original", encoding="utf-8")
                else:
                    out.mkdir();(out / "original").write_text("original", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    compile_plan_files(*paths, out)
                self.assertEqual((out if kind == "file" else out / "original").read_text(), "original")
            link = directory / "dangling"
            try:
                link.symlink_to(directory / "missing", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"host symlink permission unavailable: {exc}")
            with self.assertRaises(FileExistsError):
                compile_plan_files(*paths, link)
            self.assertTrue(link.is_symlink())

    def test_same_output_race_has_one_winner_and_preserves_complete_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp);paths = write_inputs(directory, inputs());out = directory / "race"
            def attempt():
                try:
                    compile_plan_files(*paths, out);return "success"
                except FileExistsError:
                    return "existing"
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: attempt(), range(2)))
            self.assertCountEqual(results, ["success", "existing"])
            self.assertEqual(load_artifact(out / "development-plan.json"), inputs()[0])

    def test_write_failure_cleans_only_own_files_and_not_unexpected_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp);paths = write_inputs(directory, inputs());original_open = Path.open
            for foreign in (False, True):
                out = directory / ("foreign" if foreign else "clean")
                def failed_open(path, *args, **kwargs):
                    if path == out / "PROMPTS.md":
                        if foreign:
                            (out / "external-entry").write_text("keep", encoding="utf-8")
                        raise OSError("simulated output write failure")
                    return original_open(path, *args, **kwargs)
                with patch.object(Path, "open", failed_open), self.assertRaises(OSError):
                    compile_plan_files(*paths, out)
                if foreign:
                    self.assertEqual((out / "external-entry").read_text(), "keep")
                    self.assertEqual({p.name for p in out.iterdir()}, {"external-entry"})
                else:
                    self.assertFalse(out.exists())

    def test_output_inside_declared_target_and_invalid_json_never_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp);data = inputs();data[1]["project"]["root"] = str(directory / "target")
            paths = write_inputs(directory, data)
            with self.assertRaises(ArtifactValidationError):
                compile_plan_files(*paths, directory / "target" / "out")
            self.assertFalse((directory / "target").exists())
            paths[0].write_text('{"artifact_kind":"development-plan","artifact_kind":"evidence"}', encoding="utf-8")
            with self.assertRaises(ArtifactValidationError):
                compile_plan_files(*paths, directory / "invalid")
            self.assertFalse((directory / "invalid").exists())


if __name__ == "__main__":
    unittest.main()

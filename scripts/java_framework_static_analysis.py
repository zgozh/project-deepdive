#!/usr/bin/env python3
"""Conservative Java framework candidates derived from a locked C1 snapshot."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
import shutil
from typing import Any, Mapping
from pathlib import Path

from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact
from java_static_analysis import (
    BRIDGE_SOURCE,
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    MAX_BRIDGE_COMPILE_OUTPUT_BYTES,
    MAX_PROTOCOL_BYTES,
    MAX_SOURCE_BYTES,
    StaticAnalysisError as C1StaticAnalysisError,
    _SkipFile,
    _run_bounded_process,
    _decode_source,
    _line_range,
    _parse_kind,
    _parse_protocol,
    _source_payload,
    _symbol_id,
    _evidence_id,
    analyze_java_artifacts,
    validate_java_analysis_bundle,
)
from stack_detection import StackDetectionError, _source_metadata


class StaticAnalysisError(ValueError):
    """A Phase 3C2 input or cross-artifact invariant failed."""


_ANNOTATIONS = {
    "org.springframework.stereotype.Controller",
    "org.springframework.web.bind.annotation.RestController",
    "org.springframework.web.bind.annotation.RequestMapping",
    "org.springframework.web.bind.annotation.GetMapping",
    "org.springframework.web.bind.annotation.PostMapping",
    "org.springframework.web.bind.annotation.PutMapping",
    "org.springframework.web.bind.annotation.PatchMapping",
    "org.springframework.web.bind.annotation.DeleteMapping",
    "javax.persistence.Entity", "javax.persistence.Embeddable",
    "javax.persistence.MappedSuperclass", "jakarta.persistence.Entity",
    "jakarta.persistence.Embeddable", "jakarta.persistence.MappedSuperclass",
    "org.junit.Test", "org.junit.jupiter.api.Test",
    "org.junit.jupiter.api.RepeatedTest", "org.junit.jupiter.api.TestFactory",
    "org.junit.jupiter.params.ParameterizedTest",
}
_LOCAL_ID = re.compile(r"(?:compilation_unit|class|interface|enum|record|annotation|method|constructor):[0-9]+:[0-9]+")


def _decode_pdj2_text(value: str, label: str) -> str:
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
        decoded = raw.decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise StaticAnalysisError(f"PDJ2 contains invalid encoded {label}") from exc
    if not decoded or len(raw) > 4096 or "\x00" in decoded:
        raise StaticAnalysisError(f"PDJ2 contains unsafe {label}")
    return decoded


def _pdj2_int(value: str, label: str) -> int:
    if len(value) > 10 or not re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        raise StaticAnalysisError(f"PDJ2 contains invalid {label}")
    try:
        return int(value)
    except ValueError as exc:
        raise StaticAnalysisError(f"PDJ2 contains invalid {label}") from exc


def _parse_framework_protocol(stdout: str, files: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if len(stdout.encode("utf-8", "replace")) > MAX_PROTOCOL_BYTES:
        raise StaticAnalysisError("PDJ2 protocol exceeded its output limit")
    lines = stdout.splitlines()
    if not lines or lines[0] != "PDJ2":
        raise StaticAnalysisError("Java framework parser returned an invalid PDJ2 header")
    parsed: dict[int, dict[str, Any]] = {}
    seen_annotations: set[tuple[Any, ...]] = set()
    for line in lines[1:]:
        if not line:
            continue
        fields = line.split("\t")
        if fields[0] == "FILE":
            if len(fields) != 5:
                raise StaticAnalysisError("PDJ2 contains an invalid file record")
            index = _pdj2_int(fields[1], "file index")
            if index >= len(files) or index in parsed or fields[2] not in {"OK", "UNSUPPORTED_SYNTAX", "AST_TOO_LARGE", "PARSER_LIMIT"}:
                raise StaticAnalysisError("PDJ2 contains an unexpected file record")
            line_count = _pdj2_int(fields[3], "line count")
            source_length = _pdj2_int(fields[4], "source length")
            text = files[index].get("text")
            if not isinstance(text, str):
                raise StaticAnalysisError("PDJ2 input source is unavailable")
            expected_length = len(text.encode("utf-16-le")) // 2
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            expected_lines = max(1, normalized.count("\n") + (0 if normalized.endswith("\n") else 1))
            if line_count != expected_lines or source_length != expected_length:
                raise StaticAnalysisError("PDJ2 file record does not match the submitted source")
            parsed[index] = {
                "status": fields[2], "line_count": line_count,
                "source_length": source_length, "annotations": [],
            }
            continue
        if fields[0] != "ANNOTATION" or len(fields) != 8:
            raise StaticAnalysisError("PDJ2 contains an unknown or malformed row")
        index = _pdj2_int(fields[1], "annotation file index")
        if index >= len(files) or index not in parsed or parsed[index]["status"] != "OK":
            raise StaticAnalysisError("PDJ2 annotation refers to an unavailable file")
        fqn = _decode_pdj2_text(fields[2], "annotation identity")
        owner_local_id = _decode_pdj2_text(fields[3], "annotation owner ID")
        target_local_id = _decode_pdj2_text(fields[4], "annotation target ID")
        if fqn not in _ANNOTATIONS or not _LOCAL_ID.fullmatch(owner_local_id) or not _LOCAL_ID.fullmatch(target_local_id):
            raise StaticAnalysisError("PDJ2 contains an unsupported annotation identity")
        line_number = _pdj2_int(fields[5], "annotation line")
        start = _pdj2_int(fields[6], "annotation start offset")
        end = _pdj2_int(fields[7], "annotation end offset")
        record = parsed[index]
        if not (1 <= line_number <= record["line_count"] and 0 <= start < end <= record["source_length"]):
            raise StaticAnalysisError("PDJ2 annotation source position is invalid")
        signature = (index, fqn, owner_local_id, target_local_id, line_number, start, end)
        if signature in seen_annotations:
            raise StaticAnalysisError("PDJ2 contains a duplicate annotation row")
        seen_annotations.add(signature)
        record["annotations"].append({
            "annotation_fqn": fqn,
            "owner_local_id": owner_local_id,
            "target_local_id": target_local_id,
            "line": line_number,
            "start_offset": start,
            "end_offset": end,
        })
    if set(parsed) != set(range(len(files))):
        raise StaticAnalysisError("PDJ2 omitted a submitted file record")
    return parsed


class _JavaFrameworkParserRunner:
    """Compile the checked-in bridge once and parse one bounded annotation batch."""

    def __init__(self, java: str, javac: str):
        self.java = java
        self.environment = os.environ.copy()
        for key in (
            "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS",
            "JDK_JAVAC_OPTIONS", "CLASSPATH",
        ):
            self.environment.pop(key, None)
        self.temporary = tempfile.TemporaryDirectory(prefix="project-deepdive-java-c2-")
        self.root = Path(self.temporary.name)
        self.classes = self.root / "classes"
        self.classes.mkdir()
        try:
            return_code, _stdout, _stderr = _run_bounded_process(
                [javac, "-proc:none", "-encoding", "UTF-8", "-d", str(self.classes), str(BRIDGE_SOURCE)],
                cwd=self.root, env=self.environment, timeout=30,
                max_output_bytes=MAX_BRIDGE_COMPILE_OUTPUT_BYTES,
                operation="JDK parse bridge compilation",
            )
            if return_code != 0:
                raise StaticAnalysisError("JDK parse bridge compilation failed")
        except C1StaticAnalysisError as exc:
            self.close()
            raise StaticAnalysisError("JDK parse bridge compilation failed closed") from exc
        except BaseException:
            self.close()
            raise

    def close(self):
        self.temporary.cleanup()

    def parse_batch(self, batch: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return self._parse(batch, framework=True)

    def parse_v12_batch(self, batch: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        return self._parse(batch, framework=False)

    def _parse(self, batch: list[dict[str, Any]], *, framework: bool):
        with tempfile.TemporaryDirectory(prefix="batch-", dir=self.root) as temporary:
            batch_root = Path(temporary)
            parser_files = []
            for index, item in enumerate(batch):
                parser_file = batch_root / f"unit-{index}.java"
                parser_file.write_text(item["text"], encoding="utf-8", newline="\n")
                parser_files.append(parser_file)
            command = [self.java, "-Xms16m", "-Xmx256m", "-cp", str(self.classes), "DeepDiveJavaParser"]
            if framework:
                command.extend(["--protocol", "PDJ2"])
            command.extend(str(path) for path in parser_files)
            try:
                return_code, stdout, stderr = _run_bounded_process(
                    command, cwd=batch_root, env=self.environment, timeout=30,
                    max_output_bytes=MAX_PROTOCOL_BYTES,
                    operation="JDK Java framework parser" if framework else "JDK Java parser",
                )
            except C1StaticAnalysisError as exc:
                raise StaticAnalysisError(str(exc)) from exc
            if return_code != 0:
                raise StaticAnalysisError("JDK Java framework parser failed" if framework else "JDK Java parser failed")
            try:
                parsed = _parse_framework_protocol(stdout, batch) if framework else _parse_protocol(stdout, batch)
            except C1StaticAnalysisError as exc:
                raise StaticAnalysisError("JDK parser returned an invalid protocol") from exc
            if stderr.strip():
                raise StaticAnalysisError("JDK Java framework parser produced unexpected diagnostics" if framework
                                          else "JDK Java parser produced unexpected diagnostics")
            if framework:
                return {item["path"]: parsed[index] for index, item in enumerate(batch)}
            return parsed


def rebuild_v12_bundle(
    project_index, coverage, stack_profile, phase3a_evidence,
    source_reader, regular_source_paths, *, g01_status, unknown_files,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild the accepted C1 pair by delegating to the existing v1.2 analyzer."""
    try:
        return analyze_java_artifacts(
            project_index, coverage, stack_profile, phase3a_evidence,
            source_reader, regular_source_paths,
            g01_status=g01_status, unknown_files=unknown_files,
        )
    except C1StaticAnalysisError as exc:
        raise StaticAnalysisError("C1_REEXTRACTION_FAILED") from exc


def compare_supplied_v12_bundle(
    rebuilt_analysis, rebuilt_evidence, supplied_analysis, supplied_evidence,
) -> None:
    """Require the supplied C1 analysis and paired evidence to match re-extraction."""
    try:
        validate_artifact(rebuilt_analysis)
        validate_artifact(rebuilt_evidence)
        validate_artifact(supplied_analysis)
        validate_artifact(supplied_evidence)
        if (dumps_artifact(rebuilt_analysis) != dumps_artifact(supplied_analysis)
                or dumps_artifact(rebuilt_evidence) != dumps_artifact(supplied_evidence)):
            raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")
    except StaticAnalysisError:
        raise
    except Exception as exc:
        raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH") from exc


def analyze_java_framework_candidates(
    project_index, coverage, stack_profile, phase3a_evidence,
    canonical_v12_analysis, canonical_v12_evidence,
    source_reader, regular_source_paths, *, g01_status, unknown_files,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rebuilt_analysis, rebuilt_evidence = rebuild_v12_bundle(
        project_index, coverage, stack_profile, phase3a_evidence,
        source_reader, regular_source_paths,
        g01_status=g01_status, unknown_files=unknown_files,
    )
    compare_supplied_v12_bundle(
        rebuilt_analysis, rebuilt_evidence, canonical_v12_analysis, canonical_v12_evidence,
    )
    return _build_framework_candidates(
        project_index, coverage, stack_profile, phase3a_evidence,
        rebuilt_analysis, rebuilt_evidence, source_reader, regular_source_paths,
        g01_status=g01_status, unknown_files=unknown_files,
    )


def _canonical_sha256(artifact: Mapping[str, Any]) -> str:
    return hashlib.sha256(dumps_artifact(artifact).encode("utf-8")).hexdigest()


def _candidate_identifier(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _metadata(project_index, coverage, g01_status, unknown_files):
    try:
        return _source_metadata(project_index, coverage, g01_status, unknown_files)
    except (KeyError, TypeError, ValueError, StackDetectionError) as exc:
        raise StaticAnalysisError("Phase 2 source metadata cannot be verified") from exc


def _model_annotation(fqn: str) -> bool:
    return fqn in {
        "javax.persistence.Entity", "javax.persistence.Embeddable",
        "javax.persistence.MappedSuperclass", "jakarta.persistence.Entity",
        "jakarta.persistence.Embeddable", "jakarta.persistence.MappedSuperclass",
    }


def _test_annotation(fqn: str) -> bool:
    return fqn in {
        "org.junit.Test", "org.junit.jupiter.api.Test",
        "org.junit.jupiter.api.RepeatedTest", "org.junit.jupiter.api.TestFactory",
        "org.junit.jupiter.params.ParameterizedTest",
    }


def _controller_annotation(fqn: str) -> bool:
    return fqn in {
        "org.springframework.stereotype.Controller",
        "org.springframework.web.bind.annotation.RestController",
    }


def _route_annotation(fqn: str) -> bool:
    return fqn in {
        "org.springframework.web.bind.annotation.RequestMapping",
        "org.springframework.web.bind.annotation.GetMapping",
        "org.springframework.web.bind.annotation.PostMapping",
        "org.springframework.web.bind.annotation.PutMapping",
        "org.springframework.web.bind.annotation.PatchMapping",
        "org.springframework.web.bind.annotation.DeleteMapping",
    }


def _fact_evidence(fact, summary: str, symbol: str) -> dict[str, Any]:
    return {
        "id": _evidence_id(fact["id"]),
        "kind": "source",
        "level": "E1",
        "summary": summary,
        "confidence": 1.0,
        "locator": {
            "path": fact["path"],
            "symbol": symbol,
            "line_start": fact["line_start"],
            "line_end": fact["line_end"],
        },
    }


def _local_to_c1_symbols(path, parsed_v12, c1_analysis, source_metadata, file_record):
    symbols_by_id = {fact["id"]: fact for fact in c1_analysis["symbols"] if fact["path"] == path}
    entry_digest = file_record["sha256"]
    revision = c1_analysis["repository_revision"]
    line_count = file_record["line_count"]
    source_length = parsed_v12["source_length"]
    module_local = f"compilation_unit:0:{source_length}"
    module_name = Path(path).stem or "source"
    module_qname = "java-source:" + Path(path).with_suffix("").as_posix()
    module_id = _symbol_id(
        revision, source_metadata, entry_digest, path, "compilation_unit", module_qname,
        1, line_count, 0, source_length,
    )
    module = symbols_by_id.get(module_id)
    if module is None or module["name"] != module_name:
        raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")
    local_map = {module_local: module}
    for parsed in parsed_v12["symbols"]:
        symbol_id = _symbol_id(
            revision, source_metadata, entry_digest, path, parsed["kind"],
            parsed["qualified_name"], parsed["line_start"], parsed["line_end"],
            parsed["start_offset"], parsed["end_offset"],
        )
        symbol = symbols_by_id.get(symbol_id)
        if symbol is None or symbol["name"] != parsed["name"]:
            raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")
        local_id = parsed["local_id"]
        if local_id in local_map:
            raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")
        local_map[local_id] = symbol
    return local_map


def _derive_framework_facts(
    project_index, coverage, stack_profile, phase3a_evidence,
    canonical_v12_analysis, canonical_v12_evidence,
    source_reader, regular_source_paths, *, g01_status, unknown_files,
):
    try:
        validate_java_analysis_bundle(
            project_index, coverage, stack_profile, phase3a_evidence,
            canonical_v12_evidence, canonical_v12_analysis,
            g01_status=g01_status, unknown_files=unknown_files,
            regular_source_paths=regular_source_paths,
        )
    except (C1StaticAnalysisError, ArtifactValidationError, KeyError, TypeError) as exc:
        raise StaticAnalysisError("canonical C1 v1.2 pair is invalid") from exc

    metadata = canonical_v12_analysis["source_metadata"]
    entries = {entry["path"]: entry for entry in project_index["files"]}
    coverage_paths = {entry["path"]: entry for entry in coverage["entries"]}
    file_records = canonical_v12_analysis["languages"][0]["files"]
    analyzed_paths = [record["path"] for record in file_records if record["status"] == "ANALYZED"]
    for path in analyzed_paths:
        entry = entries.get(path)
        if entry is None or coverage_paths.get(path) is None or entry["bytes"] > MAX_SOURCE_BYTES:
            raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")
        if path not in regular_source_paths:
            raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")

    java, javac = shutil.which("java"), shutil.which("javac")
    if analyzed_paths and not (java and javac):
        raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")

    parent_by_child: dict[str, str] = {}
    for relation in canonical_v12_analysis["relations"]:
        if relation["kind"] == "DEFINES":
            if relation["target_id"] in parent_by_child:
                raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH")
            parent_by_child[relation["target_id"]] = relation["source_id"]
    symbols_by_id = {fact["id"]: fact for fact in canonical_v12_analysis["symbols"]}

    def is_member_class(symbol_id, seen=None):
        seen = set() if seen is None else seen
        if symbol_id in seen:
            return False
        seen.add(symbol_id)
        symbol = symbols_by_id.get(symbol_id)
        if symbol is None or symbol["kind"] != "class":
            return False
        parent_id = parent_by_child.get(symbol_id)
        if parent_id is None:
            return False
        parent = symbols_by_id.get(parent_id)
        if parent is None:
            return False
        if parent["kind"] == "compilation_unit":
            return True
        if parent["kind"] == "class":
            return is_member_class(parent_id, seen)
        return False

    annotations_by_file: dict[str, list[dict[str, Any]]] = {}
    parser = None
    try:
        paths_by_size = []
        for path in analyzed_paths:
            entry = entries[path]
            if entry["bytes"] > MAX_BATCH_BYTES:
                raise StaticAnalysisError("an analyzed Java source exceeds the C1 batch limit")
            paths_by_size.append((path, entry["bytes"]))
        batches: list[list[str]] = []
        current: list[str] = []
        current_size = 0
        for path, size in paths_by_size:
            if current and (len(current) >= MAX_BATCH_FILES or current_size + size > MAX_BATCH_BYTES):
                batches.append(current)
                current, current_size = [], 0
            current.append(path)
            current_size += size
        if current:
            batches.append(current)

        for batch_paths in batches:
            candidates = []
            for path in batch_paths:
                entry = entries[path]
                try:
                    payload = _source_payload(source_reader, path, entry)
                except C1StaticAnalysisError as exc:
                    raise StaticAnalysisError("Java source changed during C2 snapshot reading") from exc
                try:
                    source_text = _decode_source(payload)
                except _SkipFile as exc:
                    raise StaticAnalysisError("C1_REEXTRACTION_MISMATCH") from exc
                candidates.append({
                    "path": path,
                    "text": source_text,
                    "digest": entry["sha256"],
                    "line_count": _line_range(source_text),
                    "source_length": len(source_text.encode("utf-16-le")) // 2,
                })
                del payload, source_text
            if parser is None:
                parser = _JavaFrameworkParserRunner(java, javac)
            parsed_v12 = parser.parse_v12_batch(candidates)
            parsed_annotations = parser.parse_batch(candidates)
            records_by_path = {record["path"]: record for record in file_records}
            for candidate in candidates:
                path = candidate["path"]
                parsed = parsed_v12.get(next(i for i, item in enumerate(candidates) if item["path"] == path))
                annotation_result = parsed_annotations.get(path)
                if (parsed is None or annotation_result is None
                        or annotation_result["status"] != "OK"
                        or parsed["line_count"] != candidate["line_count"]
                        or parsed["source_length"] != candidate["source_length"]
                        or annotation_result["line_count"] != candidate["line_count"]
                        or annotation_result["source_length"] != candidate["source_length"]
                        or records_by_path[path]["status"] != "ANALYZED"):
                    raise StaticAnalysisError("PDJ2 reparse does not match the analyzed C1 source")
                local_map = _local_to_c1_symbols(
                    path, parsed, canonical_v12_analysis, metadata, records_by_path[path],
                )
                for annotation in annotation_result["annotations"]:
                    target = local_map.get(annotation["target_local_id"])
                    owner = local_map.get(annotation["owner_local_id"])
                    if (target is None or owner is None
                            or parent_by_child.get(target["id"]) != owner["id"]
                            or not (target["line_start"] <= annotation["line"] <= target["line_end"])):
                        raise StaticAnalysisError("PDJ2 annotation is not bound to its C1 declaration")
                    _, target_start_text, target_end_text = annotation["target_local_id"].rsplit(":", 2)
                    target_start, target_end = int(target_start_text), int(target_end_text)
                    if not (target_start <= annotation["start_offset"] < annotation["end_offset"] <= target_end):
                        raise StaticAnalysisError("PDJ2 annotation lies outside its declaration")
                    annotations_by_file.setdefault(path, []).append({
                        **annotation,
                        "target_id": target["id"],
                        "owner_id": owner["id"],
                    })
            del candidates, parsed_annotations, parsed_v12
    finally:
        if parser is not None:
            parser.close()

    controller_ids = set()
    for rows in annotations_by_file.values():
        for annotation in rows:
            target = symbols_by_id[annotation["target_id"]]
            if (_controller_annotation(annotation["annotation_fqn"])
                    and is_member_class(target["id"])):
                controller_ids.add(target["id"])

    revision = canonical_v12_analysis["repository_revision"]
    source_metadata = canonical_v12_analysis["source_metadata"]
    relations = []
    roles = []
    additions = []
    for path in sorted(annotations_by_file):
        for annotation in annotations_by_file[path]:
            fqn = annotation["annotation_fqn"]
            target = symbols_by_id[annotation["target_id"]]
            owner = symbols_by_id[annotation["owner_id"]]
            line = annotation["line"]
            digest = entries[path]["sha256"]
            if (_route_annotation(fqn) and target["kind"] == "method"
                    and owner["kind"] == "class" and owner["id"] in controller_ids):
                unresolved = "spring-route-declaration:" + fqn
                identifier = _candidate_identifier(
                    "REL", revision, source_metadata, digest, "ROUTE_TO", target["id"],
                    unresolved, path, line, annotation["start_offset"], annotation["end_offset"],
                )
                fact = {
                    "id": identifier, "language": "java", "kind": "ROUTE_TO",
                    "source_id": target["id"], "target_id": None,
                    "unresolved_target": unresolved, "path": path,
                    "extraction_method": "jdk-javac-parse-framework-candidate",
                    "certainty": "CANDIDATE", "line_start": line, "line_end": line,
                    "evidence_ids": [_evidence_id(identifier)],
                }
                relations.append(fact)
                additions.append(_fact_evidence(
                    fact, f"Java method has allowlisted route annotation {fqn}.", unresolved,
                ))
            elif _model_annotation(fqn) and target["kind"] == "class" and is_member_class(target["id"]):
                role_kind = "data_model_candidate"
                identifier = _candidate_identifier(
                    "ROLE", revision, source_metadata, digest, role_kind,
                    target["id"], path, fqn, line, annotation["start_offset"], annotation["end_offset"],
                )
                fact = {
                    "id": identifier, "language": "java", "kind": role_kind,
                    "symbol_id": target["id"], "path": path,
                    "extraction_method": "jdk-javac-parse-framework-candidate",
                    "certainty": "CANDIDATE", "line_start": line, "line_end": line,
                    "evidence_ids": [_evidence_id(identifier)],
                }
                roles.append(fact)
                additions.append(_fact_evidence(
                    fact, f"Java declaration has allowlisted data-model annotation {fqn}.",
                    target["qualified_name"],
                ))
            elif (_test_annotation(fqn) and target["kind"] == "method"
                    and owner["kind"] == "class" and is_member_class(owner["id"])):
                role_kind = "test_candidate"
                identifier = _candidate_identifier(
                    "ROLE", revision, source_metadata, digest, role_kind,
                    target["id"], path, fqn, line, annotation["start_offset"], annotation["end_offset"],
                )
                fact = {
                    "id": identifier, "language": "java", "kind": role_kind,
                    "symbol_id": target["id"], "path": path,
                    "extraction_method": "jdk-javac-parse-framework-candidate",
                    "certainty": "CANDIDATE", "line_start": line, "line_end": line,
                    "evidence_ids": [_evidence_id(identifier)],
                }
                roles.append(fact)
                additions.append(_fact_evidence(
                    fact, f"Java method has allowlisted test annotation {fqn}.",
                    target["qualified_name"],
                ))
    return relations, roles, additions


def _build_framework_candidates(
    project_index, coverage, stack_profile, phase3a_evidence,
    canonical_v12_analysis, canonical_v12_evidence,
    source_reader, regular_source_paths, *, g01_status, unknown_files,
):
    relations, roles, additions = _derive_framework_facts(
        project_index, coverage, stack_profile, phase3a_evidence,
        canonical_v12_analysis, canonical_v12_evidence,
        source_reader, regular_source_paths,
        g01_status=g01_status, unknown_files=unknown_files,
    )
    source_metadata = canonical_v12_analysis["source_metadata"]
    analysis = copy.deepcopy(canonical_v12_analysis)
    analysis["schema_version"] = "1.3.0"
    analysis["stack_profile_sha256"] = _canonical_sha256(stack_profile)
    analysis["phase3a_evidence_sha256"] = _canonical_sha256(phase3a_evidence)
    analysis["java_analysis_v12_sha256"] = _canonical_sha256(canonical_v12_analysis)
    analysis["java_evidence_v12_sha256"] = _canonical_sha256(canonical_v12_evidence)
    analysis["relations"] = [*canonical_v12_analysis["relations"], *sorted(relations, key=lambda row: row["id"])]
    analysis["roles"] = sorted(roles, key=lambda row: row["id"])
    evidence = copy.deepcopy(canonical_v12_evidence)
    evidence["items"] = sorted([*canonical_v12_evidence["items"], *additions], key=lambda row: row["id"])
    validate_java_framework_bundle(
        project_index, coverage, stack_profile, phase3a_evidence,
        canonical_v12_analysis, canonical_v12_evidence,
        analysis, evidence, source_reader,
        g01_status=g01_status, unknown_files=unknown_files,
        regular_source_paths=regular_source_paths,
    )
    return analysis, evidence


def validate_java_framework_bundle(
    project_index, coverage, stack_profile, phase3a_evidence,
    canonical_v12_analysis, canonical_v12_evidence,
    framework_analysis, final_evidence, source_reader, *, g01_status,
    unknown_files, regular_source_paths,
) -> None:
    try:
        validate_java_analysis_bundle(
            project_index, coverage, stack_profile, phase3a_evidence,
            canonical_v12_evidence, canonical_v12_analysis,
            g01_status=g01_status, unknown_files=unknown_files,
            regular_source_paths=regular_source_paths,
        )
        validate_artifact(framework_analysis)
        validate_artifact(final_evidence)
    except (C1StaticAnalysisError, ArtifactValidationError, KeyError, TypeError) as exc:
        raise StaticAnalysisError("Java framework analysis/evidence schema or C1 pair is invalid") from exc
    if framework_analysis["schema_version"] != "1.3.0":
        raise StaticAnalysisError("Java framework analysis must use static-analysis v1.3.0")
    if (framework_analysis["repository_revision"] != canonical_v12_analysis["repository_revision"]
            or framework_analysis["generated_at"] != canonical_v12_analysis["generated_at"]
            or framework_analysis["source_metadata"] != canonical_v12_analysis["source_metadata"]):
        raise StaticAnalysisError("Java framework analysis identity or source metadata is inconsistent")
    expected_digests = {
        "stack_profile_sha256": _canonical_sha256(stack_profile),
        "phase3a_evidence_sha256": _canonical_sha256(phase3a_evidence),
        "java_analysis_v12_sha256": _canonical_sha256(canonical_v12_analysis),
        "java_evidence_v12_sha256": _canonical_sha256(canonical_v12_evidence),
    }
    if any(framework_analysis.get(key) != value for key, value in expected_digests.items()):
        raise StaticAnalysisError("Java framework input digest mismatch")
    if (framework_analysis["analysis_status"] != canonical_v12_analysis["analysis_status"]
            or framework_analysis["languages"] != canonical_v12_analysis["languages"]
            or framework_analysis["symbols"] != canonical_v12_analysis["symbols"]):
        raise StaticAnalysisError("Java framework analysis modified C1 facts")
    expected_metadata = _metadata(project_index, coverage, g01_status, unknown_files)
    if (final_evidence["schema_version"] != "1.1.0"
            or final_evidence["repository_revision"] != canonical_v12_analysis["repository_revision"]
            or final_evidence["generated_at"] != canonical_v12_analysis["generated_at"]
            or final_evidence["source_metadata"] != expected_metadata):
        raise StaticAnalysisError("Java framework evidence identity or source metadata is inconsistent")

    relations, roles, additions = _derive_framework_facts(
        project_index, coverage, stack_profile, phase3a_evidence,
        canonical_v12_analysis, canonical_v12_evidence,
        source_reader, regular_source_paths,
        g01_status=g01_status, unknown_files=unknown_files,
    )
    expected_relations = [
        *canonical_v12_analysis["relations"], *sorted(relations, key=lambda row: row["id"]),
    ]
    if (framework_analysis["relations"] != expected_relations
            or framework_analysis["roles"] != sorted(roles, key=lambda row: row["id"])):
        raise StaticAnalysisError("Java framework candidates do not match same-snapshot annotations")
    expected_evidence = sorted(
        [*canonical_v12_evidence["items"], *additions], key=lambda row: row["id"],
    )
    if final_evidence["items"] != expected_evidence:
        raise StaticAnalysisError("Java framework E1 evidence does not match candidate facts")
    fact_ids = [
        fact["id"] for fact in framework_analysis["symbols"]
        + framework_analysis["relations"] + framework_analysis["roles"]
    ]
    evidence_ids = [item["id"] for item in final_evidence["items"]]
    if len(fact_ids) != len(set(fact_ids)) or len(evidence_ids) != len(set(evidence_ids)):
        raise StaticAnalysisError("Java framework bundle contains duplicate IDs")

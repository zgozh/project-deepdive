#!/usr/bin/env python3
"""Bounded, parse-only Java syntax facts for Phase 3C1."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from file_classification import normalize_artifact_path
from stack_detection import StackDetectionError, _source_metadata, validate_evidence_references


MAX_SOURCE_BYTES = 1024 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_BATCH_FILES = 16
MAX_PROTOCOL_BYTES = 64 * 1024 * 1024
MAX_BRIDGE_COMPILE_OUTPUT_BYTES = 1024 * 1024
MAX_FACTS_PER_FILE = 50_000
LANGUAGE = "java"
EXTRACTION_METHOD = "jdk-javac-parse"
BRIDGE_SOURCE = Path(__file__).resolve().parent / "java_parse_bridge" / "DeepDiveJavaParser.java"
_EXCLUDED_CLASSIFICATIONS = {"UNKNOWN", "VENDOR", "GENERATED", "IGNORED_WITH_REASON"}
_SKIP_REASONS = {
    "UNKNOWN_CLASSIFICATION": "Phase 2 did not determine whether this Java file is in scope.",
    "EXCLUDED_CLASSIFICATION": "Phase 2 classified this Java file outside source analysis.",
    "NOT_REGULAR_SOURCE": "The authoritative Git snapshot entry is not a regular file.",
    "BINARY_SOURCE": "The indexed Java source is marked or detected as binary.",
    "SOURCE_TOO_LARGE": "The Java source exceeds the 1 MiB parser input limit.",
    "UNSUPPORTED_ENCODING": "The Java source is not valid UTF-8 text.",
    "NO_JDK": "A JDK compiler and Java runtime are required for parse-only Java analysis.",
    "UNSUPPORTED_SYNTAX": "The installed JDK parser rejected this Java source syntax.",
    "AST_TOO_LARGE": "The parsed Java syntax tree exceeds the 50,000-node limit.",
    "PARSER_LIMIT": "The Java parser exceeded a safe fact or source-position bound for this file.",
}
_FILE_LIMITATIONS = {
    "OMITTED_ANONYMOUS_CLASS_MEMBERS": "Anonymous-class bodies are parsed but their unscoped members are not emitted in this slice.",
}
_RELATION_KINDS = {"DEFINES", "IMPORTS", "EXTENDS", "IMPLEMENTS"}
_SYMBOL_KINDS = {
    "compilation_unit", "package", "class", "interface", "enum", "record",
    "annotation", "method", "constructor",
}
_BRIDGE_RELATION_KINDS = {"EXTENDS", "IMPLEMENTS"}
_LANGUAGE_LIMITATION = {
    "code": "JAVA_PARSE_ONLY",
    "reason": "JDK parse-only syntax facts are emitted; types remain unresolved and no code is compiled, loaded or executed.",
}


class StaticAnalysisError(ValueError):
    """A Java static-analysis input or cross-artifact invariant failed."""


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _evidence_id(fact_id: str) -> str:
    return "EVID-JAVA-" + hashlib.sha256(fact_id.encode("ascii")).hexdigest()[:32]


def _metadata(project_index: Mapping[str, Any], coverage: Mapping[str, Any],
              g01_status: str, unknown_files: int) -> dict[str, Any]:
    try:
        return _source_metadata(project_index, coverage, g01_status, unknown_files)
    except (KeyError, TypeError, ValueError, StackDetectionError) as exc:
        raise StaticAnalysisError("Phase 2 source metadata cannot be verified") from exc


def _validate_inputs(project_index, coverage, stack_profile, evidence,
                     g01_status, unknown_files):
    for label, artifact, kind in (
        ("project-index", project_index, "project-index"),
        ("coverage", coverage, "coverage"),
        ("stack-profile", stack_profile, "stack-profile"),
        ("evidence", evidence, "evidence"),
    ):
        try:
            validate_artifact(artifact)
        except (ArtifactValidationError, KeyError, TypeError) as exc:
            raise StaticAnalysisError(f"{label} artifact failed schema validation") from exc
        if artifact.get("artifact_kind") != kind:
            raise StaticAnalysisError(f"{label} artifact has the wrong artifact_kind")
    if project_index["repository_revision"] != coverage["repository_revision"]:
        raise StaticAnalysisError("Phase 2 revisions do not agree")
    if project_index["generated_at"] != coverage["generated_at"]:
        raise StaticAnalysisError("Phase 2 timestamps do not agree")
    if stack_profile["schema_version"] != "1.1.0" or evidence["schema_version"] != "1.1.0":
        raise StaticAnalysisError("Java 3C1 requires Phase 3A v1.1 stack/evidence inputs")
    expected_metadata = _metadata(project_index, coverage, g01_status, unknown_files)
    try:
        validate_evidence_references(
            stack_profile, evidence, expected_source_metadata=expected_metadata,
        )
    except (StackDetectionError, ArtifactValidationError, KeyError, TypeError) as exc:
        raise StaticAnalysisError("Phase 3A stack/evidence provenance or references are invalid") from exc
    if (stack_profile["repository_revision"] != project_index["repository_revision"]
            or stack_profile["generated_at"] != evidence["generated_at"]):
        raise StaticAnalysisError("Phase 3A and Phase 2 revision/timestamp do not agree")

    files_by_path = {}
    for item in project_index["files"]:
        try:
            path = normalize_artifact_path(item["path"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StaticAnalysisError("Phase 2 contains an unsafe indexed path") from exc
        if path in files_by_path:
            raise StaticAnalysisError("Phase 2 project-index contains duplicate paths")
        files_by_path[path] = item
    coverage_by_path = {}
    for item in coverage["entries"]:
        try:
            path = normalize_artifact_path(item["path"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StaticAnalysisError("Phase 2 contains an unsafe coverage path") from exc
        if path in coverage_by_path:
            raise StaticAnalysisError("Phase 2 coverage contains duplicate paths")
        coverage_by_path[path] = item
    if set(files_by_path) != set(coverage_by_path):
        raise StaticAnalysisError("Phase 2 index and coverage paths do not match")
    return expected_metadata, files_by_path, coverage_by_path


def _line_count(text: str) -> int:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return max(1, normalized.count("\n") + (0 if normalized.endswith("\n") else 1))


def _decode_source(payload: bytes) -> str:
    if b"\x00" in payload:
        raise _SkipFile("BINARY_SOURCE")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeError:
        raise _SkipFile("UNSUPPORTED_ENCODING") from None


class _SkipFile(Exception):
    def __init__(self, code: str):
        self.code = code


def _encode_value(value: str, label: str) -> str:
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
        text = raw.decode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise StaticAnalysisError(f"Java parser returned invalid encoded {label}") from exc
    if not text or len(raw) > 4096 or "\x00" in text:
        raise StaticAnalysisError(f"Java parser returned unsafe {label}")
    return text


def _integer(value: str, label: str, *, minimum: int = 0) -> int:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", value):
        raise StaticAnalysisError(f"Java parser returned an invalid {label}")
    number = int(value)
    if number < minimum:
        raise StaticAnalysisError(f"Java parser returned an invalid {label}")
    return number


def _parse_protocol(stdout: str, files: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    if len(stdout.encode("utf-8", "replace")) > MAX_PROTOCOL_BYTES:
        raise StaticAnalysisError("Java parser protocol exceeded its output limit")
    lines = stdout.splitlines()
    if not lines or lines[0] != "PDJ1":
        raise StaticAnalysisError("Java parser returned an invalid protocol header")
    parsed: dict[int, dict[str, Any]] = {}
    allowed_file_status = {
        "OK", "OMITTED_ANONYMOUS_CLASS_MEMBERS", "UNSUPPORTED_SYNTAX", "AST_TOO_LARGE", "PARSER_LIMIT",
    }
    for line in lines[1:]:
        if not line:
            continue
        fields = line.split("\t")
        row = fields[0]
        if row == "FILE":
            if len(fields) != 5:
                raise StaticAnalysisError("Java parser returned an invalid file record")
            index = _integer(fields[1], "file index")
            if index >= len(files) or index in parsed or fields[2] not in allowed_file_status:
                raise StaticAnalysisError("Java parser returned an unexpected file record")
            line_count = _integer(fields[3], "file line count", minimum=1)
            source_length = _integer(fields[4], "source length")
            parsed[index] = {
                "status": fields[2], "line_count": line_count,
                "source_length": source_length, "symbols": [], "relations": [],
            }
            continue
        if row not in {"SYMBOL", "IMPORT", "TYPE"} or len(fields) < 2:
            raise StaticAnalysisError("Java parser returned an unknown fact record")
        index = _integer(fields[1], "fact file index")
        if (index >= len(files) or index not in parsed
                or parsed[index]["status"] not in {"OK", "OMITTED_ANONYMOUS_CLASS_MEMBERS"}):
            raise StaticAnalysisError("Java parser returned a fact for a skipped file")
        current = parsed[index]
        if len(current["symbols"]) + len(current["relations"]) >= MAX_FACTS_PER_FILE:
            raise StaticAnalysisError("Java parser returned too many facts for one file")
        if row == "SYMBOL":
            if len(fields) != 11:
                raise StaticAnalysisError("Java parser returned an invalid symbol record")
            kind = fields[2]
            if kind not in _SYMBOL_KINDS - {"compilation_unit"}:
                raise StaticAnalysisError("Java parser returned an unsupported symbol kind")
            current["symbols"].append({
                "kind": kind,
                "name": _encode_value(fields[3], "symbol name"),
                "qualified_name": _encode_value(fields[4], "qualified name"),
                "line_start": _integer(fields[5], "symbol start line", minimum=1),
                "line_end": _integer(fields[6], "symbol end line", minimum=1),
                "start_offset": _integer(fields[7], "symbol start offset"),
                "end_offset": _integer(fields[8], "symbol end offset"),
                "local_id": _encode_value(fields[9], "local symbol ID"),
                "parent_local_id": _encode_value(fields[10], "parent symbol ID"),
            })
        elif row == "IMPORT":
            if len(fields) != 7:
                raise StaticAnalysisError("Java parser returned an invalid import record")
            current["relations"].append({
                "kind": "IMPORTS",
                "unresolved_target": _encode_value(fields[6], "import target"),
                "line_start": _integer(fields[2], "import start line", minimum=1),
                "line_end": _integer(fields[3], "import end line", minimum=1),
                "start_offset": _integer(fields[4], "import start offset"),
                "end_offset": _integer(fields[5], "import end offset"),
                "source_local_id": "compilation_unit:0:" + str(current["source_length"]),
            })
        else:
            if len(fields) != 10 or fields[2] not in _BRIDGE_RELATION_KINDS:
                raise StaticAnalysisError("Java parser returned an invalid type relation")
            current["relations"].append({
                "kind": fields[2],
                "source_local_id": _encode_value(fields[3], "relation source ID"),
                "unresolved_target": _encode_value(fields[4], "type target"),
                "line_start": _integer(fields[5], "relation start line", minimum=1),
                "line_end": _integer(fields[6], "relation end line", minimum=1),
                "start_offset": _integer(fields[7], "relation start offset"),
                "end_offset": _integer(fields[8], "relation end offset"),
                "source_name": _encode_value(fields[9], "relation source name"),
            })
    if set(parsed) != set(range(len(files))):
        raise StaticAnalysisError("Java parser omitted a submitted file record")
    return parsed


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        process.kill()
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_bounded_process(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    max_output_bytes: int,
    operation: str,
) -> tuple[int, str, str]:
    """Drain both pipes concurrently while retaining no more than the shared byte cap."""
    if max_output_bytes <= 0:
        raise ValueError("subprocess output limit must be positive")
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        raise StaticAnalysisError(f"{operation} could not be started") from exc

    output_lock = threading.Lock()
    output_limit_reached = threading.Event()
    captured = {"stdout": [], "stderr": []}
    captured_bytes = 0
    reader_errors: list[OSError] = []

    def drain(stream, channel):
        nonlocal captured_bytes
        try:
            while not output_limit_reached.is_set():
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                with output_lock:
                    if output_limit_reached.is_set():
                        return
                    available = max_output_bytes - captured_bytes
                    retained = min(len(chunk), max(0, available))
                    if retained:
                        captured[channel].append(chunk[:retained])
                        captured_bytes += retained
                    if captured_bytes >= max_output_bytes:
                        output_limit_reached.set()
        except OSError as exc:
            if process.poll() is None and not output_limit_reached.is_set():
                reader_errors.append(exc)

    readers = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    while True:
        if output_limit_reached.is_set():
            _stop_process(process)
            break
        return_code = process.poll()
        if return_code is not None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _stop_process(process)
            break
        output_limit_reached.wait(min(remaining, 0.05))

    return_code = process.wait()
    for reader in readers:
        reader.join()
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()

    if timed_out:
        raise StaticAnalysisError(f"{operation} exceeded its time limit")
    if output_limit_reached.is_set():
        raise StaticAnalysisError(f"{operation} exceeded its output limit")
    if reader_errors:
        raise StaticAnalysisError(f"{operation} output could not be read") from reader_errors[0]
    stdout = b"".join(captured["stdout"]).decode("utf-8", errors="replace")
    stderr = b"".join(captured["stderr"]).decode("utf-8", errors="replace")
    return return_code, stdout, stderr


class _JavaParserRunner:
    """Compile the checked-in bridge once and parse one bounded source batch at a time."""

    def __init__(self, java: str, javac: str):
        self.java = java
        self.environment = os.environ.copy()
        for key in (
            "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS",
            "JDK_JAVAC_OPTIONS", "CLASSPATH",
        ):
            self.environment.pop(key, None)
        self.temporary = tempfile.TemporaryDirectory(prefix="project-deepdive-java-")
        self.root = Path(self.temporary.name)
        self.classes = self.root / "classes"
        self.classes.mkdir()
        try:
            return_code, _stdout, _stderr = _run_bounded_process(
                [javac, "-proc:none", "-encoding", "UTF-8", "-d", str(self.classes), str(BRIDGE_SOURCE)],
                cwd=self.root,
                env=self.environment,
                timeout=30,
                max_output_bytes=MAX_BRIDGE_COMPILE_OUTPUT_BYTES,
                operation="JDK parse bridge compilation",
            )
            if return_code != 0:
                raise StaticAnalysisError("JDK parse bridge compilation failed")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.temporary.cleanup()

    def parse_batch(self, batch: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="batch-", dir=self.root) as temporary:
            batch_root = Path(temporary)
            parser_files: list[Path] = []
            for index, item in enumerate(batch):
                parser_file = batch_root / f"unit-{index}.java"
                parser_file.write_text(item["text"], encoding="utf-8", newline="\n")
                parser_files.append(parser_file)
            command = [self.java, "-Xms16m", "-Xmx256m", "-cp", str(self.classes),
                       "DeepDiveJavaParser", *(str(path) for path in parser_files)]
            return_code, stdout, stderr = _run_bounded_process(
                command,
                cwd=batch_root,
                env=self.environment,
                timeout=30,
                max_output_bytes=MAX_PROTOCOL_BYTES,
                operation="JDK Java parser",
            )
            if return_code != 0:
                raise StaticAnalysisError("JDK parse bridge failed")
            parsed = _parse_protocol(stdout, batch)
            if stderr.strip():
                # Never relay diagnostics: javac can include source text in them.
                raise StaticAnalysisError("JDK parse bridge produced unexpected diagnostics")
            return {item["path"]: parsed[index] for index, item in enumerate(batch)}


def _fact_summary(fact: Mapping[str, Any]) -> str:
    return f"Java source contains {fact['kind']} syntax."


def _fact_locator(fact: Mapping[str, Any]) -> str:
    if "qualified_name" in fact:
        return fact["qualified_name"]
    return fact.get("unresolved_target") or fact["kind"]


def _build_evidence(fact: Mapping[str, Any]) -> dict[str, Any]:
    evidence_id = _evidence_id(fact["id"])
    return {
        "id": evidence_id,
        "level": "E1",
        "kind": "source",
        "summary": _fact_summary(fact),
        "confidence": 1.0,
        "locator": {
            "path": fact["path"],
            "symbol": _fact_locator(fact),
            "line_start": fact["line_start"],
            "line_end": fact["line_end"],
        },
    }


def _line_range(text: str) -> int:
    return _line_count(text)


def _parse_kind(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() == ".java"


def _iter_candidate_path_batches(
    java_paths: list[str],
    files_by_path: Mapping[str, Mapping[str, Any]],
    coverage_by_path: Mapping[str, Mapping[str, Any]],
    regular_source_paths: frozenset[str],
    has_jdk: bool,
    records: dict[str, dict[str, Any]],
):
    """Yield only path metadata; source bytes are read after each batch is selected."""
    batch: list[str] = []
    batch_bytes = 0
    for path in java_paths:
        entry = files_by_path[path]
        coverage_entry = coverage_by_path[path]
        digest = entry["sha256"]
        try:
            normalize_artifact_path(path)
        except ValueError as exc:
            raise StaticAnalysisError("Phase 2 contains an unsafe Java path") from exc

        skip_code = None
        classification = coverage_entry["classification"]
        if classification in _EXCLUDED_CLASSIFICATIONS:
            skip_code = "UNKNOWN_CLASSIFICATION" if classification == "UNKNOWN" else "EXCLUDED_CLASSIFICATION"
        elif entry.get("content_kind") in {"binary", "symlink", "gitlink"}:
            skip_code = "BINARY_SOURCE"
        elif path not in regular_source_paths:
            skip_code = "NOT_REGULAR_SOURCE"
        elif entry["bytes"] > MAX_SOURCE_BYTES:
            skip_code = "SOURCE_TOO_LARGE"
        elif not has_jdk:
            skip_code = "NO_JDK"
        elif entry["bytes"] > MAX_BATCH_BYTES:
            raise StaticAnalysisError("a Java source exceeds the parser batch byte limit")

        if skip_code:
            records[path] = _skip_record(path, digest, skip_code)
            continue

        size = entry["bytes"]
        if batch and (len(batch) >= MAX_BATCH_FILES or batch_bytes + size > MAX_BATCH_BYTES):
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(path)
        batch_bytes += size
        if len(batch) >= MAX_BATCH_FILES or batch_bytes >= MAX_BATCH_BYTES:
            yield batch
            batch = []
            batch_bytes = 0
    if batch:
        yield batch


def _source_payload(source_reader, path: str, entry: Mapping[str, Any]) -> bytes:
    try:
        payload = source_reader(path, entry)
    except (OSError, ValueError) as exc:
        raise StaticAnalysisError("cannot read a Java source from the declared Git snapshot") from exc
    if not isinstance(payload, bytes):
        raise StaticAnalysisError("Java source reader returned non-byte data")
    if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise StaticAnalysisError("Java source bytes do not match the Phase 2 snapshot")
    return payload


def _skip_record(path: str, digest: str, code: str) -> dict[str, Any]:
    return {
        "path": path,
        "sha256": digest,
        "status": "SKIPPED",
        "limitations": [{"code": code, "reason": _SKIP_REASONS[code]}],
    }


def _symbol_id(revision, source_metadata, digest, path, kind, qualified_name,
               line_start, line_end, start_offset, end_offset) -> str:
    return _stable_id(
        "SYM", revision, source_metadata, digest, LANGUAGE, kind, path,
        qualified_name, line_start, line_end, start_offset, end_offset,
    )


def _relation_id(revision, source_metadata, digest, kind, source_id, target_id,
                 unresolved_target, path, line_start, line_end, start_offset, end_offset) -> str:
    return _stable_id(
        "REL", revision, source_metadata, digest, LANGUAGE, kind, source_id,
        target_id, unresolved_target, path, line_start, line_end, start_offset, end_offset,
    )


def analyze_java_artifacts(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    source_reader: Callable[[str, Mapping[str, Any]], bytes],
    regular_source_paths: frozenset[str],
    *,
    g01_status: str,
    unknown_files: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a v1.2 Java bundle from byte-verified files in an audited snapshot."""
    source_metadata, files_by_path, coverage_by_path = _validate_inputs(
        project_index, coverage, stack_profile, evidence, g01_status, unknown_files,
    )
    revision = project_index["repository_revision"]
    generated_at = stack_profile["generated_at"]
    original_evidence = {item["id"]: item for item in evidence["items"]}
    if len(original_evidence) != len(evidence["items"]):
        raise StaticAnalysisError("Phase 3A evidence contains duplicate IDs")

    java_paths = sorted(path for path in files_by_path if _parse_kind(path))
    records: dict[str, dict[str, Any]] = {}
    java, javac = shutil.which("java"), shutil.which("javac")

    symbols: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    new_evidence: dict[str, dict[str, Any]] = {}

    def add_symbol(path, digest, kind, name, qualified_name, line_start, line_end,
                   start_offset, end_offset, local_id):
        identifier = _symbol_id(
            revision, source_metadata, digest, path, kind, qualified_name,
            line_start, line_end, start_offset, end_offset,
        )
        fact = {
            "id": identifier,
            "language": LANGUAGE,
            "kind": kind,
            "name": name,
            "qualified_name": qualified_name,
            "path": path,
            "extraction_method": EXTRACTION_METHOD,
            "certainty": "VERIFIED",
            "line_start": line_start,
            "line_end": line_end,
            "evidence_ids": [_evidence_id(identifier)],
        }
        symbols.append(fact)
        citation = _build_evidence(fact)
        if citation["id"] in original_evidence or citation["id"] in new_evidence:
            raise StaticAnalysisError("deterministic Java symbol/evidence ID collision")
        new_evidence[citation["id"]] = citation
        local_to_global[local_id] = identifier
        return fact

    def add_relation(path, digest, kind, source_id, unresolved_target, line_start,
                     line_end, start_offset, end_offset, target_id=None):
        certainty = "VERIFIED" if kind == "DEFINES" else "UNRESOLVED"
        identifier = _relation_id(
            revision, source_metadata, digest, kind, source_id, target_id,
            unresolved_target, path, line_start, line_end, start_offset, end_offset,
        )
        fact = {
            "id": identifier,
            "language": LANGUAGE,
            "kind": kind,
            "source_id": source_id,
            "target_id": target_id,
            "unresolved_target": unresolved_target,
            "path": path,
            "extraction_method": EXTRACTION_METHOD,
            "certainty": certainty,
            "line_start": line_start,
            "line_end": line_end,
            "evidence_ids": [_evidence_id(identifier)],
        }
        relations.append(fact)
        citation = _build_evidence(fact)
        if citation["id"] in original_evidence or citation["id"] in new_evidence:
            raise StaticAnalysisError("deterministic Java relation/evidence ID collision")
        new_evidence[citation["id"]] = citation
        return fact

    parser: _JavaParserRunner | None = None
    try:
        path_batches = _iter_candidate_path_batches(
            java_paths, files_by_path, coverage_by_path, regular_source_paths,
            bool(java and javac), records,
        )
        for batch_paths in path_batches:
            candidates: list[dict[str, Any]] = []
            for path in batch_paths:
                entry = files_by_path[path]
                digest = entry["sha256"]
                payload = _source_payload(source_reader, path, entry)
                try:
                    source_text = _decode_source(payload)
                except _SkipFile as exc:
                    records[path] = _skip_record(path, digest, exc.code)
                    del payload
                    continue
                candidates.append({
                    "path": path,
                    "digest": digest,
                    "text": source_text,
                    "line_count": _line_range(source_text),
                    "source_length": len(source_text.encode("utf-16-le")) // 2,
                })
                del source_text, payload

            if not candidates:
                del batch_paths, candidates
                continue
            if parser is None:
                parser = _JavaParserRunner(java, javac)
            parsed_by_path = parser.parse_batch(candidates)
            for candidate in candidates:
                path = candidate["path"]
                digest = candidate["digest"]
                parsed = parsed_by_path.get(path)
                if parsed is None:
                    raise StaticAnalysisError("Java parser did not return an indexed source")
                if parsed["line_count"] != candidate["line_count"] or parsed["source_length"] != candidate["source_length"]:
                    raise StaticAnalysisError("Java parser returned a mismatched source extent")
                if parsed["status"] in {"UNSUPPORTED_SYNTAX", "AST_TOO_LARGE", "PARSER_LIMIT"}:
                    records[path] = _skip_record(path, digest, parsed["status"])
                    continue
                records[path] = {
                    "path": path,
                    "sha256": digest,
                    "status": "ANALYZED",
                    "line_count": candidate["line_count"],
                    "limitations": ([{
                        "code": parsed["status"],
                        "reason": _FILE_LIMITATIONS[parsed["status"]],
                    }] if parsed["status"] in _FILE_LIMITATIONS else []),
                }
                local_to_global: dict[str, str] = {}
                module_local = f"compilation_unit:0:{parsed['source_length']}"
                module_name = PurePosixPath(path).stem or "source"
                module_qualified_name = "java-source:" + PurePosixPath(path).with_suffix("").as_posix()
                module = add_symbol(
                    path, digest, "compilation_unit", module_name, module_qualified_name,
                    1, candidate["line_count"], 0, parsed["source_length"], module_local,
                )
                # Package/type/method rows are ordered by source traversal. Resolve parent
                # references only after creating their deterministic symbol IDs.
                pending_definitions = []
                for parsed_symbol in parsed["symbols"]:
                    parent_local = parsed_symbol["parent_local_id"]
                    if parent_local not in local_to_global:
                        if parent_local != module_local:
                            raise StaticAnalysisError("Java parser returned a declaration with an unknown owner")
                        parent_id = module["id"]
                    else:
                        parent_id = local_to_global[parent_local]
                    fact = add_symbol(
                        path, digest, parsed_symbol["kind"], parsed_symbol["name"],
                        parsed_symbol["qualified_name"], parsed_symbol["line_start"],
                        parsed_symbol["line_end"], parsed_symbol["start_offset"],
                        parsed_symbol["end_offset"], parsed_symbol["local_id"],
                    )
                    pending_definitions.append((parent_id, fact))
                # Every declared package/type/member gets one evidence-backed DEFINES edge.
                for parent_id, child in pending_definitions:
                    add_relation(
                        path, digest, "DEFINES", parent_id, None, child["line_start"],
                        child["line_end"], 0, 0, target_id=child["id"],
                    )
                for parsed_relation in parsed["relations"]:
                    source_id = local_to_global.get(parsed_relation["source_local_id"])
                    if source_id is None:
                        raise StaticAnalysisError("Java parser returned a relation with an unknown source")
                    add_relation(
                        path, digest, parsed_relation["kind"], source_id,
                        parsed_relation["unresolved_target"], parsed_relation["line_start"],
                        parsed_relation["line_end"], parsed_relation["start_offset"],
                        parsed_relation["end_offset"],
                    )
            del candidate, parsed_by_path, candidates, batch_paths
    finally:
        if parser is not None:
            parser.close()

    files = [records[path] for path in java_paths]
    partial_files = any(item["status"] == "SKIPPED" or item["limitations"] for item in files)
    analysis_status = "PARTIAL" if partial_files or g01_status == "PARTIAL" else "PASS"
    static_analysis = {
        "artifact_kind": "static-analysis",
        "schema_version": "1.2.0",
        "repository_revision": revision,
        "generated_at": generated_at,
        "source_metadata": source_metadata,
        "analysis_status": analysis_status,
        "languages": [{
            "language": LANGUAGE,
            "analysis_status": analysis_status,
            "limitations": [_LANGUAGE_LIMITATION],
            "files": files,
        }],
        "symbols": symbols,
        "relations": relations,
        "roles": [],
    }
    merged_evidence = {
        "artifact_kind": "evidence",
        "schema_version": evidence["schema_version"],
        "repository_revision": revision,
        "generated_at": generated_at,
        "source_metadata": source_metadata,
        "items": sorted(
            [*evidence["items"], *new_evidence.values()], key=lambda item: item["id"],
        ),
    }
    validate_java_analysis_bundle(
        project_index, coverage, stack_profile, evidence, merged_evidence,
        static_analysis, g01_status=g01_status, unknown_files=unknown_files,
        regular_source_paths=regular_source_paths,
    )
    return static_analysis, merged_evidence


def validate_java_analysis_bundle(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    original_evidence: Mapping[str, Any],
    merged_evidence: Mapping[str, Any],
    analysis: Mapping[str, Any],
    *,
    g01_status: str,
    unknown_files: int,
    regular_source_paths: frozenset[str] | None = None,
) -> None:
    """Validate v1.2 facts against Phase 2 paths and matching Phase 3A evidence."""
    metadata, files_by_path, coverage_by_path = _validate_inputs(
        project_index, coverage, stack_profile, original_evidence,
        g01_status, unknown_files,
    )
    try:
        validate_artifact(analysis)
        validate_artifact(merged_evidence)
    except (ArtifactValidationError, KeyError, TypeError) as exc:
        raise StaticAnalysisError("Java analysis/evidence artifact failed schema validation") from exc
    revision = project_index["repository_revision"]
    timestamp = stack_profile["generated_at"]
    if (analysis["schema_version"] != "1.2.0"
            or analysis["repository_revision"] != revision
            or analysis["generated_at"] != timestamp
            or analysis["source_metadata"] != metadata):
        raise StaticAnalysisError("Java analysis identity or source metadata is inconsistent")
    if (merged_evidence["schema_version"] != original_evidence["schema_version"]
            or merged_evidence["repository_revision"] != revision
            or merged_evidence["generated_at"] != timestamp
            or merged_evidence["source_metadata"] != metadata):
        raise StaticAnalysisError("merged Java evidence identity or source metadata is inconsistent")
    try:
        validate_evidence_references(
            stack_profile, merged_evidence, expected_source_metadata=metadata,
        )
    except (StackDetectionError, ArtifactValidationError, KeyError, TypeError) as exc:
        raise StaticAnalysisError("merged evidence failed Phase 3A semantic validation") from exc
    original_by_id = {item["id"]: item for item in original_evidence["items"]}
    merged_by_id = {item["id"]: item for item in merged_evidence["items"]}
    if len(original_by_id) != len(original_evidence["items"]):
        raise StaticAnalysisError("original Phase 3A evidence contains duplicate IDs")
    if len(merged_by_id) != len(merged_evidence["items"]):
        raise StaticAnalysisError("merged evidence contains duplicate IDs")
    if any(merged_by_id.get(key) != value for key, value in original_by_id.items()):
        raise StaticAnalysisError("merged evidence modified or removed a Phase 3A evidence record")

    expected_paths = {path for path in files_by_path if _parse_kind(path)}
    language_records = analysis["languages"]
    if len(language_records) != 1 or language_records[0]["language"] != LANGUAGE:
        raise StaticAnalysisError("v1.2 Java output must contain exactly one Java language record")
    if language_records[0]["limitations"] != [_LANGUAGE_LIMITATION]:
        raise StaticAnalysisError("Java language limitations do not match the parse-only contract")
    if analysis["roles"] != []:
        raise StaticAnalysisError("Phase 3C1 Java v1.2 does not emit roles")
    file_records = language_records[0]["files"]
    if [item["path"] for item in file_records] != sorted(expected_paths):
        raise StaticAnalysisError("Java file records do not match the indexed Java path set")
    record_by_path = {item["path"]: item for item in file_records}
    for path, record in record_by_path.items():
        entry = files_by_path[path]
        if record["sha256"] != entry["sha256"]:
            raise StaticAnalysisError("Java file record digest differs from Phase 2")
        expected_skip = None
        if coverage_by_path[path]["classification"] in _EXCLUDED_CLASSIFICATIONS:
            expected_skip = (
                "UNKNOWN_CLASSIFICATION"
                if coverage_by_path[path]["classification"] == "UNKNOWN"
                else "EXCLUDED_CLASSIFICATION"
            )
        elif entry.get("content_kind") in {"binary", "symlink", "gitlink"}:
            expected_skip = "BINARY_SOURCE"
        elif regular_source_paths is not None and path not in regular_source_paths:
            expected_skip = "NOT_REGULAR_SOURCE"
        elif entry["bytes"] > MAX_SOURCE_BYTES:
            expected_skip = "SOURCE_TOO_LARGE"
        if expected_skip and (record["status"] != "SKIPPED"
                              or record["limitations"][0]["code"] != expected_skip):
            raise StaticAnalysisError("Java file status does not match Phase 2 eligibility")
        if record["status"] == "ANALYZED":
            if "line_count" not in record:
                raise StaticAnalysisError("analyzed Java file is missing its line count")
            if any(item["code"] not in _FILE_LIMITATIONS for item in record["limitations"]):
                raise StaticAnalysisError("analyzed Java file has an unsupported limitation")
        elif len(record["limitations"]) != 1 or record["limitations"][0]["code"] not in _SKIP_REASONS:
            raise StaticAnalysisError("skipped Java file has an invalid fixed reason")

    symbols_by_id = {}
    identifiers = set()
    for fact in analysis["symbols"]:
        if (fact["language"] != LANGUAGE or fact["kind"] not in _SYMBOL_KINDS
                or fact["extraction_method"] != EXTRACTION_METHOD):
            raise StaticAnalysisError("Java symbol has incorrect language or extraction provenance")
        if fact["id"] in identifiers:
            raise StaticAnalysisError("Java symbol/relation IDs are not unique")
        identifiers.add(fact["id"])
        record = record_by_path.get(fact["path"])
        if (record is None or record["status"] != "ANALYZED"
                or not (1 <= fact["line_start"] <= fact["line_end"] <= record["line_count"])):
            raise StaticAnalysisError("Java symbol path or one-based source span is invalid")
        symbols_by_id[fact["id"]] = fact
    for path, record in record_by_path.items():
        if sum(
            item["path"] == path and item["kind"] == "package"
            for item in analysis["symbols"]
        ) > 1:
            raise StaticAnalysisError("Java source unit has duplicate package symbols")
        compilation_units = [
            item for item in analysis["symbols"]
            if item["path"] == path and item["kind"] == "compilation_unit"
        ]
        if (record["status"] == "ANALYZED" and len(compilation_units) != 1
                or record["status"] == "SKIPPED" and compilation_units):
            raise StaticAnalysisError("Java compilation-unit symbol count does not match file status")

    facts = analysis["symbols"] + analysis["relations"]
    referenced_new_evidence = set()
    for fact in facts:
        for evidence_id in fact["evidence_ids"]:
            citation = merged_by_id.get(evidence_id)
            if citation is None:
                raise StaticAnalysisError("Java fact has dangling evidence")
            if evidence_id in original_by_id:
                raise StaticAnalysisError("new Java fact cannot reuse Phase 3A evidence")
            referenced_new_evidence.add(evidence_id)
            if (citation["level"] != "E1" or citation["kind"] != "source"
                    or citation["summary"] != _fact_summary(fact)
                    or citation["locator"] != {
                        "path": fact["path"],
                        "symbol": _fact_locator(fact),
                        "line_start": fact["line_start"],
                        "line_end": fact["line_end"],
                    }):
                raise StaticAnalysisError("Java fact evidence does not match its source location and kind")
            if evidence_id != _evidence_id(fact["id"]):
                raise StaticAnalysisError("Java fact evidence ID is not bound to the fact")
    added_evidence = set(merged_by_id) - set(original_by_id)
    if added_evidence != referenced_new_evidence:
        raise StaticAnalysisError("merged evidence has unreferenced Java records")

    defined_targets = {}
    package_names_by_path = {
        path: [item["qualified_name"] for item in analysis["symbols"]
               if item["path"] == path and item["kind"] == "package"]
        for path in expected_paths
    }
    for relation in analysis["relations"]:
        if relation["language"] != LANGUAGE or relation["extraction_method"] != EXTRACTION_METHOD:
            raise StaticAnalysisError("Java relation has incorrect language or extraction provenance")
        if relation["id"] in identifiers:
            raise StaticAnalysisError("Java symbol/relation IDs are not unique")
        identifiers.add(relation["id"])
        source = symbols_by_id.get(relation["source_id"])
        target = symbols_by_id.get(relation["target_id"]) if relation["target_id"] else None
        record = record_by_path.get(relation["path"])
        if (relation["kind"] not in _RELATION_KINDS or source is None
                or source["path"] != relation["path"] or record is None
                or record["status"] != "ANALYZED"
                or not (1 <= relation["line_start"] <= relation["line_end"] <= record["line_count"])
                or not (source["line_start"] <= relation["line_start"]
                        <= relation["line_end"] <= source["line_end"])):
            raise StaticAnalysisError("Java relation source, path or one-based source span is invalid")
        if relation["kind"] == "DEFINES":
            if (target is None or relation["unresolved_target"] is not None
                    or target["path"] != relation["path"]
                    or not (source["line_start"] <= target["line_start"]
                            <= target["line_end"] <= source["line_end"])
                    or relation["line_start"] != target["line_start"]
                    or relation["line_end"] != target["line_end"]
                    or relation["certainty"] != "VERIFIED"):
                raise StaticAnalysisError("Java DEFINES relation has an invalid source/target scope")
            if target["kind"] == "compilation_unit":
                raise StaticAnalysisError("Java compilation-unit symbols cannot be defined by another symbol")
            if source["kind"] == "compilation_unit":
                if target["kind"] not in {"package", "class", "interface", "enum", "record", "annotation"}:
                    raise StaticAnalysisError("Java compilation unit cannot directly define this declaration kind")
                packages = package_names_by_path[relation["path"]]
                if target["kind"] == "package":
                    if packages != [target["qualified_name"]]:
                        raise StaticAnalysisError("Java package declaration does not match the source unit")
                else:
                    prefix = packages[0] + "." if packages else ""
                    tail = target["qualified_name"][len(prefix):]
                    if (len(packages) > 1
                            or (packages and not target["qualified_name"].startswith(prefix))
                            or "." in tail):
                        raise StaticAnalysisError("Java compilation unit defines a non-top-level declaration")
            elif not target["qualified_name"].startswith(source["qualified_name"] + "."):
                raise StaticAnalysisError("Java DEFINES relation does not match its lexical owner")
            defined_targets[target["id"]] = defined_targets.get(target["id"], 0) + 1
        else:
            if (relation["target_id"] is not None or not relation["unresolved_target"]
                    or relation["certainty"] != "UNRESOLVED"):
                raise StaticAnalysisError("Java reference relation must keep an explicit unresolved target")
            if relation["kind"] == "IMPORTS":
                if (source["kind"] != "compilation_unit"
                        or not relation["unresolved_target"].startswith(("java-import:", "java-static-import:"))):
                    raise StaticAnalysisError("Java import relation has an invalid owner or target")
            elif (source["kind"] not in {"class", "interface", "enum", "record"}
                    or not relation["unresolved_target"].startswith("java-type:")):
                raise StaticAnalysisError("Java inheritance relation has an invalid owner or target")
    if any(
        symbol["kind"] != "compilation_unit" and defined_targets.get(symbol["id"], 0) != 1
        for symbol in analysis["symbols"]
    ):
        raise StaticAnalysisError("each Java declaration must have exactly one lexical DEFINES relation")
    expected_status = (
        "PARTIAL" if g01_status == "PARTIAL"
        or any(record["status"] == "SKIPPED" or record["limitations"] for record in file_records)
        else "PASS"
    )
    if (analysis["analysis_status"] != expected_status
            or language_records[0]["analysis_status"] != expected_status):
        raise StaticAnalysisError("Java analysis status does not match G01 and per-file states")

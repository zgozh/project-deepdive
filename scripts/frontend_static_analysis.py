#!/usr/bin/env python3
"""Parse explicitly supplied JavaScript/TypeScript files with a pinned local TypeScript parser."""

from __future__ import annotations

import base64
from bisect import bisect_right
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence


PARSER_VERSION = "6.0.3"
PROTOCOL = "PDJS1"
_PROTOCOL_BY_VERSION = {1: "PDJS1", 2: "PDJS2"}
MAX_FILES = 32
MAX_FILE_BYTES = 1 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
PARSER_TIMEOUT_SECONDS = 20.0
NODE_HEAP_MIB = 512
_ALLOWED_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
_SCRIPT_KINDS = {".js": "JS", ".jsx": "JSX", ".ts": "TS", ".tsx": "TSX"}
_SENSITIVE_LABEL = re.compile(
    r"(?:secret|token|password|passwd|credential|authorization|cookie|api[_-]?key)",
    re.IGNORECASE,
)
_FIXED_ERRORS = {
    "ROOT_INVALID": "analysis root is invalid",
    "EMPTY_INPUT": "at least one explicit source path is required",
    "TOO_MANY_FILES": "explicit source file count exceeds the limit",
    "UNSUPPORTED_EXTENSION": "source file extension is not supported",
    "DUPLICATE_INPUT": "explicit source paths contain a duplicate",
    "PATH_ESCAPE": "source path escapes the analysis root",
    "PATH_SYMLINK": "symbolic-link source paths are not accepted",
    "SOURCE_NOT_REGULAR": "source path is not a regular file",
    "SOURCE_FILE_TOO_LARGE": "source file exceeds the byte limit",
    "SOURCE_TOTAL_TOO_LARGE": "source set exceeds the aggregate byte limit",
    "SOURCE_NOT_UTF8": "source file is not valid UTF-8",
    "REQUEST_TOO_LARGE": "parser request exceeds the byte limit",
    "NODE_NOT_FOUND": "Node.js is unavailable",
    "PARSER_DEPENDENCY_MISSING": "tool-local TypeScript parser dependency is unavailable",
    "PARSER_START_FAILED": "frontend parser process could not be started",
    "PARSER_TIMEOUT": "frontend parser process exceeded the time limit",
    "PARSER_OUTPUT_LIMIT": "frontend parser process exceeded the output limit",
    "PARSER_INPUT_FAILED": "frontend parser request could not be sent",
    "PARSER_FAILED": "frontend parser process failed",
    "PARSER_PROTOCOL_ERROR": "frontend parser returned an invalid response",
}


class FrontendAnalysisError(Exception):
    """Fixed-code failure that does not include source text or child diagnostics."""

    def __init__(self, code: str):
        self.code = code if code in _FIXED_ERRORS else "PARSER_PROTOCOL_ERROR"
        super().__init__(self.code)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_bounded_process(
    command: Sequence[str],
    *,
    input_bytes: bytes,
    cwd: Path,
    timeout_seconds: float,
    max_output_bytes: int,
) -> tuple[int, bytes, bytes]:
    """Write bounded stdin and drain both output streams under a shared hard cap."""
    if timeout_seconds <= 0 or max_output_bytes <= 0:
        raise ValueError("timeout and output limits must be positive")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=_parser_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        raise FrontendAnalysisError("PARSER_START_FAILED") from exc

    lock = threading.Lock()
    output_exceeded = threading.Event()
    retained = {"stdout": bytearray(), "stderr": bytearray()}
    retained_bytes = 0
    reader_errors: list[OSError] = []
    writer_errors: list[OSError] = []

    def drain(stream, channel: str) -> None:
        nonlocal retained_bytes
        try:
            while not output_exceeded.is_set():
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                overflow = False
                with lock:
                    available = max_output_bytes - retained_bytes
                    keep = min(len(chunk), max(0, available))
                    if keep:
                        retained[channel].extend(chunk[:keep])
                        retained_bytes += keep
                    # Hitting the configured ceiling is terminal. Continuing to
                    # drain at exactly the cap would let a silent child run until
                    # its timeout despite having exhausted the output budget.
                    overflow = len(chunk) >= available
                    if overflow:
                        output_exceeded.set()
                if overflow:
                    _stop_process(process)
                    return
        except OSError as exc:
            if process.poll() is None and not output_exceeded.is_set():
                reader_errors.append(exc)

    def write_request() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(input_bytes)
            process.stdin.flush()
        except OSError as exc:
            if process.poll() is None and not output_exceeded.is_set():
                writer_errors.append(exc)
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass

    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
    ]
    writer = threading.Thread(target=write_request, daemon=True)
    for thread in readers:
        thread.start()
    writer.start()

    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_process(process)
        return_code = process.wait()
    writer.join()
    for thread in readers:
        thread.join()
    process.stdout.close()
    process.stderr.close()

    if output_exceeded.is_set():
        raise FrontendAnalysisError("PARSER_OUTPUT_LIMIT")
    if timed_out:
        raise FrontendAnalysisError("PARSER_TIMEOUT")
    if writer_errors:
        raise FrontendAnalysisError("PARSER_INPUT_FAILED") from writer_errors[0]
    if reader_errors:
        raise FrontendAnalysisError("PARSER_FAILED") from reader_errors[0]
    return return_code, bytes(retained["stdout"]), bytes(retained["stderr"])


def _parser_environment() -> dict[str, str]:
    allowed = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["NODE_OPTIONS"] = ""
    environment["NODE_PATH"] = ""
    return environment


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _resolve_explicit_paths(root: Path, paths: Sequence[str]) -> list[tuple[str, Path]]:
    if not paths:
        raise FrontendAnalysisError("EMPTY_INPUT")
    if len(paths) > MAX_FILES:
        raise FrontendAnalysisError("TOO_MANY_FILES")
    try:
        root_real = root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FrontendAnalysisError("ROOT_INVALID") from exc
    if not root_real.is_dir():
        raise FrontendAnalysisError("ROOT_INVALID")

    resolved: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw_value in paths:
        raw = str(raw_value)
        if not raw or "\x00" in raw:
            raise FrontendAnalysisError("PATH_ESCAPE")
        normalized = raw.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(raw)
        if posix.is_absolute() or windows.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
            raise FrontendAnalysisError("PATH_ESCAPE")
        suffix = PurePosixPath(normalized).suffix.lower()
        if suffix not in _ALLOWED_EXTENSIONS:
            raise FrontendAnalysisError("UNSUPPORTED_EXTENSION")
        key = normalized.casefold()
        if key in seen:
            raise FrontendAnalysisError("DUPLICATE_INPUT")
        seen.add(key)

        candidate = root_real.joinpath(*posix.parts)
        cursor = root_real
        for part in posix.parts:
            cursor = cursor / part
            if _is_link(cursor):
                raise FrontendAnalysisError("PATH_SYMLINK")
        try:
            actual = candidate.resolve(strict=True)
            actual.relative_to(root_real)
        except (OSError, RuntimeError, ValueError) as exc:
            raise FrontendAnalysisError("PATH_ESCAPE") from exc
        try:
            mode = actual.stat().st_mode
        except OSError as exc:
            raise FrontendAnalysisError("SOURCE_NOT_REGULAR") from exc
        if not stat.S_ISREG(mode):
            raise FrontendAnalysisError("SOURCE_NOT_REGULAR")
        relative = actual.relative_to(root_real).as_posix()
        resolved.append((relative, actual))
    return sorted(resolved, key=lambda pair: pair[0].casefold())


def _read_sources(root: Path, paths: Sequence[str]) -> tuple[list[dict[str, str]], dict[str, str]]:
    resolved = _resolve_explicit_paths(root, paths)
    source_rows: list[dict[str, str]] = []
    source_texts: dict[str, str] = {}
    total_bytes = 0
    for relative, path in resolved:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise FrontendAnalysisError("SOURCE_NOT_REGULAR") from exc
        if size > MAX_FILE_BYTES:
            raise FrontendAnalysisError("SOURCE_FILE_TOO_LARGE")
        remaining = MAX_TOTAL_BYTES - total_bytes
        if size > remaining:
            raise FrontendAnalysisError("SOURCE_TOTAL_TOO_LARGE")
        try:
            with path.open("rb") as source_file:
                source_bytes = source_file.read(min(MAX_FILE_BYTES, remaining) + 1)
        except OSError as exc:
            raise FrontendAnalysisError("SOURCE_NOT_REGULAR") from exc
        if len(source_bytes) > MAX_FILE_BYTES:
            raise FrontendAnalysisError("SOURCE_FILE_TOO_LARGE")
        if len(source_bytes) > remaining:
            raise FrontendAnalysisError("SOURCE_TOTAL_TOO_LARGE")
        total_bytes += len(source_bytes)
        try:
            source_text = source_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise FrontendAnalysisError("SOURCE_NOT_UTF8") from exc
        digest = hashlib.sha256(source_bytes).hexdigest()
        source_texts[relative] = source_text
        source_rows.append({
            "path": relative,
            "sha256": digest,
            "source_base64": base64.b64encode(source_bytes).decode("ascii"),
        })
    return source_rows, source_texts


def _line_starts_utf16(source_text: str) -> tuple[list[int], int]:
    encoded = source_text.encode("utf-16-le")
    unit_count = len(encoded) // 2
    starts = [0]
    position = 0
    while position < unit_count:
        value = encoded[position * 2] | (encoded[position * 2 + 1] << 8)
        if value == 0x0D:
            position += 1
            if (
                position < unit_count
                and encoded[position * 2] == 0x0A
                and encoded[position * 2 + 1] == 0
            ):
                position += 1
            starts.append(position)
        elif value in {0x0A, 0x2028, 0x2029}:
            position += 1
            starts.append(position)
        else:
            position += 1
    return starts, unit_count


def _line_column(starts: Sequence[int], offset: int) -> tuple[int, int]:
    line_index = bisect_right(starts, offset) - 1
    return line_index + 1, offset - starts[line_index] + 1


def _valid_location(location: Any, line_starts: Sequence[int], source_length_utf16: int) -> bool:
    if not isinstance(location, dict) or set(location) != {
        "start_line", "start_column", "end_line", "end_column", "start_offset", "end_offset",
    }:
        return False
    if any(type(value) is not int for value in location.values()):
        return False
    start_offset = location["start_offset"]
    end_offset = location["end_offset"]
    if not 0 <= start_offset < end_offset <= source_length_utf16:
        return False
    return (
        (location["start_line"], location["start_column"]) == _line_column(line_starts, start_offset)
        and (location["end_line"], location["end_column"]) == _line_column(line_starts, end_offset)
    )


_COMMON_FACT_FIELDS = {"id", "kind", "location", "certainty"}
_FACT_FIELDS = {
    "IMPORT": _COMMON_FACT_FIELDS | {"form", "specifier_state", "target_state"},
    "EXPORT": _COMMON_FACT_FIELDS | {"form", "specifier_state", "target_state"},
    "SYMBOL_FUNCTION": _COMMON_FACT_FIELDS | {"name", "function_kind", "exported"},
    "SYMBOL_CLASS": _COMMON_FACT_FIELDS | {"name", "exported"},
    "COMPONENT_CANDIDATE": _COMMON_FACT_FIELDS | {"name", "basis"},
    "HOOK_CANDIDATE": _COMMON_FACT_FIELDS | {"name", "basis"},
    "API_CALL_CANDIDATE": _COMMON_FACT_FIELDS | {"callee", "target_state", "basis"},
}
_FACT_FIELDS_V2 = {
    **_FACT_FIELDS,
    "STORE_DECLARATION_CANDIDATE": _COMMON_FACT_FIELDS | {"name", "exported", "basis"},
}
_FUNCTION_KINDS = {"arrow", "expression", "method", "constructor", "accessor", "declaration"}
_COMPONENT_BASES = {"uppercase_function_with_jsx", "uppercase_class_with_jsx_render"}
_API_CALLEE_BASES = {
    "globalThis.fetch": "global_fetch_call_syntax",
    "axios.get": "axios_import_call_syntax",
    "axios.post": "axios_import_call_syntax",
    "axios.put": "axios_import_call_syntax",
    "axios.patch": "axios_import_call_syntax",
    "axios.delete": "axios_import_call_syntax",
}
_API_CALLEE_BASES_V2 = {
    **_API_CALLEE_BASES,
    "fetch": "bare_fetch_call_syntax",
}


def _fact_id(path: str, source_digest: str, fact: Mapping[str, Any]) -> str:
    location = fact["location"]
    seed = "\0".join((
        path,
        source_digest,
        fact["kind"],
        str(location["start_offset"]),
        str(location["end_offset"]),
    ))
    return "FE_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _validate_fact(
    fact: Any,
    path: str,
    source_digest: str,
    line_starts: Sequence[int],
    source_length_utf16: int,
    protocol_version: int = 1,
) -> bool:
    if not isinstance(fact, dict):
        return False
    kind = fact.get("kind")
    allowed_fact_fields = _FACT_FIELDS_V2 if protocol_version == 2 else _FACT_FIELDS
    if (
        not isinstance(kind, str)
        or kind not in allowed_fact_fields
        or set(fact) != allowed_fact_fields[kind]
    ):
        return False
    expected_certainty = "CANDIDATE" if kind.endswith("_CANDIDATE") else "SYNTAX"
    if fact["certainty"] != expected_certainty:
        return False
    if not isinstance(fact["location"], dict) or not _valid_location(
        fact["location"], line_starts, source_length_utf16
    ):
        return False
    if fact["id"] != _fact_id(path, source_digest, fact):
        return False
    if "name" in fact and (
        not isinstance(fact["name"], str)
        or _SENSITIVE_LABEL.search(fact["name"])
        or len(fact["name"]) > 256
    ):
        return False
    if kind in {"IMPORT", "EXPORT"}:
        form = fact["form"]
        state = fact["specifier_state"]
        if not isinstance(form, str) or not isinstance(state, str):
            return False
        if fact["target_state"] != "UNRESOLVED":
            return False
        if kind == "IMPORT":
            if form not in {"static", "dynamic"} or state not in {"REDACTED", "UNRESOLVED"}:
                return False
            if form == "static" and state != "REDACTED":
                return False
        elif form == "reexport":
            if state != "REDACTED":
                return False
        elif form == "local":
            if state != "UNRESOLVED":
                return False
        elif form in {"equals", "default"}:
            if state != "NOT_APPLICABLE":
                return False
        else:
            return False
    if kind == "SYMBOL_FUNCTION":
        if (
            not isinstance(fact["function_kind"], str)
            or fact["function_kind"] not in _FUNCTION_KINDS
            or type(fact["exported"]) is not bool
        ):
            return False
    if kind == "SYMBOL_CLASS" and type(fact["exported"]) is not bool:
        return False
    if kind == "STORE_DECLARATION_CANDIDATE" and (
        not isinstance(fact["name"], str)
        or type(fact["exported"]) is not bool
        or fact["basis"] != "zustand_create_import_call"
    ):
        return False
    if kind == "COMPONENT_CANDIDATE" and (
        not isinstance(fact["basis"], str) or fact["basis"] not in _COMPONENT_BASES
    ):
        return False
    if kind == "HOOK_CANDIDATE" and fact["basis"] != "function_name_convention":
        return False
    if kind == "API_CALL_CANDIDATE":
        callee = fact["callee"]
        api_bases = _API_CALLEE_BASES_V2 if protocol_version == 2 else _API_CALLEE_BASES
        if not isinstance(callee, str) or callee not in api_bases:
            return False
        if fact["target_state"] != "UNRESOLVED" or fact["basis"] != api_bases[callee]:
            return False
    return True


class _DuplicateJSONKeyError(ValueError):
    pass


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError
        result[key] = value
    return result


def _validate_response(
    payload: bytes,
    stderr: bytes,
    return_code: int,
    sources: list[dict[str, str]],
    source_texts: Mapping[str, str],
    protocol_version: int = 1,
) -> list[dict[str, Any]]:
    if return_code != 0 or stderr:
        raise FrontendAnalysisError("PARSER_FAILED")
    try:
        response = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKeyError) as exc:
        raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR") from exc
    expected_paths = [row["path"] for row in sources]
    expected_protocol = _PROTOCOL_BY_VERSION.get(protocol_version)
    if expected_protocol is None:
        raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
    if (
        not isinstance(response, dict)
        or set(response) != {"protocol", "parser_version", "files"}
        or response.get("protocol") != expected_protocol
        or response.get("parser_version") != PARSER_VERSION
        or not isinstance(response.get("files"), list)
        or len(response["files"]) != len(sources)
    ):
        raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")

    validated: list[dict[str, Any]] = []
    for item, source in zip(response["files"], sources, strict=True):
        path = source["path"]
        text = source_texts[path]
        line_starts, utf16_length = _line_starts_utf16(text)
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "script_kind", "status", "facts", "diagnostics"}
            or item.get("path") != path
            or item.get("sha256") != source["sha256"]
            or item.get("script_kind") != _SCRIPT_KINDS[Path(path).suffix.lower()]
            or not isinstance(item.get("status"), str)
            or item.get("status") not in {"ANALYZED", "PARTIAL"}
            or not isinstance(item.get("facts"), list)
            or not isinstance(item.get("diagnostics"), list)
        ):
            raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
        if item["status"] == "PARTIAL":
            if item["facts"] or not item["diagnostics"]:
                raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
            for diagnostic in item["diagnostics"]:
                if (
                    not isinstance(diagnostic, dict)
                    or set(diagnostic) != {"code", "line", "column"}
                    or diagnostic.get("code") != "SYNTAX_ERROR"
                    or type(diagnostic.get("line")) is not int
                    or type(diagnostic.get("column")) is not int
                    or diagnostic["line"] < 1
                    or diagnostic["column"] < 1
                ):
                    raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
        elif item["diagnostics"]:
            raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
        if any(
            not _validate_fact(
                fact, path, source["sha256"], line_starts, utf16_length,
                protocol_version=protocol_version,
            )
            for fact in item["facts"]
        ):
            raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
        if len({fact["id"] for fact in item["facts"]}) != len(item["facts"]):
            raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
        validated.append(item)
    if [item["path"] for item in validated] != expected_paths:
        raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
    return validated


def _parser_command() -> tuple[list[str], Path]:
    node = shutil.which("node")
    if not node:
        raise FrontendAnalysisError("NODE_NOT_FOUND")
    bridge_dir = Path(__file__).resolve().parent / "frontend_parse_bridge"
    bridge_script = bridge_dir / "parse_frontend.mjs"
    dependency = bridge_dir / "node_modules" / "typescript" / "package.json"
    if not bridge_script.is_file() or not dependency.is_file():
        raise FrontendAnalysisError("PARSER_DEPENDENCY_MISSING")
    return [node, f"--max-old-space-size={NODE_HEAP_MIB}", str(bridge_script)], bridge_dir


def _analyze_source_rows(
    source_rows: list[dict[str, str]],
    source_texts: Mapping[str, str],
    protocol_version: int = 1,
) -> list[dict[str, Any]]:
    protocol = _PROTOCOL_BY_VERSION.get(protocol_version)
    if protocol is None:
        raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
    request = json.dumps(
        {"protocol": protocol, "sources": source_rows},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if len(request) > MAX_REQUEST_BYTES:
        raise FrontendAnalysisError("REQUEST_TOO_LARGE")
    command, bridge_dir = _parser_command()
    return_code, stdout, stderr = _run_bounded_process(
        command,
        input_bytes=request,
        cwd=bridge_dir,
        timeout_seconds=PARSER_TIMEOUT_SECONDS,
        max_output_bytes=MAX_PROCESS_OUTPUT_BYTES,
    )
    return _validate_response(
        stdout, stderr, return_code, source_rows, source_texts,
        protocol_version=protocol_version,
    )


def _report(files: list[dict[str, Any]], protocol_version: int = 1) -> dict[str, object]:
    if protocol_version not in _PROTOCOL_BY_VERSION:
        raise FrontendAnalysisError("PARSER_PROTOCOL_ERROR")
    return {
        "report_version": f"frontend-parse-report/{protocol_version}",
        "parser": {"name": "TypeScript", "version": PARSER_VERSION, "mode": "createSourceFile"},
        "files": files,
        "limits": {
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_source_bytes": MAX_TOTAL_BYTES,
            "parser_timeout_seconds": PARSER_TIMEOUT_SECONDS,
            "parser_combined_output_bytes": MAX_PROCESS_OUTPUT_BYTES,
        },
    }


def analyze_paths(
    root: Path,
    paths: Sequence[str],
    *,
    protocol_version: int = 1,
) -> dict[str, object]:
    """Analyze only explicit JavaScript/TypeScript source files under root."""
    source_rows, source_texts = _read_sources(Path(root), paths)
    return _report(_analyze_source_rows(source_rows, source_texts, protocol_version), protocol_version)


def analyze_sources(
    sources: Mapping[str, bytes],
    *,
    protocol_version: int = 1,
) -> dict[str, object]:
    """Analyze bounded caller-supplied snapshot bytes without opening target paths."""
    if not isinstance(sources, Mapping) or not sources:
        raise FrontendAnalysisError("EMPTY_INPUT")
    if len(sources) > MAX_FILES:
        raise FrontendAnalysisError("TOO_MANY_FILES")

    rows: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for raw_value, payload in sources.items():
        if not isinstance(raw_value, str):
            raise FrontendAnalysisError("PATH_ESCAPE")
        raw = raw_value
        if not raw or "\x00" in raw:
            raise FrontendAnalysisError("PATH_ESCAPE")
        normalized = raw.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(raw)
        if posix.is_absolute() or windows.drive or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise FrontendAnalysisError("PATH_ESCAPE")
        if posix.suffix.lower() not in _ALLOWED_EXTENSIONS:
            raise FrontendAnalysisError("UNSUPPORTED_EXTENSION")
        relative = posix.as_posix()
        key = relative.casefold()
        if key in seen:
            raise FrontendAnalysisError("DUPLICATE_INPUT")
        seen.add(key)
        if not isinstance(payload, bytes):
            raise FrontendAnalysisError("SOURCE_NOT_REGULAR")
        if len(payload) > MAX_FILE_BYTES:
            raise FrontendAnalysisError("SOURCE_FILE_TOO_LARGE")
        rows.append((relative, payload))

    source_rows: list[dict[str, str]] = []
    source_texts: dict[str, str] = {}
    total_bytes = 0
    for relative, source_bytes in sorted(rows, key=lambda item: item[0].casefold()):
        if len(source_bytes) > MAX_TOTAL_BYTES - total_bytes:
            raise FrontendAnalysisError("SOURCE_TOTAL_TOO_LARGE")
        total_bytes += len(source_bytes)
        try:
            source_texts[relative] = source_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise FrontendAnalysisError("SOURCE_NOT_UTF8") from exc
        source_rows.append({
            "path": relative,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_base64": base64.b64encode(source_bytes).decode("ascii"),
        })
    return _report(_analyze_source_rows(source_rows, source_texts, protocol_version), protocol_version)

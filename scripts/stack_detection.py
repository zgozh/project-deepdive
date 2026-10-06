#!/usr/bin/env python3
"""Declaration-only stack detection over audited Phase 2 snapshot entries."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Any

from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact

MAX_MANIFEST_BYTES = 1024 * 1024
_VERIFIED_CONFIDENCE = 0.95
_LIKELY_CONFIDENCE = 0.65
_UNKNOWN_CONFIDENCE = 0.0
_SKIP_CLASSIFICATIONS = {"GENERATED", "VENDOR", "IGNORED_WITH_REASON"}
_PYTHON_SUFFIXES = {".py", ".pyi"}
_JAVA_SUFFIXES = {".java"}
_JAVASCRIPT_SUFFIXES = {".js", ".mjs", ".cjs", ".jsx"}
_TYPESCRIPT_SUFFIXES = {".ts", ".mts", ".cts", ".tsx"}
_REQUIREMENTS_NAME = re.compile(r"requirements(?:[-_][a-z0-9_.-]+)?\.txt\Z", re.IGNORECASE)
_REQUIREMENT_LINE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]\s]+\])?"
    r"(?=\s*(?:===|==|!=|~=|<=|>=|<|>|;|#|$))"
)
_XML_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_PACKAGE_MANAGER_NAME = re.compile(r"([A-Za-z0-9_-]+)(?:@[^@\s]+)?\Z")
_RUNTIME_CLAIM = re.compile(
    r"\b(runtime|running|runs|executed|execution|installed|active|production)\b",
    re.IGNORECASE,
)

_PYTHON_PACKAGES = {
    "crewai": ("framework", "CrewAI"),
    "django": ("framework", "Django"),
    "fastapi": ("framework", "FastAPI"),
    "flask": ("framework", "Flask"),
    "langchain": ("framework", "LangChain"),
    "langgraph": ("framework", "LangGraph"),
    "openai-agents": ("framework", "OpenAI Agents SDK"),
}
_NODE_PACKAGES = {
    "@angular/core": ("frontend", "Angular"),
    "next": ("framework", "Next.js"),
    "react": ("frontend", "React"),
    "react-dom": ("frontend", "React"),
    "svelte": ("frontend", "Svelte"),
    "vite": ("build", "Vite"),
    "vitest": ("testing", "Vitest"),
    "vue": ("frontend", "Vue"),
}
_KNOWN_NODE_MANAGERS = {"bun", "npm", "pnpm", "yarn"}


class StackDetectionError(ValueError):
    """An input manifest or generated cross-artifact contract is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _evidence_id(revision: str, entry: Mapping[str, Any], path: str,
                 symbol: str, rule: str) -> str:
    identity = {
        "revision": revision,
        "sha256": entry["sha256"],
        "path": path,
        "symbol": symbol,
        "rule": rule,
    }
    return "EVID-" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()[:24]


class _ArtifactBuilder:
    def __init__(self, revision: str, generated_at: str,
                 source_metadata: Mapping[str, Any]):
        self.revision = revision
        self.generated_at = generated_at
        self.source_metadata = dict(source_metadata)
        self.detections: dict[tuple[str, str], dict[str, Any]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}

    def add(self, *, category: str, name: str, status: str, path: str,
            entry: Mapping[str, Any], symbol: str, rule: str, level: str,
            kind: str, summary: str) -> None:
        evidence_id = _evidence_id(self.revision, entry, path, symbol, rule)
        citation = {
            "id": evidence_id,
            "level": level,
            "kind": kind,
            "summary": summary,
            "confidence": _VERIFIED_CONFIDENCE if status == "VERIFIED" else _LIKELY_CONFIDENCE,
            "locator": {"path": path, "symbol": symbol},
        }
        previous = self.evidence.get(evidence_id)
        if previous is not None and previous != citation:
            raise StackDetectionError("deterministic evidence ID collision")
        self.evidence[evidence_id] = citation

        key = (category, name)
        detection = self.detections.setdefault(key, {
            "category": category,
            "name": name,
            "status": "LIKELY",
            "confidence": _LIKELY_CONFIDENCE,
            "evidence_ids": set(),
        })
        detection["evidence_ids"].add(evidence_id)
        if status == "VERIFIED":
            detection["status"] = "VERIFIED"
            detection["confidence"] = _VERIFIED_CONFIDENCE

    def documents(self) -> tuple[dict[str, Any], dict[str, Any]]:
        profile = {
            "artifact_kind": "stack-profile",
            "schema_version": "1.1.0",
            "repository_revision": self.revision,
            "generated_at": self.generated_at,
            "source_metadata": dict(self.source_metadata),
            "detections": [
                {
                    **item,
                    "evidence_ids": sorted(item["evidence_ids"]),
                }
                for _key, item in sorted(self.detections.items())
            ],
        }
        evidence = {
            "artifact_kind": "evidence",
            "schema_version": "1.1.0",
            "repository_revision": self.revision,
            "generated_at": self.generated_at,
            "source_metadata": dict(self.source_metadata),
            "items": [self.evidence[key] for key in sorted(self.evidence)],
        }
        return profile, evidence


def _is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name in {"pyproject.toml", "pom.xml", "package.json", "package-lock.json"} or bool(
        _REQUIREMENTS_NAME.fullmatch(name)
    )


def _decode_manifest(path: str, payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise StackDetectionError(f"manifest reader returned non-byte data for {path}")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise StackDetectionError(f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte size limit: {path}")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise StackDetectionError(f"manifest is not valid UTF-8 text: {path}") from None


def _strict_manifest_json(path: str, text: str) -> Any:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("non-JSON constant")

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError, TypeError):
        raise StackDetectionError(f"malformed JSON manifest: {path}") from None


def _normal_package(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _toml_key_segment(value: str) -> str:
    """Identify a dynamic TOML key without copying its text into artifacts."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"[key-sha256:{digest}]"


def _dependency_name(requirement: str) -> str | None:
    match = _REQUIREMENT_LINE.match(requirement.strip())
    return _normal_package(match.group(1)) if match else None


def _manifest_fact(builder: _ArtifactBuilder, *, category: str, name: str,
                   path: str, entry: Mapping[str, Any], symbol: str,
                   kind: str = "config", summary: str, status: str = "VERIFIED",
                   rule: str | None = None) -> None:
    builder.add(
        category=category, name=name, status=status, path=path, entry=entry,
        symbol=symbol, rule=rule or "manifest:" + symbol, level="E2", kind=kind,
        summary=summary,
    )


def _source_language_facts(builder: _ArtifactBuilder, entries: list[Mapping[str, Any]],
                           classifications: Mapping[str, str],
                           regular_source_paths: frozenset[str] | None) -> None:
    suffixes = (
        (_PYTHON_SUFFIXES, "Python"),
        (_JAVA_SUFFIXES, "Java"),
        (_JAVASCRIPT_SUFFIXES, "JavaScript"),
        (_TYPESCRIPT_SUFFIXES, "TypeScript"),
    )
    for entry in sorted(entries, key=lambda item: item["path"]):
        path = entry["path"]
        if classifications.get(path) in _SKIP_CLASSIFICATIONS:
            continue
        if regular_source_paths is not None and path not in regular_source_paths:
            continue
        if entry.get("tracked") is not True or entry.get("content_kind") in {"binary", "symlink", "gitlink"}:
            continue
        suffix = PurePosixPath(path).suffix.lower()
        for recognized, language in suffixes:
            if suffix not in recognized:
                continue
            builder.add(
                category="language", name=language, status="LIKELY", path=path,
                entry=entry, symbol=f"source-suffix:{suffix}", rule="source-suffix:" + language,
                level="E1", kind="source",
                summary=f"A tracked source path suggests {language} syntax from its {suffix} suffix.",
            )
            break


def _python_manifest(builder: _ArtifactBuilder, path: str, entry: Mapping[str, Any], text: str) -> None:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise StackDetectionError(f"malformed TOML manifest: {path}") from None
    if not isinstance(document, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")

    tool = document.get("tool", {})
    if not isinstance(tool, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")
    poetry = tool.get("poetry", {})
    if not isinstance(poetry, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")
    project = document.get("project", {})
    if not isinstance(project, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")
    build_system = document.get("build-system", {})
    if not isinstance(build_system, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")

    verified_python = bool(project or poetry)
    backend = build_system.get("build-backend")
    if backend is not None and not isinstance(backend, str):
        raise StackDetectionError(f"malformed TOML manifest: {path}")
    recognized_backends = {
        "flit_core.buildapi": ("build", "Flit"),
        "hatchling.build": ("build", "Hatch"),
        "pdm.backend": ("package_manager", "PDM"),
        "poetry.core.masonry.api": ("package_manager", "Poetry"),
        "setuptools.build_meta": ("build", "setuptools"),
        "setuptools.build_meta:__legacy__": ("build", "setuptools"),
    }
    backend_fact = recognized_backends.get(backend)
    verified_python = verified_python or backend_fact is not None
    if verified_python:
        _manifest_fact(
            builder, category="language", name="Python", path=path, entry=entry,
            symbol="project" if project or poetry else "build-system.build-backend",
            summary="The pyproject.toml manifest declares a Python project.",
        )
    elif document:
        _manifest_fact(
            builder, category="language", name="Python", path=path, entry=entry,
            symbol="pyproject.toml", summary="The pyproject.toml manifest suggests Python tooling.",
            status="LIKELY",
        )

    if backend_fact is not None:
        category, name = backend_fact
        _manifest_fact(
            builder, category=category, name=name, path=path, entry=entry,
            symbol="build-system.build-backend",
            summary=f"The pyproject.toml manifest declares {name} as its build backend.",
        )
    if poetry:
        _manifest_fact(
            builder, category="package_manager", name="Poetry", path=path, entry=entry,
            symbol="tool.poetry", summary="The pyproject.toml manifest declares Poetry project metadata.",
        )

    dependency_symbols: dict[str, set[str]] = {}

    def add_dependency(name: str, symbol: str) -> None:
        dependency_symbols.setdefault(_normal_package(name), set()).add(symbol)

    for field in ("dependencies",):
        values = project.get(field, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            if field in project:
                raise StackDetectionError(f"malformed TOML manifest: {path}")
        for index, value in enumerate(values):
            name = _dependency_name(value)
            if name:
                add_dependency(name, f"project.{field}[{index}]")
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")
    for group_name, values in optional.items():
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise StackDetectionError(f"malformed TOML manifest: {path}")
        for index, value in enumerate(values):
            name = _dependency_name(value)
            if name:
                add_dependency(
                    name, f"project.optional-dependencies{_toml_key_segment(group_name)}[{index}]",
                )

    poetry_dependencies = poetry.get("dependencies", {})
    if poetry_dependencies and not isinstance(poetry_dependencies, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")
    if isinstance(poetry_dependencies, dict):
        for name in poetry_dependencies:
            if name.lower() != "python":
                add_dependency(name, "tool.poetry.dependencies" + _toml_key_segment(name))
    poetry_groups = poetry.get("group", {})
    if not isinstance(poetry_groups, dict):
        raise StackDetectionError(f"malformed TOML manifest: {path}")
    for group_name, group in poetry_groups.items():
        if not isinstance(group, dict) or not isinstance(group.get("dependencies", {}), dict):
            raise StackDetectionError(f"malformed TOML manifest: {path}")
        for name in group.get("dependencies", {}):
            add_dependency(
                name,
                "tool.poetry.group" + _toml_key_segment(group_name)
                + ".dependencies" + _toml_key_segment(name),
            )

    for package in sorted(dependency_symbols):
        known = _PYTHON_PACKAGES.get(package)
        if known is None:
            continue
        category, name = known
        for symbol in sorted(dependency_symbols[package]):
            _manifest_fact(
                builder, category=category, name=name, path=path, entry=entry,
                symbol=symbol, kind="dependency",
                summary=f"The pyproject.toml manifest declares {name} as a direct dependency.",
            )


def _requirements_manifest(builder: _ArtifactBuilder, path: str,
                           entry: Mapping[str, Any], text: str) -> None:
    package_lines: dict[str, set[str]] = {}
    basename = PurePosixPath(path).name
    for line_number, line in enumerate(text.splitlines(), start=1):
        candidate = line.strip()
        if not candidate or candidate.startswith(("#", "-", ".", "/")):
            continue
        candidate = candidate.split("#", 1)[0].strip()
        name = _dependency_name(candidate)
        if name:
            package_lines.setdefault(name, set()).add(f"{basename}:line:{line_number}")
    for package in sorted(package_lines):
        for symbol in sorted(package_lines[package]):
            _manifest_fact(
                builder, category="language", name="Python", path=path, entry=entry,
                symbol=symbol, kind="dependency",
                summary="The requirements manifest declares a Python package.",
                rule="manifest:language:Python:" + symbol,
            )
    _manifest_fact(
        builder, category="package_manager", name="pip", path=path, entry=entry,
        symbol="requirements-format", kind="config",
        summary="The requirements manifest suggests pip-style dependency management.",
        status="LIKELY",
    )
    for package in sorted(package_lines):
        known = _PYTHON_PACKAGES.get(package)
        if known is None:
            continue
        category, name = known
        for symbol in sorted(package_lines[package]):
            _manifest_fact(
                builder, category=category, name=name, path=path, entry=entry,
                symbol=symbol, kind="dependency",
                summary=f"The requirements manifest declares {name} as a direct dependency.",
            )


def _local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_namespace(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return None


def _direct_child(element: ET.Element, name: str, namespace: str | None) -> ET.Element | None:
    return next((
        child for child in list(element)
        if _local_xml_name(child.tag) == name and _xml_namespace(child.tag) == namespace
    ), None)


def _xml_text(element: ET.Element, name: str, namespace: str | None) -> str | None:
    child = _direct_child(element, name, namespace)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _java_manifest(builder: _ArtifactBuilder, path: str, entry: Mapping[str, Any],
                   payload: bytes, text: str) -> None:
    if _XML_DECLARATION.search(payload):
        raise StackDetectionError(f"XML DTD/entity declarations are not allowed in manifest: {path}")
    try:
        project = ET.fromstring(text)
    except ET.ParseError:
        raise StackDetectionError(f"malformed XML manifest: {path}") from None
    if _local_xml_name(project.tag) != "project":
        raise StackDetectionError(f"malformed Maven manifest: {path}")
    namespace = _xml_namespace(project.tag)
    model_version = _xml_text(project, "modelVersion", namespace)
    model_namespaces = {
        "4.0.0": "http://maven.apache.org/POM/4.0.0",
        "4.1.0": "http://maven.apache.org/POM/4.1.0",
    }
    expected_namespace = model_namespaces.get(model_version)
    if expected_namespace is None or (namespace is not None and namespace != expected_namespace):
        raise StackDetectionError(f"malformed or unsupported Maven modelVersion/namespace: {path}")

    _manifest_fact(
        builder, category="build", name="Maven", path=path, entry=entry,
        symbol="project.modelVersion", summary="The pom.xml manifest declares a Maven build project.",
    )
    properties = _direct_child(project, "properties", namespace)
    java_level_property = None
    if properties is not None:
        for child in list(properties):
            name = _local_xml_name(child.tag)
            if (_xml_namespace(child.tag) == namespace
                    and name in {"maven.compiler.release", "maven.compiler.source"}
                    and (child.text or "").strip()):
                java_level_property = name
                break
    if java_level_property is not None:
        _manifest_fact(
            builder, category="language", name="Java", path=path, entry=entry,
            symbol=f"properties.{java_level_property}", kind="config",
            summary="The pom.xml manifest declares a Java compiler level.",
        )

    dependencies = _direct_child(project, "dependencies", namespace)
    if dependencies is None:
        return
    dependency_index = 0
    for dependency in list(dependencies):
        if (_local_xml_name(dependency.tag) != "dependency"
                or _xml_namespace(dependency.tag) != namespace):
            continue
        current_index = dependency_index
        dependency_index += 1
        group = _xml_text(dependency, "groupId", namespace)
        artifact = _xml_text(dependency, "artifactId", namespace)
        if group == "org.springframework.boot" and artifact and artifact.startswith("spring-boot-"):
            _manifest_fact(
                builder, category="framework", name="Spring Boot", path=path, entry=entry,
                symbol=f"project.dependencies.dependency[{current_index}]", kind="dependency",
                summary="The pom.xml manifest declares a Spring Boot dependency.",
            )
        elif group == "org.springframework.ai" and artifact and artifact.startswith("spring-ai-"):
            _manifest_fact(
                builder, category="framework", name="Spring AI", path=path, entry=entry,
                symbol=f"project.dependencies.dependency[{current_index}]", kind="dependency",
                summary="The pom.xml manifest declares a Spring AI dependency.",
            )


def _node_manifest(builder: _ArtifactBuilder, path: str, entry: Mapping[str, Any], text: str,
                   managers_by_directory: dict[str, str]) -> None:
    document = _strict_manifest_json(path, text)
    if not isinstance(document, dict):
        raise StackDetectionError(f"malformed Node manifest: {path}")
    directory = str(PurePosixPath(path).parent)
    _manifest_fact(
        builder, category="language", name="Node.js", path=path, entry=entry,
        symbol="package.json", summary="The package.json manifest declares a Node.js package.",
    )

    manager_value = document.get("packageManager")
    if manager_value is not None:
        if not isinstance(manager_value, str):
            raise StackDetectionError(f"malformed packageManager declaration: {path}")
        match = _PACKAGE_MANAGER_NAME.fullmatch(manager_value)
        if not match:
            raise StackDetectionError(f"malformed packageManager declaration: {path}")
        manager = match.group(1).lower()
        if manager in _KNOWN_NODE_MANAGERS:
            managers_by_directory[directory] = manager
            _manifest_fact(
                builder, category="package_manager", name=manager, path=path, entry=entry,
                symbol="packageManager", summary=f"The package.json manifest declares {manager} as its package manager.",
            )

    package_names: dict[str, set[str]] = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = document.get(section, {})
        if not isinstance(values, dict):
            raise StackDetectionError(f"malformed dependency section in manifest: {path}")
        for package, requirement in values.items():
            if not package or not isinstance(requirement, str) or not requirement.strip():
                raise StackDetectionError(f"malformed dependency declaration in manifest: {path}")
            package_names.setdefault(_normal_package(package), set()).add(section)
    for package in sorted(package_names):
        known = _NODE_PACKAGES.get(package)
        if known is None:
            continue
        category, name = known
        for section in sorted(package_names[package]):
            _manifest_fact(
                builder, category=category, name=name, path=path, entry=entry,
                symbol=f"package.json.{section}.{package}", kind="dependency",
                summary=f"The package.json manifest declares {name} as a direct dependency.",
            )


def _node_lockfile(builder: _ArtifactBuilder, path: str, entry: Mapping[str, Any],
                   text: str, managers_by_directory: Mapping[str, str],
                   lock_directories: set[str]) -> None:
    document = _strict_manifest_json(path, text)
    if not isinstance(document, dict):
        raise StackDetectionError(f"malformed npm lockfile: {path}")
    version = document.get("lockfileVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2, 3}:
        raise StackDetectionError(f"malformed npm lockfile: {path}")
    if version == 1:
        if not isinstance(document.get("dependencies"), dict):
            raise StackDetectionError(f"malformed npm lockfile: {path}")
    elif not isinstance(document.get("packages"), dict):
        raise StackDetectionError(f"malformed npm lockfile: {path}")
    directory = str(PurePosixPath(path).parent)
    lock_directories.add(directory)
    manager = managers_by_directory.get(directory)
    if manager is not None and manager != "npm":
        raise StackDetectionError(f"conflicting package manager declarations in {directory}")
    _manifest_fact(
        builder, category="package_manager", name="npm", path=path, entry=entry,
        symbol="lockfileVersion", kind="dependency",
        summary="The package-lock.json manifest declares the npm lockfile format.",
    )


_SOURCE_METADATA_KEYS = {
    "snapshot_kind",
    "g01_status",
    "unknown_files",
    "project_index_sha256",
    "coverage_sha256",
}


def _source_metadata(project_index: Mapping[str, Any], coverage: Mapping[str, Any],
                     g01_status: str, unknown_files: int) -> dict[str, Any]:
    coverage_unknowns = coverage["unknown_count"]
    if (not isinstance(unknown_files, int) or isinstance(unknown_files, bool)
            or unknown_files < 0 or unknown_files != coverage_unknowns):
        raise StackDetectionError("G01 unknown_files does not match Phase 2 coverage")
    expected_status = "PARTIAL" if unknown_files else "PASS"
    if g01_status != expected_status:
        raise StackDetectionError("G01 status does not match the Phase 2 unknown file count")

    try:
        project_index_bytes = dumps_artifact(project_index).encode("utf-8")
        coverage_bytes = dumps_artifact(coverage).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StackDetectionError("Phase 2 inputs cannot be normalized for source digests") from exc
    return {
        "snapshot_kind": project_index["project"]["snapshot_kind"],
        "g01_status": g01_status,
        "unknown_files": unknown_files,
        "project_index_sha256": hashlib.sha256(project_index_bytes).hexdigest(),
        "coverage_sha256": hashlib.sha256(coverage_bytes).hexdigest(),
    }


def _validate_source_metadata(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != _SOURCE_METADATA_KEYS:
        raise StackDetectionError("v1.1 source metadata is missing or has an invalid shape")
    if value["snapshot_kind"] not in {"git-tree", "worktree"}:
        raise StackDetectionError("source metadata has an invalid snapshot_kind")
    unknown_files = value["unknown_files"]
    if (not isinstance(unknown_files, int) or isinstance(unknown_files, bool)
            or unknown_files < 0):
        raise StackDetectionError("source metadata has an invalid unknown_files count")
    expected_status = "PARTIAL" if unknown_files else "PASS"
    if value["g01_status"] != expected_status:
        raise StackDetectionError("source metadata G01 status conflicts with unknown_files")
    for key in ("project_index_sha256", "coverage_sha256"):
        digest = value[key]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StackDetectionError(f"source metadata {key} is not a lowercase SHA-256 digest")


def _requirements_manifest_name(path: str) -> bool:
    return bool(_REQUIREMENTS_NAME.fullmatch(PurePosixPath(path).name))


def build_stack_artifacts(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    manifest_reader: Callable[[str, Mapping[str, Any]], bytes],
    *,
    generated_at: str | None = None,
    regular_source_paths: frozenset[str] | None = None,
    g01_status: str,
    unknown_files: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build schema-v1.1 stack/evidence documents from Phase 2 artifacts and bytes.

    ``manifest_reader`` must return bytes from the exact audited snapshot. The
    CLI supplies worktree-safe reads or Git blob reads; this function never
    accesses the repository or executes target code itself.
    """
    try:
        validate_artifact(project_index)
        validate_artifact(coverage)
    except (ArtifactValidationError, KeyError, TypeError) as exc:
        raise StackDetectionError("Phase 2 input artifact is invalid") from exc

    if project_index["repository_revision"] != coverage["repository_revision"]:
        raise StackDetectionError("Phase 2 input revisions do not agree")
    if project_index["generated_at"] != coverage["generated_at"]:
        raise StackDetectionError("Phase 2 input timestamps do not agree")
    timestamp = generated_at or project_index["generated_at"]
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp,
    ):
        raise StackDetectionError("generated_at must be YYYY-MM-DDTHH:MM:SSZ")
    source_metadata = _source_metadata(
        project_index, coverage, g01_status, unknown_files,
    )

    files = project_index["files"]
    coverage_by_path = {item["path"]: item for item in coverage["entries"]}
    file_by_path: dict[str, Mapping[str, Any]] = {}
    for item in files:
        path = item["path"]
        if path in file_by_path:
            raise StackDetectionError("Phase 2 project-index contains duplicate paths")
        file_by_path[path] = item

    # v1.0 indexes do not carry content_kind. Without an authoritative path set
    # from the audited Git tree, a suffix could describe a symlink target name.
    if project_index["schema_version"] == "1.0.0" and regular_source_paths is None:
        regular_source_paths = frozenset()

    builder = _ArtifactBuilder(project_index["repository_revision"], timestamp, source_metadata)
    _source_language_facts(builder, files, {
        path: entry["classification"] for path, entry in coverage_by_path.items()
    }, regular_source_paths)

    manifests_by_directory: dict[str, str] = {}
    lock_directories: set[str] = set()
    for path in sorted(file_by_path):
        entry = file_by_path[path]
        classification = coverage_by_path.get(path, {}).get("classification")
        if classification in _SKIP_CLASSIFICATIONS or not _is_manifest(path):
            continue
        if coverage_by_path.get(path) is None:
            raise StackDetectionError("Phase 2 coverage is missing a manifest path")
        if entry["bytes"] > MAX_MANIFEST_BYTES:
            raise StackDetectionError(f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte size limit: {path}")
        payload = manifest_reader(path, entry)
        if not isinstance(payload, bytes) or len(payload) != entry["bytes"]:
            raise StackDetectionError(f"manifest size does not match project-index: {path}")
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise StackDetectionError(f"manifest bytes do not match project-index snapshot: {path}")
        text = _decode_manifest(path, payload)
        basename = PurePosixPath(path).name.lower()
        if basename == "pyproject.toml":
            _python_manifest(builder, path, entry, text)
        elif _requirements_manifest_name(path):
            _requirements_manifest(builder, path, entry, text)
        elif basename == "pom.xml":
            _java_manifest(builder, path, entry, payload, text)
        elif basename == "package.json":
            directory = str(PurePosixPath(path).parent)
            _node_manifest(builder, path, entry, text, manifests_by_directory)
            if directory in manifests_by_directory:
                manager = manifests_by_directory[directory]
                if manager != "npm" and directory in lock_directories:
                    raise StackDetectionError(f"conflicting package manager declarations in {directory}")
        elif basename == "package-lock.json":
            _node_lockfile(builder, path, entry, text, manifests_by_directory, lock_directories)

    # A lockfile may sort before package.json, so validate managers after the
    # complete directory set is known as well as while each lockfile is read.
    for directory in lock_directories:
        manager = manifests_by_directory.get(directory)
        if manager is not None and manager != "npm":
            raise StackDetectionError(f"conflicting package manager declarations in {directory}")

    profile, evidence = builder.documents()
    validate_evidence_references(
        profile, evidence, expected_source_metadata=source_metadata,
    )
    try:
        validate_artifact(profile)
        validate_artifact(evidence)
    except ArtifactValidationError as exc:
        raise StackDetectionError("generated stack/evidence artifact failed schema validation") from exc
    return profile, evidence


def validate_evidence_references(
    profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    expected_source_metadata: Mapping[str, Any] | None = None,
) -> None:
    """Enforce cross-artifact evidence/certainty rules not present in v1 schemas."""
    try:
        validate_artifact(profile)
        validate_artifact(evidence)
    except ArtifactValidationError as exc:
        raise StackDetectionError("stack/evidence artifact schema validation failed") from exc
    if (profile["repository_revision"] != evidence["repository_revision"]
            or profile["generated_at"] != evidence["generated_at"]):
        raise StackDetectionError("stack/evidence revision or timestamp mismatch")
    profile_version = profile["schema_version"]
    evidence_version = evidence["schema_version"]
    if profile_version != evidence_version:
        raise StackDetectionError("stack/evidence schema version mismatch")
    if profile_version == "1.1.0":
        profile_metadata = profile.get("source_metadata")
        evidence_metadata = evidence.get("source_metadata")
        _validate_source_metadata(profile_metadata)
        _validate_source_metadata(evidence_metadata)
        if profile_metadata != evidence_metadata:
            raise StackDetectionError("stack/evidence source metadata mismatch")
        if expected_source_metadata is not None:
            _validate_source_metadata(expected_source_metadata)
            if profile_metadata != expected_source_metadata:
                raise StackDetectionError(
                    "stack/evidence source metadata does not match audited Phase 2 input digests/status",
                )
    elif expected_source_metadata is not None:
        raise StackDetectionError("expected source metadata requires v1.1 stack/evidence artifacts")

    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for item in evidence["items"]:
        evidence_id = item["id"]
        if evidence_id in evidence_by_id:
            raise StackDetectionError("duplicate evidence ID")
        if item["level"] not in {"E1", "E2"}:
            raise StackDetectionError("stack declarations may cite E1/E2 declaration evidence only")
        if item["kind"] == "runtime":
            raise StackDetectionError("runtime evidence is outside declaration-only stack detection")
        if _RUNTIME_CLAIM.search(item["summary"]):
            raise StackDetectionError("evidence summary makes a runtime claim outside declaration-only detection")
        evidence_by_id[evidence_id] = item

    for detection in profile["detections"]:
        status = detection["status"]
        citations = detection["evidence_ids"]
        if status == "UNKNOWN":
            if detection["confidence"] != _UNKNOWN_CONFIDENCE or citations:
                raise StackDetectionError("UNKNOWN detection must have confidence 0 and no evidence")
            continue
        expected_confidence = (
            _VERIFIED_CONFIDENCE if status == "VERIFIED" else _LIKELY_CONFIDENCE
        )
        if detection["confidence"] != expected_confidence:
            raise StackDetectionError("stack confidence does not match the documented rule rubric")
        if not citations:
            raise StackDetectionError("positive stack detection has no declaration evidence")
        missing = [item for item in citations if item not in evidence_by_id]
        if missing:
            raise StackDetectionError("stack detection references missing evidence")


__all__ = [
    "MAX_MANIFEST_BYTES",
    "StackDetectionError",
    "build_stack_artifacts",
    "validate_evidence_references",
]

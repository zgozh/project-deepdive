#!/usr/bin/env python3
"""Bounded, declaration-only Python AST facts for Phase 3B1."""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import re
import tokenize
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from file_classification import normalize_artifact_path
from stack_detection import StackDetectionError, _source_metadata, validate_evidence_references


MAX_SOURCE_BYTES = 1024 * 1024
MAX_AST_NODES = 50_000
LANGUAGE = "python"
EXTRACTION_METHOD = "python-ast"
_CLASSIFICATIONS_EXCLUDED = {"UNKNOWN", "VENDOR", "GENERATED", "IGNORED_WITH_REASON"}
_SKIP_REASONS = {
    "UNKNOWN_CLASSIFICATION": "Phase 2 did not determine whether this file is in scope.",
    "EXCLUDED_CLASSIFICATION": "Phase 2 classified this file outside source analysis.",
    "NOT_REGULAR_SOURCE": "The Git snapshot entry is not a regular file.",
    "BINARY_SOURCE": "The file is marked or detected as binary.",
    "SOURCE_TOO_LARGE": "The file exceeds the 1 MiB source limit.",
    "UNSUPPORTED_ENCODING": "The file encoding is unsupported by this bounded analyzer.",
    "SYNTAX_ERROR": "The source is not valid Python syntax for the analyzer runtime.",
    "AST_TOO_LARGE": "The parsed syntax tree exceeds the 50,000-node limit.",
    "PARSER_LIMIT": "The source exceeds a parser resource limit.",
}
_SLICE_LIMITATION = {
    "code": "SLICE_SCOPE",
    "reason": "3B1 extracts modules, definitions and syntactic imports only; calls, framework/runtime semantics and other languages are not analyzed.",
}
_SLICE_LIMITATION_V11 = {
    "code": "B2_STATIC_ONLY",
    "reason": "3B2 adds conservative Python call, inheritance, route-declaration and role candidates; it does not observe runtime behavior or analyze other languages.",
}
_V11_RELATION_KINDS = {"DEFINES", "IMPORTS", "CALL_CANDIDATE", "EXTENDS", "ROUTE_TO"}


class StaticAnalysisError(ValueError):
    """A static-analysis input or cross-artifact invariant failed."""


def _stable_identifier(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _evidence_identifier(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"EVID-PY-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _expected_source_metadata(
    project_index: Mapping[str, Any], coverage: Mapping[str, Any],
    g01_status: str, unknown_files: int,
) -> dict[str, Any]:
    """Return the exact Phase 3A-compatible provenance for audited inputs."""
    try:
        return _source_metadata(project_index, coverage, g01_status, unknown_files)
    except (KeyError, TypeError, ValueError, StackDetectionError) as exc:
        raise StaticAnalysisError("Phase 2 source metadata cannot be verified") from exc


def _expected_python_evidence_label(
    fact: Mapping[str, Any], symbols: Mapping[str, Mapping[str, Any]],
) -> str:
    """Return the one fixed static label allowed for a Python fact's E1 evidence."""
    if "qualified_name" in fact:
        return f"Python {fact['kind']} declaration {fact['qualified_name']}"

    kind = fact["kind"]
    if kind == "DEFINES":
        target = symbols.get(fact["target_id"])
        if target is None:
            raise StaticAnalysisError("DEFINES evidence references a missing target symbol")
        return f"Python defines {target['qualified_name']}"
    if kind == "IMPORTS":
        target = fact["unresolved_target"]
        if not isinstance(target, str) or not target:
            raise StaticAnalysisError("IMPORTS evidence has no unresolved static target")
        return f"Python import syntax names unresolved target {target}"
    fixed_labels = {
        "CALL_CANDIDATE": "Python direct call expression candidate",
        "EXTENDS": "Python class base expression candidate",
        "ROUTE_TO": "Python supported route decorator candidate; endpoint value redacted",
    }
    label = fixed_labels.get(kind)
    if label is None:
        raise StaticAnalysisError("Python fact has no supported static evidence label")
    return label


def expected_source_metadata(
    project_index: Mapping[str, Any], coverage: Mapping[str, Any],
    g01_status: str, unknown_files: int,
) -> dict[str, Any]:
    return _expected_source_metadata(project_index, coverage, g01_status, unknown_files)


def _json_input(label: str, artifact: Mapping[str, Any], expected_kind: str) -> None:
    try:
        validate_artifact(artifact)
    except (ArtifactValidationError, KeyError, TypeError) as exc:
        raise StaticAnalysisError(f"{label} artifact failed schema validation") from exc
    if artifact.get("artifact_kind") != expected_kind:
        raise StaticAnalysisError(f"{label} artifact has the wrong artifact_kind")


def _line_span(node: ast.AST) -> tuple[int, int]:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", start)
    if (not isinstance(start, int) or isinstance(start, bool) or start < 1
            or not isinstance(end, int) or isinstance(end, bool) or end < start):
        raise StaticAnalysisError("Python AST produced an invalid one-based source span")
    return start, end


def _module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__init__"


def _decode_source(payload: bytes) -> str:
    if b"\x00" in payload:
        raise _SkipFile("BINARY_SOURCE")
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(payload).readline)
        normalized = encoding.lower().replace("_", "-")
        if normalized not in {"utf-8", "utf-8-sig", "latin-1", "iso-8859-1"}:
            raise _SkipFile("UNSUPPORTED_ENCODING")
        return payload.decode(encoding)
    except _SkipFile:
        raise
    except (SyntaxError, UnicodeError, LookupError, tokenize.TokenError):
        raise _SkipFile("UNSUPPORTED_ENCODING") from None


class _SkipFile(Exception):
    def __init__(self, code: str):
        self.code = code


class _FactBuilder:
    def __init__(self, revision: str, source_metadata: Mapping[str, Any]):
        self.revision = revision
        self.source_metadata = dict(source_metadata)
        self.source_digest = ""
        self.symbols: list[dict[str, Any]] = []
        self.relations: list[dict[str, Any]] = []
        self.roles: list[dict[str, Any]] = []
        self.evidence: dict[str, dict[str, Any]] = {}
        self.identifiers: set[str] = set()

    def _claim_id(self, identifier: str) -> str:
        if identifier in self.identifiers:
            raise StaticAnalysisError("duplicate deterministic symbol/relation ID collision")
        self.identifiers.add(identifier)
        return identifier

    def _evidence(
        self, fact_id: str, fact_kind: str, label: str,
        path: str, line_start: int, line_end: int,
    ) -> str:
        evidence_id = _evidence_identifier(
            self.revision, fact_id, fact_kind, path, line_start, line_end,
        )
        if evidence_id in self.evidence:
            raise StaticAnalysisError("duplicate deterministic evidence ID collision")
        item = {
            "id": evidence_id,
            "level": "E1",
            "kind": "source",
            "summary": label,
            "confidence": 1.0,
            "locator": {
                "path": path,
                "symbol": label,
                "line_start": line_start,
                "line_end": line_end,
            },
        }
        self.evidence[evidence_id] = item
        return evidence_id

    def symbol(
        self, *, kind: str, name: str, qualified_name: str, path: str,
        line_start: int, line_end: int, identity_anchor: tuple[int, int],
    ) -> dict[str, Any]:
        identifier = self._claim_id(_stable_identifier(
            "SYM", self.revision, self.source_metadata, self.source_digest,
            LANGUAGE, kind, path, qualified_name, line_start, line_end, identity_anchor,
        ))
        label = f"Python {kind} declaration {qualified_name}"
        evidence_id = self._evidence(
            identifier, "symbol", label, path, line_start, line_end,
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
            "evidence_ids": [evidence_id],
        }
        self.symbols.append(fact)
        return fact

    def relation(
        self, *, kind: str, source_id: str, target_id: str | None,
        unresolved_target: str | None, path: str, line_start: int, line_end: int,
        label: str, identity_discriminator: int | None = None,
        identity_anchor: tuple[int, int] | None = None,
        certainty: str | None = None,
    ) -> dict[str, Any]:
        relation_certainty = certainty or ("VERIFIED" if kind == "DEFINES" else "UNRESOLVED")
        identity_parts = [
            LANGUAGE, kind, source_id, target_id, unresolved_target,
            path, line_start, line_end, identity_discriminator,
        ]
        if certainty is not None:
            identity_parts.append(relation_certainty)
        if identity_anchor is not None:
            identity_parts.append(identity_anchor)
        identifier = self._claim_id(_stable_identifier(
            "REL", self.revision, self.source_metadata, self.source_digest, *identity_parts,
        ))
        evidence_id = self._evidence(
            identifier, "relation", label, path, line_start, line_end,
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
            "certainty": relation_certainty,
            "line_start": line_start,
            "line_end": line_end,
            "evidence_ids": [evidence_id],
        }
        self.relations.append(fact)
        return fact

    def role(
        self, *, kind: str, symbol_id: str, path: str, line_start: int,
        line_end: int, label: str, identity_anchor: tuple[int, int],
    ) -> dict[str, Any]:
        identifier = self._claim_id(_stable_identifier(
            "ROLE", self.revision, self.source_metadata, self.source_digest,
            LANGUAGE, kind, symbol_id, path, line_start, line_end, identity_anchor,
        ))
        evidence_id = self._evidence(
            identifier, "role", label, path, line_start, line_end,
        )
        fact = {
            "id": identifier,
            "language": LANGUAGE,
            "kind": kind,
            "symbol_id": symbol_id,
            "path": path,
            "extraction_method": EXTRACTION_METHOD,
            "certainty": "CANDIDATE",
            "line_start": line_start,
            "line_end": line_end,
            "evidence_ids": [evidence_id],
        }
        self.roles.append(fact)
        return fact


def _unresolved_imports(node: ast.stmt) -> list[tuple[str, int, int, int]]:
    start, end = _line_span(node)
    if isinstance(node, ast.Import):
        return [(alias.name, start, end, index) for index, alias in enumerate(node.names)]
    if isinstance(node, ast.ImportFrom):
        base = "." * node.level + (node.module or "")
        return [
            (f"{base}{'' if base.endswith('.') else '.'}{alias.name}" if base else alias.name,
             start, end, index)
            for index, alias in enumerate(node.names)
        ]
    return []


def _parse_file(
    builder: _FactBuilder, path: str, payload: bytes, digest: str,
) -> tuple[dict[str, Any], int]:
    builder.source_digest = digest
    text = _decode_source(payload)
    normalized_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    line_count = max(1, normalized_newlines.count("\n") + (0 if normalized_newlines.endswith("\n") else 1))
    try:
        tree = ast.parse(text, filename="<tracked-python>", mode="exec", type_comments=True)
        node_count = 0
        for _node in ast.walk(tree):
            node_count += 1
            if node_count > MAX_AST_NODES:
                raise _SkipFile("AST_TOO_LARGE")
    except _SkipFile:
        raise
    except (SyntaxError, ValueError):
        raise _SkipFile("SYNTAX_ERROR") from None
    except (RecursionError, MemoryError):
        raise _SkipFile("PARSER_LIMIT") from None

    module_name = _module_name(path)
    module_symbol = builder.symbol(
        kind="module", name=module_name.rsplit(".", 1)[-1],
        qualified_name=module_name, path=path, line_start=1, line_end=line_count,
        identity_anchor=(0, 0),
    )

    def visit(statement: ast.AST, owner: dict[str, Any], owner_name: str, in_class: bool) -> None:
        if isinstance(statement, ast.ClassDef):
            start, end = _line_span(statement)
            qualified_name = f"{owner_name}.{statement.name}"
            symbol = builder.symbol(
                kind="class", name=statement.name, qualified_name=qualified_name,
                path=path, line_start=start, line_end=end,
                identity_anchor=(statement.col_offset, statement.end_col_offset),
            )
            builder.relation(
                kind="DEFINES", source_id=owner["id"], target_id=symbol["id"],
                unresolved_target=None, path=path, line_start=start, line_end=end,
                label=f"Python defines {qualified_name}",
            )
            for child in statement.body:
                visit(child, symbol, qualified_name, True)
            return
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _line_span(statement)
            qualified_name = f"{owner_name}.{statement.name}"
            symbol = builder.symbol(
                kind="method" if in_class else "function", name=statement.name,
                qualified_name=qualified_name, path=path, line_start=start, line_end=end,
                identity_anchor=(statement.col_offset, statement.end_col_offset),
            )
            builder.relation(
                kind="DEFINES", source_id=owner["id"], target_id=symbol["id"],
                unresolved_target=None, path=path, line_start=start, line_end=end,
                label=f"Python defines {qualified_name}",
            )
            for child in statement.body:
                visit(child, symbol, qualified_name, False)
            return
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            for target, start, end, ordinal in _unresolved_imports(statement):
                builder.relation(
                    kind="IMPORTS", source_id=owner["id"], target_id=None,
                    unresolved_target=target, path=path, line_start=start, line_end=end,
                    label=f"Python import syntax names unresolved target {target}",
                    identity_discriminator=ordinal,
                    identity_anchor=(statement.col_offset, statement.end_col_offset),
                )
            return
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                visit(child, owner, owner_name, in_class)
            elif isinstance(child, ast.AST) and not isinstance(child, ast.expr):
                for nested in ast.iter_child_nodes(child):
                    if isinstance(nested, ast.stmt):
                        visit(nested, owner, owner_name, in_class)

    for statement in tree.body:
        visit(statement, module_symbol, module_name, False)

    record = {
        "path": path,
        "sha256": digest,
        "status": "ANALYZED",
        "line_count": line_count,
        "limitations": [],
    }
    return record, line_count


class _AnalysisScope:
    def __init__(self, kind: str, symbol: Mapping[str, Any], parent: "_AnalysisScope | None"):
        self.kind = kind
        self.symbol = symbol
        self.parent = parent
        self.bindings: dict[str, list[tuple[str, str | None]]] = {}

    def add(self, name: str, kind: str, symbol_id: str | None = None) -> None:
        self.bindings.setdefault(name, []).append((kind, symbol_id))


def _ast_span_and_anchor(node: ast.AST) -> tuple[int, int, tuple[int, int]]:
    start, end = _line_span(node)
    col_start = getattr(node, "col_offset", 0)
    col_end = getattr(node, "end_col_offset", col_start)
    if (not isinstance(col_start, int) or isinstance(col_start, bool) or col_start < 0
            or not isinstance(col_end, int) or isinstance(col_end, bool) or col_end < 0
            or (start == end and col_end < col_start)):
        raise StaticAnalysisError("Python AST produced an invalid source column anchor")
    return start, end, (col_start, col_end)


def _scope_tree(
    tree: ast.Module, path: str, analysis_symbols: list[Mapping[str, Any]],
    revision: str, source_metadata: Mapping[str, Any], digest: str,
) -> tuple[_AnalysisScope, dict[int, _AnalysisScope]]:
    module_name = _module_name(path)
    symbols_by_id = {item["id"]: item for item in analysis_symbols}
    module_symbol = next(
        item for item in analysis_symbols
        if item["path"] == path and item["kind"] == "module" and item["qualified_name"] == module_name
    )
    root = _AnalysisScope("module", module_symbol, None)
    scopes: dict[int, _AnalysisScope] = {}

    def add_statement(statement: ast.AST, parent: _AnalysisScope, owner_name: str, in_class: bool) -> None:
        if isinstance(statement, ast.ClassDef):
            start, end, anchor = _ast_span_and_anchor(statement)
            qualified_name = f"{owner_name}.{statement.name}"
            symbol_id = _stable_identifier(
                "SYM", revision, source_metadata, digest, LANGUAGE, "class", path,
                qualified_name, start, end, anchor,
            )
            symbol = symbols_by_id.get(symbol_id)
            if symbol is None:
                raise StaticAnalysisError("B1 class symbol identity changed while extending the AST")
            current = _AnalysisScope("class", symbol, parent)
            scopes[id(statement)] = current
            for child in statement.body:
                add_statement(child, current, qualified_name, True)
            return
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end, anchor = _ast_span_and_anchor(statement)
            qualified_name = f"{owner_name}.{statement.name}"
            kind = "method" if in_class else "function"
            symbol_id = _stable_identifier(
                "SYM", revision, source_metadata, digest, LANGUAGE, kind, path,
                qualified_name, start, end, anchor,
            )
            symbol = symbols_by_id.get(symbol_id)
            if symbol is None:
                raise StaticAnalysisError("B1 function symbol identity changed while extending the AST")
            current = _AnalysisScope("function", symbol, parent)
            scopes[id(statement)] = current
            for child in statement.body:
                add_statement(child, current, qualified_name, False)
            return
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                add_statement(child, parent, owner_name, in_class)
            elif isinstance(child, ast.AST) and not isinstance(child, ast.expr):
                for nested in ast.iter_child_nodes(child):
                    if isinstance(nested, ast.stmt):
                        add_statement(nested, parent, owner_name, in_class)

    for statement in tree.body:
        add_statement(statement, root, module_name, False)

    def collect_bindings(node: ast.AST, scope: _AnalysisScope) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            child_scope = scopes.get(id(node))
            scope.add(node.name, "definition", child_scope.symbol["id"] if child_scope else None)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                scope.add(alias.asname or alias.name.split(".", 1)[0], "import")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    scope.add(alias.asname or alias.name, "import")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            scope.add(node.id, "assignment")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                scope.add(name, "dynamic")
        elif isinstance(node, ast.ExceptHandler) and node.name:
            scope.add(node.name, "assignment")
        elif isinstance(node, ast.MatchAs) and node.name:
            scope.add(node.name, "assignment")
        for child in ast.iter_child_nodes(node):
            collect_bindings(child, scope)

    nodes_by_scope = {id(scope): node for node in ast.walk(tree) if (scope := scopes.get(id(node))) is not None}
    for scope in [root, *scopes.values()]:
        if scope.kind == "module":
            body = tree.body
            owner_node = None
        else:
            owner_node = nodes_by_scope[id(scope)]
            body = getattr(owner_node, "body", [])
            if isinstance(owner_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = owner_node.args
                for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
                    scope.add(argument.arg, "parameter")
                if arguments.vararg:
                    scope.add(arguments.vararg.arg, "parameter")
                if arguments.kwarg:
                    scope.add(arguments.kwarg.arg, "parameter")
        for statement in body:
            collect_bindings(statement, scope)
    return root, scopes


def _scope_binding_owner(
    scope: _AnalysisScope, name: str,
) -> tuple[_AnalysisScope | None, tuple[str, str | None] | None]:
    current: _AnalysisScope | None = scope
    while current is not None:
        entries = current.bindings.get(name)
        if entries:
            return current, entries[0] if len(entries) == 1 else None
        parent = current.parent
        if current.kind == "function" and parent is not None and parent.kind == "class":
            parent = parent.parent
        current = parent
    return None, None


def _scope_binding(scope: _AnalysisScope, name: str) -> tuple[str, str | None] | None:
    return _scope_binding_owner(scope, name)[1]


def _local_definition_target(name: str, scope: _AnalysisScope, symbols: Mapping[str, Mapping[str, Any]]) -> str | None:
    binding = _scope_binding(scope, name)
    if binding is None or binding[0] != "definition" or binding[1] is None:
        return None
    target = symbols.get(binding[1])
    if target is None or target["kind"] not in {"class", "function", "method"}:
        return None
    return binding[1]


def _import_aliases(tree: ast.Module) -> dict[str, list[tuple[str, str | None]]]:
    aliases: dict[str, list[tuple[str, str | None]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                canonical = (item.name, None) if item.asname else (local, None)
                aliases.setdefault(local, []).append(canonical)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            for item in node.names:
                if item.name != "*":
                    aliases.setdefault(item.asname or item.name, []).append((module, item.name))
    return aliases


def _canonical_imported_name(
    expression: ast.AST, scope: _AnalysisScope,
    imports: Mapping[str, list[tuple[str, str | None]]],
) -> str | None:
    parts: list[str] = []
    current = expression
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    binding = _scope_binding(scope, current.id)
    candidates = imports.get(current.id, [])
    if binding is None or binding[0] != "import" or len(candidates) != 1:
        return None
    module, member = candidates[0]
    base = f"{module}.{member}" if member else module
    return ".".join([base, *reversed(parts)]) if parts else base


def _known_constructor(expression: ast.AST, scope: _AnalysisScope, imports: Mapping[str, list[tuple[str, str | None]]]) -> str | None:
    name = _canonical_imported_name(expression, scope, imports)
    return {
        "fastapi.FastAPI": "fastapi-app",
        "fastapi.APIRouter": "fastapi-router",
        "flask.Flask": "flask-app",
        "flask.Blueprint": "flask-blueprint",
    }.get(name)


class _ExtendedAstVisitor(ast.NodeVisitor):
    def __init__(self, root_scope: _AnalysisScope, scopes: Mapping[int, _AnalysisScope]):
        self.scope = root_scope
        self.scopes = scopes
        self.calls: list[tuple[ast.Call, _AnalysisScope]] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append((node, self.scope))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        previous = self.scope
        self.scope = self.scopes[id(node)]
        for statement in node.body:
            self.visit(statement)
        self.scope = previous

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous_scope = self.scope
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg and node.args.vararg.annotation:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg and node.args.kwarg.annotation:
            self.visit(node.args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        self.scope = self.scopes[id(node)]
        for statement in node.body:
            self.visit(statement)
        self.scope = previous_scope

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        outer_scope = self.scope
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        lambda_scope = _AnalysisScope("function", outer_scope.symbol, outer_scope)
        arguments = node.args
        for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            lambda_scope.add(argument.arg, "parameter")
        if arguments.vararg:
            lambda_scope.add(arguments.vararg.arg, "parameter")
        if arguments.kwarg:
            lambda_scope.add(arguments.kwarg.arg, "parameter")
        self.scope = lambda_scope
        try:
            self.visit(node.body)
        finally:
            self.scope = outer_scope


def _route_owners(
    tree: ast.Module, root_scope: _AnalysisScope,
    imports: Mapping[str, list[tuple[str, str | None]]],
) -> dict[str, str]:
    constructors: dict[str, list[str]] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            constructor = _known_constructor(statement.value.func, root_scope, imports) if isinstance(statement.value, ast.Call) else None
            for target in statement.targets:
                if isinstance(target, ast.Name) and constructor:
                    constructors.setdefault(target.id, []).append(constructor)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            value = statement.value
            constructor = _known_constructor(value.func, root_scope, imports) if isinstance(value, ast.Call) else None
            if constructor:
                constructors.setdefault(statement.target.id, []).append(constructor)
    result: dict[str, str] = {}
    for name, kinds in constructors.items():
        bindings = root_scope.bindings.get(name, [])
        if len(kinds) == 1 and len(bindings) == 1 and bindings[0][0] == "assignment":
            result[name] = kinds[0]
    return result


def _route_method(decorator: ast.Call, owner_kind: str) -> str | None:
    function = decorator.func
    if not isinstance(function, ast.Attribute):
        return None
    name = function.attr.lower()
    if owner_kind.startswith(("fastapi-", "flask-")) and name in {
        "get", "post", "put", "patch", "delete", "options", "head",
    }:
        return name.upper()
    if owner_kind.startswith("flask-") and name == "route":
        methods = next((keyword.value for keyword in decorator.keywords if keyword.arg == "methods"), None)
        if methods is None:
            return "GET"
        if isinstance(methods, (ast.List, ast.Tuple, ast.Set)) and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            and item.value.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
            for item in methods.elts
        ) and methods.elts:
            return "+".join(sorted({item.value.upper() for item in methods.elts}))
        return "DYNAMIC-METHOD"
    return None


def _recognized_route_declarations(
    tree: ast.Module, root_scope: _AnalysisScope,
    scopes: Mapping[int, _AnalysisScope], owners: Mapping[str, str],
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call, str, str]]:
    recognized: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        owner_scope = scopes[id(node)].parent or root_scope
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            receiver = decorator.func.value
            if not isinstance(receiver, ast.Name):
                continue
            owner_kind = owners.get(receiver.id)
            if owner_kind is None:
                continue
            binding_scope, binding = _scope_binding_owner(owner_scope, receiver.id)
            if binding_scope is not root_scope or binding != ("assignment", None):
                continue
            method = _route_method(decorator, owner_kind)
            if method is None:
                continue
            framework = "fastapi" if owner_kind.startswith("fastapi-") else "flask"
            recognized.append((node, decorator, framework, method))
    return recognized


def _extract_v11_file_facts(
    builder: _FactBuilder, tree: ast.Module, path: str,
    analysis_symbols: list[Mapping[str, Any]], revision: str,
    source_metadata: Mapping[str, Any], digest: str,
) -> None:
    root_scope, scopes = _scope_tree(
        tree, path, analysis_symbols, revision, source_metadata, digest,
    )
    symbols_by_id = {item["id"]: item for item in analysis_symbols}
    imports = _import_aliases(tree)
    owners = _route_owners(tree, root_scope, imports)
    routes = _recognized_route_declarations(tree, root_scope, scopes, owners)
    redacted_route_argument_calls = {
        id(candidate)
        for _handler, decorator, _framework, _method in routes
        for argument in [*decorator.args, *(keyword.value for keyword in decorator.keywords)]
        for candidate in ast.walk(argument)
        if isinstance(candidate, ast.Call)
    }
    visitor = _ExtendedAstVisitor(root_scope, scopes)
    visitor.visit(tree)
    counts: dict[str, int] = {}

    for node, scope in visitor.calls:
        start, end, anchor = _ast_span_and_anchor(node)
        ordinal = counts.get("call", 0)
        counts["call"] = ordinal + 1
        target_id: str | None = None
        if id(node) in redacted_route_argument_calls:
            unresolved = "redacted-route-argument-call"
        elif isinstance(node.func, ast.Name):
            target_id = _local_definition_target(node.func.id, scope, symbols_by_id)
            unresolved = None if target_id else f"name:{node.func.id}"
        elif isinstance(node.func, ast.Attribute):
            unresolved = f"attribute:{node.func.attr}"
        else:
            unresolved = "dynamic-call-target"
        builder.relation(
            kind="CALL_CANDIDATE", source_id=scope.symbol["id"], target_id=target_id,
            unresolved_target=unresolved, path=path, line_start=start, line_end=end,
            label="Python direct call expression candidate",
            identity_discriminator=ordinal, identity_anchor=anchor,
            certainty="CANDIDATE" if target_id else "UNRESOLVED",
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_scope = scopes[id(node)]
            class_symbol = class_scope.symbol
            base_scope = class_scope.parent or root_scope
            for base in node.bases:
                start, end, anchor = _ast_span_and_anchor(base)
                target_id = None
                if isinstance(base, ast.Name):
                    target_id = _local_definition_target(base.id, base_scope, symbols_by_id)
                    unresolved = None if target_id and symbols_by_id[target_id]["kind"] == "class" else f"name:{base.id}"
                    if unresolved is not None:
                        target_id = None
                elif isinstance(base, ast.Attribute):
                    unresolved = f"attribute:{base.attr}"
                else:
                    unresolved = "dynamic-base-expression"
                ordinal = counts.get("extends", 0)
                counts["extends"] = ordinal + 1
                builder.relation(
                    kind="EXTENDS", source_id=class_symbol["id"], target_id=target_id,
                    unresolved_target=unresolved, path=path, line_start=start, line_end=end,
                    label="Python class base expression candidate",
                    identity_discriminator=ordinal, identity_anchor=anchor,
                    certainty="CANDIDATE" if target_id else "UNRESOLVED",
                )

    def add_role(kind: str, symbol: Mapping[str, Any], marker: ast.AST, label: str) -> None:
        start, end, anchor = _ast_span_and_anchor(marker)
        identity = (anchor[0], anchor[1] + counts.get("role", 0))
        counts["role"] = counts.get("role", 0) + 1
        builder.role(
            kind=kind, symbol_id=symbol["id"], path=path,
            line_start=start, line_end=end, label=label, identity_anchor=identity,
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbol = scopes[id(node)].symbol
            model_marker: ast.AST | None = None
            for base in node.bases:
                canonical = _canonical_imported_name(base, scopes[id(node)].parent or root_scope, imports)
                if canonical in {"pydantic.BaseModel", "sqlalchemy.orm.DeclarativeBase"}:
                    model_marker = base
                    break
            if model_marker is None:
                for decorator in node.decorator_list:
                    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
                    canonical = _canonical_imported_name(expression, scopes[id(node)].parent or root_scope, imports)
                    if canonical == "dataclasses.dataclass":
                        model_marker = decorator
                        break
            if model_marker is not None:
                add_role(
                    "data_model_candidate", symbol, model_marker,
                    f"Python data model declaration candidate {symbol['qualified_name']}",
                )
            if any(
                _canonical_imported_name(base, scopes[id(node)].parent or root_scope, imports) == "unittest.TestCase"
                for base in node.bases
            ):
                marker = next(
                    base for base in node.bases
                    if _canonical_imported_name(base, scopes[id(node)].parent or root_scope, imports) == "unittest.TestCase"
                )
                add_role("test_candidate", symbol, marker, f"Python unittest class candidate {symbol['qualified_name']}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol = scopes[id(node)].symbol
            marker = node if node.name.startswith("test_") else None
            if marker is None:
                for decorator in node.decorator_list:
                    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
                    canonical = _canonical_imported_name(expression, scopes[id(node)].parent or root_scope, imports)
                    if canonical and canonical.startswith("pytest.mark."):
                        marker = decorator
                        break
            if marker is not None:
                add_role("test_candidate", symbol, marker, f"Python test function candidate {symbol['qualified_name']}")

    for node, decorator, framework, method in routes:
        start, end, anchor = _ast_span_and_anchor(decorator)
        ordinal = counts.get("route", 0)
        counts["route"] = ordinal + 1
        descriptor = f"route:{framework}:{method}:endpoint-redacted"
        builder.relation(
            kind="ROUTE_TO", source_id=scopes[id(node)].symbol["id"],
            target_id=None, unresolved_target=descriptor, path=path,
            line_start=start, line_end=end,
            label="Python supported route decorator candidate; endpoint value redacted",
            identity_discriminator=ordinal, identity_anchor=anchor, certainty="CANDIDATE",
        )


def _validate_phase2_pair(
    project_index: Mapping[str, Any], coverage: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    _json_input("project-index", project_index, "project-index")
    _json_input("coverage", coverage, "coverage")
    if (project_index.get("repository_revision") != coverage.get("repository_revision")
            or project_index.get("generated_at") != coverage.get("generated_at")):
        raise StaticAnalysisError("Phase 2 revision or timestamp mismatch")
    try:
        index_by_path = {entry["path"]: entry for entry in project_index["files"]}
        coverage_by_path = {entry["path"]: entry for entry in coverage["entries"]}
    except (KeyError, TypeError) as exc:
        raise StaticAnalysisError("Phase 2 path entries are invalid") from exc
    if len(index_by_path) != len(project_index["files"]):
        raise StaticAnalysisError("Phase 2 project-index contains duplicate paths")
    if len(coverage_by_path) != len(coverage["entries"]):
        raise StaticAnalysisError("Phase 2 coverage contains duplicate paths")
    if set(index_by_path) != set(coverage_by_path):
        raise StaticAnalysisError("Phase 2 file and coverage path sets do not agree")
    try:
        for path in index_by_path:
            normalize_artifact_path(path)
    except (TypeError, ValueError) as exc:
        raise StaticAnalysisError("Phase 2 contains an unsafe analysis-root-relative path") from exc
    return index_by_path, coverage_by_path


def analyze_python_artifacts(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    source_reader: Callable[[str, Mapping[str, Any]], bytes],
    regular_source_paths: frozenset[str] | set[str],
    *,
    g01_status: str,
    unknown_files: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build Python AST facts from a caller-provided authoritative snapshot reader."""
    index_by_path, coverage_by_path = _validate_phase2_pair(project_index, coverage)
    expected = expected_source_metadata(project_index, coverage, g01_status, unknown_files)
    for label, artifact, expected_kind in (
        ("stack-profile", stack_profile, "stack-profile"),
        ("evidence", evidence, "evidence"),
    ):
        _json_input(label, artifact, expected_kind)
        if artifact.get("schema_version") != "1.1.0":
            raise StaticAnalysisError("Phase 3B1 requires Phase 3A v1.1.0 stack/evidence artifacts")
    try:
        validate_evidence_references(
            stack_profile, evidence, expected_source_metadata=expected,
        )
    except StackDetectionError as exc:
        raise StaticAnalysisError("Phase 3A provenance or evidence references are invalid") from exc
    if stack_profile.get("repository_revision") != project_index.get("repository_revision"):
        raise StaticAnalysisError("Phase 2 and Phase 3A repository revisions do not agree")

    builder = _FactBuilder(project_index["repository_revision"], expected)
    file_records: list[dict[str, Any]] = []
    python_paths = sorted(
        path for path in index_by_path
        if PurePosixPath(path).suffix.lower() == ".py"
    )
    if not python_paths:
        raise StaticAnalysisError("Phase 2 snapshot contains no tracked Python source paths")

    for path in python_paths:
        entry = index_by_path[path]
        classification = coverage_by_path[path].get("classification")
        limitation_code: str | None = None
        if classification == "UNKNOWN":
            limitation_code = "UNKNOWN_CLASSIFICATION"
        elif classification in _CLASSIFICATIONS_EXCLUDED - {"UNKNOWN"}:
            limitation_code = "EXCLUDED_CLASSIFICATION"
        elif classification not in {"COVERED", "CLASSIFIED"}:
            raise StaticAnalysisError("Python file has an unsupported Phase 2 classification")
        elif path not in regular_source_paths:
            limitation_code = "NOT_REGULAR_SOURCE"
        elif entry.get("content_kind") == "binary":
            limitation_code = "BINARY_SOURCE"
        elif entry.get("content_kind") in {"symlink", "gitlink"}:
            limitation_code = "NOT_REGULAR_SOURCE"
        elif entry["bytes"] > MAX_SOURCE_BYTES:
            limitation_code = "SOURCE_TOO_LARGE"

        if limitation_code is not None:
            file_records.append({
                "path": path,
                "sha256": entry["sha256"],
                "status": "SKIPPED",
                "limitations": [{"code": limitation_code, "reason": _SKIP_REASONS[limitation_code]}],
            })
            continue

        payload = source_reader(path, entry)
        if not isinstance(payload, bytes):
            raise StaticAnalysisError("snapshot reader returned non-byte source data")
        if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise StaticAnalysisError("Python source bytes do not match the audited Phase 2 snapshot")
        try:
            record, _line_count = _parse_file(builder, path, payload, entry["sha256"])
        except _SkipFile as exc:
            record = {
                "path": path,
                "sha256": entry["sha256"],
                "status": "SKIPPED",
                "limitations": [{"code": exc.code, "reason": _SKIP_REASONS[exc.code]}],
            }
        file_records.append(record)

    status = "PARTIAL" if any(item["status"] == "SKIPPED" for item in file_records) else "PASS"
    limitation_codes = sorted({
        item["code"] for record in file_records for item in record["limitations"]
    })
    languages = [{
        "language": LANGUAGE,
        "analysis_status": status,
        "limitations": [copy.deepcopy(_SLICE_LIMITATION)] + [
            {"code": code, "reason": _SKIP_REASONS[code]} for code in limitation_codes
        ],
        "files": file_records,
    }]
    timestamp = stack_profile["generated_at"]
    analysis = {
        "artifact_kind": "static-analysis",
        "schema_version": "1.0.0",
        "repository_revision": project_index["repository_revision"],
        "generated_at": timestamp,
        "source_metadata": expected,
        "analysis_status": status,
        "languages": languages,
        "symbols": sorted(builder.symbols, key=lambda item: (item["path"], item["line_start"], item["line_end"], item["kind"], item["qualified_name"], item["id"])),
        "relations": sorted(builder.relations, key=lambda item: (item["path"], item["line_start"], item["line_end"], item["kind"], item["source_id"], item["id"])),
    }

    merged_evidence = copy.deepcopy(dict(evidence))
    original_items = evidence["items"]
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in original_items:
        identifier = item["id"]
        if identifier in by_id:
            raise StaticAnalysisError("Phase 3A evidence contains duplicate IDs")
        by_id[identifier] = item
    for identifier, item in builder.evidence.items():
        if identifier in by_id:
            raise StaticAnalysisError("new static-analysis evidence collides with an existing evidence ID")
        by_id[identifier] = item
    merged_evidence["items"] = [by_id[key] for key in sorted(by_id)]

    try:
        validate_artifact(analysis)
        validate_artifact(merged_evidence)
    except ArtifactValidationError as exc:
        raise StaticAnalysisError("generated static-analysis bundle failed schema validation") from exc
    validate_analysis_bundle(
        project_index, coverage, stack_profile, evidence, merged_evidence, analysis,
        g01_status=g01_status, unknown_files=unknown_files,
        expected_source_metadata=expected,
    )
    return analysis, merged_evidence


def analyze_python_artifacts_v11(
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    source_reader: Callable[[str, Mapping[str, Any]], bytes],
    regular_source_paths: frozenset[str] | set[str],
    *,
    g01_status: str,
    unknown_files: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Opt in to Phase 3B2 relations while preserving the B1 v1.0 path."""
    base_analysis, base_evidence = analyze_python_artifacts(
        project_index, coverage, stack_profile, evidence, source_reader,
        regular_source_paths, g01_status=g01_status, unknown_files=unknown_files,
    )
    analysis = copy.deepcopy(base_analysis)
    analysis["schema_version"] = "1.1.0"
    analysis["roles"] = []
    analysis["languages"][0]["limitations"][0] = copy.deepcopy(_SLICE_LIMITATION_V11)
    expected = expected_source_metadata(project_index, coverage, g01_status, unknown_files)
    builder = _FactBuilder(project_index["repository_revision"], expected)
    builder.identifiers.update(item["id"] for item in analysis["symbols"])
    builder.identifiers.update(item["id"] for item in analysis["relations"])
    file_by_path = {item["path"]: item for item in project_index["files"]}

    for file_record in analysis["languages"][0]["files"]:
        if file_record["status"] != "ANALYZED":
            continue
        path = file_record["path"]
        entry = file_by_path[path]
        payload = source_reader(path, entry)
        if (not isinstance(payload, bytes) or len(payload) != entry["bytes"]
                or hashlib.sha256(payload).hexdigest() != entry["sha256"]):
            raise StaticAnalysisError("Python source bytes changed during the v1.1 AST extension")
        text = _decode_source(payload)
        try:
            tree = ast.parse(text, filename="<tracked-python>", mode="exec", type_comments=True)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            raise StaticAnalysisError("Python source parse result changed during the v1.1 AST extension") from None
        builder.source_digest = entry["sha256"]
        _extract_v11_file_facts(
            builder, tree, path, analysis["symbols"], project_index["repository_revision"],
            expected, entry["sha256"],
        )

    analysis["relations"].extend(builder.relations)
    analysis["relations"].sort(
        key=lambda item: (item["path"], item["line_start"], item["line_end"], item["kind"], item["source_id"], item["id"]),
    )
    analysis["roles"] = sorted(
        builder.roles, key=lambda item: (item["path"], item["line_start"], item["line_end"], item["kind"], item["symbol_id"], item["id"]),
    )

    merged_evidence = copy.deepcopy(base_evidence)
    merged_by_id = {item["id"]: item for item in merged_evidence["items"]}
    for identifier, item in builder.evidence.items():
        if identifier in merged_by_id:
            raise StaticAnalysisError("new Phase 3B2 evidence collides with an existing evidence ID")
        merged_by_id[identifier] = item
    merged_evidence["items"] = [merged_by_id[key] for key in sorted(merged_by_id)]
    try:
        validate_artifact(analysis)
        validate_artifact(merged_evidence)
    except ArtifactValidationError as exc:
        raise StaticAnalysisError("generated static-analysis v1.1 bundle failed schema validation") from exc
    validate_analysis_bundle(
        project_index, coverage, stack_profile, evidence, merged_evidence, analysis,
        g01_status=g01_status, unknown_files=unknown_files,
        expected_source_metadata=expected,
    )
    return analysis, merged_evidence


def validate_analysis_bundle(
    project_index: Mapping[str, Any], coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any], original_evidence: Mapping[str, Any],
    merged_evidence: Mapping[str, Any], analysis: Mapping[str, Any], *,
    g01_status: str, unknown_files: int,
    expected_source_metadata: Mapping[str, Any] | None = None,
) -> None:
    """Enforce bundle consistency, bounded spans, endpoints and E1 citations."""
    index_by_path, coverage_by_path = _validate_phase2_pair(project_index, coverage)
    computed_metadata = _expected_source_metadata(project_index, coverage, g01_status, unknown_files)
    expected = computed_metadata if expected_source_metadata is None else expected_source_metadata
    if expected != computed_metadata:
        raise StaticAnalysisError("supplied source metadata does not match audited Phase 2 inputs")
    try:
        validate_artifact(analysis)
        validate_artifact(merged_evidence)
    except ArtifactValidationError as exc:
        raise StaticAnalysisError("static-analysis bundle failed schema validation") from exc
    for label, artifact in (("stack-profile", stack_profile), ("evidence", original_evidence),
                            ("evidence", merged_evidence), ("static-analysis", analysis)):
        if artifact.get("repository_revision") != project_index.get("repository_revision"):
            raise StaticAnalysisError(f"{label} repository revision mismatch")
        if artifact.get("generated_at") != stack_profile.get("generated_at"):
            raise StaticAnalysisError(f"{label} generated_at timestamp mismatch")
    for artifact in (stack_profile, original_evidence, merged_evidence, analysis):
        if artifact.get("source_metadata") != expected:
            raise StaticAnalysisError("bundle source metadata is missing or inconsistent")
    try:
        validate_evidence_references(
            stack_profile, original_evidence, expected_source_metadata=expected,
        )
    except StackDetectionError as exc:
        raise StaticAnalysisError("Phase 3A evidence failed semantic validation") from exc

    old_items = original_evidence.get("items")
    new_items = merged_evidence.get("items")
    if not isinstance(old_items, list) or not isinstance(new_items, list):
        raise StaticAnalysisError("evidence items are missing")
    old_by_id: dict[str, Mapping[str, Any]] = {}
    merged_by_id: dict[str, Mapping[str, Any]] = {}
    for target, rows, label in ((old_by_id, old_items, "Phase 3A"), (merged_by_id, new_items, "merged")):
        for row in rows:
            identifier = row.get("id")
            if not isinstance(identifier, str) or identifier in target:
                raise StaticAnalysisError(f"{label} evidence contains duplicate or invalid IDs")
            target[identifier] = row
    for identifier, old in old_by_id.items():
        if merged_by_id.get(identifier) != old:
            raise StaticAnalysisError("merged evidence did not preserve an original Phase 3A evidence ID and record")

    if len(analysis["languages"]) != 1 or analysis["languages"][0]["language"] != LANGUAGE:
        raise StaticAnalysisError("3B1 must contain exactly one Python language record")
    if analysis["languages"][0]["analysis_status"] != analysis["analysis_status"]:
        raise StaticAnalysisError("language and artifact analysis statuses do not agree")

    files: dict[str, Mapping[str, Any]] = {}
    for language in analysis["languages"]:
        for record in language["files"]:
            path = record["path"]
            if path in files:
                raise StaticAnalysisError("duplicate analyzed file path")
            if path not in index_by_path or path not in coverage_by_path:
                raise StaticAnalysisError("static-analysis file path is outside the Phase 2 input set")
            if PurePosixPath(path).suffix.lower() != ".py":
                raise StaticAnalysisError("static-analysis contains a non-Python source path")
            if record["sha256"] != index_by_path[path]["sha256"]:
                raise StaticAnalysisError("static-analysis source digest does not match Phase 2")
            classification = coverage_by_path[path].get("classification")
            if classification in _CLASSIFICATIONS_EXCLUDED:
                if record["status"] != "SKIPPED":
                    raise StaticAnalysisError("Phase 2 excluded Python path was analyzed")
            if record["status"] == "ANALYZED":
                if not isinstance(record.get("line_count"), int) or record["line_count"] < 1:
                    raise StaticAnalysisError("analyzed file has no valid line count")
                if record["limitations"]:
                    raise StaticAnalysisError("analyzed file has skip limitations")
            elif not record["limitations"]:
                raise StaticAnalysisError("skipped file has no limitation reason")
            files[path] = record
    expected_python_paths = {
        path for path in index_by_path if PurePosixPath(path).suffix.lower() == ".py"
    }
    if set(files) != expected_python_paths:
        raise StaticAnalysisError("static-analysis file set does not match Phase 2 Python paths")
    expected_status = "PARTIAL" if any(item["status"] == "SKIPPED" for item in files.values()) else "PASS"
    if analysis["analysis_status"] != expected_status:
        raise StaticAnalysisError("analysis status does not reflect per-file skips")

    is_v11 = analysis.get("schema_version") == "1.1.0"
    evidence_ids_used: set[str] = set()
    symbols: dict[str, Mapping[str, Any]] = {}
    fact_records = list(analysis["symbols"]) + list(analysis["relations"])
    if is_v11:
        fact_records.extend(analysis["roles"])
    all_fact_ids: set[str] = set()
    for fact in fact_records:
        identifier = fact["id"]
        if identifier in all_fact_ids:
            raise StaticAnalysisError("duplicate symbol/relation/role ID")
        all_fact_ids.add(identifier)
        if fact["language"] != LANGUAGE or fact["extraction_method"] != EXTRACTION_METHOD:
            raise StaticAnalysisError("3B1 contains an unsupported language or extraction method")
        path = fact["path"]
        file_record = files.get(path)
        if file_record is None or file_record["status"] != "ANALYZED":
            raise StaticAnalysisError("fact path does not name an analyzed Phase 2 Python file")
        start, end = fact["line_start"], fact["line_end"]
        if start < 1 or end < start or end > file_record.get("line_count", 0):
            raise StaticAnalysisError("fact has an invalid one-based source line span")
        if not fact["evidence_ids"]:
            raise StaticAnalysisError("fact has no evidence IDs")
        if len(set(fact["evidence_ids"])) != len(fact["evidence_ids"]):
            raise StaticAnalysisError("fact has duplicate evidence IDs")
        for evidence_id in fact["evidence_ids"]:
            if evidence_id in old_by_id:
                raise StaticAnalysisError("static fact must cite newly generated Python AST evidence")
            item = merged_by_id.get(evidence_id)
            if item is None or item.get("level") != "E1":
                raise StaticAnalysisError("fact references missing or non-E1 evidence")
            locator = item.get("locator", {})
            if (locator.get("path") != path or locator.get("line_start") != start
                    or locator.get("line_end") != end):
                raise StaticAnalysisError("fact evidence locator does not match its source span")
            if "symbol_id" not in fact:
                expected_label = _expected_python_evidence_label(fact, symbols)
                if (item.get("summary") != expected_label
                        or locator.get("symbol") != expected_label):
                    raise StaticAnalysisError(
                        "Python evidence summary or locator symbol does not match its static fact",
                    )
            evidence_ids_used.add(evidence_id)
        if "qualified_name" in fact:
            if fact["certainty"] != "VERIFIED":
                raise StaticAnalysisError("AST symbol certainty must be VERIFIED")
            if identifier in symbols:
                raise StaticAnalysisError("duplicate symbol ID")
            symbols[identifier] = fact

    for relation in analysis["relations"]:
        source = symbols.get(relation["source_id"])
        if source is None:
            raise StaticAnalysisError("relation has a dangling source symbol ID")
        if source["path"] != relation["path"]:
            raise StaticAnalysisError("relation source symbol is in a different source file")
        if (source["line_start"] > relation["line_start"]
                or source["line_end"] < relation["line_end"]):
            if not (is_v11 and relation["kind"] == "ROUTE_TO"
                    and relation["line_end"] <= source["line_start"]):
                raise StaticAnalysisError("relation source span is outside its source symbol range")
        if is_v11 and relation["kind"] == "ROUTE_TO":
            next_handlers = [
                symbol for symbol in symbols.values()
                if symbol["path"] == relation["path"]
                and symbol["kind"] in {"function", "method"}
                and symbol["line_start"] >= relation["line_end"]
            ]
            if not next_handlers:
                raise StaticAnalysisError("ROUTE_TO has no following source handler symbol")
            nearest_line = min(item["line_start"] for item in next_handlers)
            nearest = [item for item in next_handlers if item["line_start"] == nearest_line]
            if all(item["id"] != relation["source_id"] for item in nearest):
                raise StaticAnalysisError("ROUTE_TO source is not the adjacent handler declaration")
        target_id, unresolved = relation["target_id"], relation["unresolved_target"]
        if (target_id is None) == (unresolved is None):
            raise StaticAnalysisError("relation must have exactly one resolved or unresolved target")
        if relation["kind"] == "DEFINES":
            target = symbols.get(target_id)
            if target is None or unresolved is not None:
                raise StaticAnalysisError("DEFINES relation has a dangling target symbol ID")
            if (target["path"] != relation["path"] or target["line_start"] != relation["line_start"]
                    or target["line_end"] != relation["line_end"]):
                raise StaticAnalysisError("DEFINES relation source span does not match target symbol")
            if target["kind"] == "module" or target["qualified_name"].rsplit(".", 1)[0] != source["qualified_name"]:
                raise StaticAnalysisError("DEFINES relation does not follow lexical symbol scope")
            if relation["certainty"] != "VERIFIED":
                raise StaticAnalysisError("DEFINES relation certainty must be VERIFIED")
        elif relation["kind"] == "IMPORTS":
            if target_id is not None or not unresolved or relation["certainty"] != "UNRESOLVED":
                raise StaticAnalysisError("IMPORTS relation must keep an explicit unresolved target")
        elif is_v11 and relation["kind"] == "CALL_CANDIDATE":
            if target_id is None:
                if not unresolved or relation["certainty"] != "UNRESOLVED":
                    raise StaticAnalysisError("unresolved CALL_CANDIDATE lacks an explicit unresolved target")
            else:
                target = symbols.get(target_id)
                if (target is None or target["path"] != relation["path"]
                        or target["kind"] not in {"class", "function", "method"}
                        or unresolved is not None or relation["certainty"] != "CANDIDATE"):
                    raise StaticAnalysisError("CALL_CANDIDATE target is not a local evidenced symbol")
        elif is_v11 and relation["kind"] == "EXTENDS":
            if target_id is None:
                if not unresolved or relation["certainty"] != "UNRESOLVED":
                    raise StaticAnalysisError("unresolved EXTENDS lacks an explicit unresolved target")
            else:
                target = symbols.get(target_id)
                if (target is None or target["path"] != relation["path"] or target["kind"] != "class"
                        or unresolved is not None or relation["certainty"] != "CANDIDATE"):
                    raise StaticAnalysisError("EXTENDS target is not a local class candidate")
        elif is_v11 and relation["kind"] == "ROUTE_TO":
            if (target_id is not None or not isinstance(unresolved, str)
                    or not re.fullmatch(
                        r"route:(?:fastapi|flask):(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD|DYNAMIC-METHOD)(?:\+(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD))*:endpoint-redacted",
                        unresolved,
                    ) or relation["certainty"] != "CANDIDATE"):
                raise StaticAnalysisError("ROUTE_TO must be a redacted source-backed route candidate")
        else:
            raise StaticAnalysisError("unsupported static-analysis relation kind")

    if is_v11:
        for role in analysis["roles"]:
            symbol = symbols.get(role["symbol_id"])
            if symbol is None or symbol["path"] != role["path"]:
                raise StaticAnalysisError("role references a missing or cross-file symbol")
            if role["certainty"] != "CANDIDATE":
                raise StaticAnalysisError("Python declaration role must remain a candidate")
            expected_kinds = {"class"} if role["kind"] == "data_model_candidate" else {"class", "function", "method"}
            if symbol["kind"] not in expected_kinds:
                raise StaticAnalysisError("role kind does not match its source symbol kind")
            summary_prefix = (
                "Python data model declaration candidate "
                if role["kind"] == "data_model_candidate"
                else "Python unittest class candidate "
                if symbol["kind"] == "class"
                else "Python test function candidate "
            )
            expected_summary = summary_prefix + symbol["qualified_name"]
            if any(merged_by_id[evidence_id].get("summary") != expected_summary
                   or merged_by_id[evidence_id].get("locator", {}).get("symbol") != expected_summary
                   for evidence_id in role["evidence_ids"]):
                raise StaticAnalysisError("role evidence does not identify its linked source symbol")

            # A marker may precede its declaration (decorator), lie in the
            # declaration header (base class), or span the declaration itself
            # (a test_ function). Accept only the nearest/innermost matching
            # declaration for that source geometry.
            preceding = role["line_end"] <= symbol["line_start"]
            contained = (
                symbol["line_start"] <= role["line_start"]
                and role["line_end"] <= symbol["line_end"]
            )
            if preceding:
                following = [
                    item for item in symbols.values()
                    if item["path"] == role["path"] and item["kind"] in expected_kinds
                    and item["line_start"] >= role["line_end"]
                ]
                if not following:
                    raise StaticAnalysisError("role has no following source declaration")
                nearest_line = min(item["line_start"] for item in following)
                nearest = [item for item in following if item["line_start"] == nearest_line]
                if all(item["id"] != role["symbol_id"] for item in nearest):
                    raise StaticAnalysisError("role does not name the adjacent source declaration")
            elif contained:
                containing = [
                    item for item in symbols.values()
                    if item["path"] == role["path"] and item["kind"] in expected_kinds
                    and item["line_start"] <= role["line_start"]
                    and role["line_end"] <= item["line_end"]
                ]
                minimum_width = min(item["line_end"] - item["line_start"] for item in containing)
                innermost = [
                    item for item in containing
                    if item["line_end"] - item["line_start"] == minimum_width
                ]
                if all(item["id"] != role["symbol_id"] for item in innermost):
                    raise StaticAnalysisError("role does not name the innermost source declaration")
            else:
                raise StaticAnalysisError("role source span is not adjacent to or within its declaration")
    elif "roles" in analysis:
        raise StaticAnalysisError("v1.0 static-analysis cannot contain v1.1 roles")

    modules_by_path: dict[str, int] = {}
    for item in symbols.values():
        if item["kind"] == "module":
            modules_by_path[item["path"]] = modules_by_path.get(item["path"], 0) + 1
    analyzed_paths = {path for path, record in files.items() if record["status"] == "ANALYZED"}
    if set(modules_by_path) != analyzed_paths or any(count != 1 for count in modules_by_path.values()):
        raise StaticAnalysisError("each analyzed Python file must have exactly one module symbol")
    defined_targets = [
        relation["target_id"] for relation in analysis["relations"]
        if relation["kind"] == "DEFINES"
    ]
    non_module_ids = {identifier for identifier, symbol in symbols.items() if symbol["kind"] != "module"}
    if set(defined_targets) != non_module_ids or len(defined_targets) != len(non_module_ids):
        raise StaticAnalysisError("each non-module symbol must have exactly one DEFINES relation")

    new_evidence_ids = set(merged_by_id) - set(old_by_id)
    if new_evidence_ids != evidence_ids_used:
        raise StaticAnalysisError("merged evidence has dangling or unreferenced new evidence records")
    for identifier in new_evidence_ids:
        item = merged_by_id[identifier]
        if item.get("level") != "E1" or item.get("kind") != "source":
            raise StaticAnalysisError("new Python AST evidence must be E1 source evidence")


__all__ = [
    "MAX_AST_NODES", "MAX_SOURCE_BYTES", "StaticAnalysisError",
    "analyze_python_artifacts", "analyze_python_artifacts_v11",
    "expected_source_metadata", "validate_analysis_bundle",
]

"""Deterministic, report-only checks for a digest-bound D3a whole book."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Mapping


_CLAIM_TOKEN = re.compile(r"\[\^CLAIM-([^\]\s]+)\]")
_CLAIM_DEFINITION = re.compile(r"^\[\^CLAIM-([^\]\s]+)\]:\s*(.*)$")
_LINK = re.compile(r"\[[^\]]*\]\(#([^\s)]+)\)")
_HTML_ID = re.compile(r'<(?:a|\w+)[^>]*\bid=[\"\']([^\"\']+)[\"\'][^>]*>', re.IGNORECASE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _anchor(unit_id: str) -> str:
    return "unit-" + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:16]


def _visible_lines(markdown: str) -> list[tuple[int, str]]:
    """Return non-fenced lines with original one-based line numbers."""
    result: list[tuple[int, str]] = []
    fence_char = None
    fence_size = 0
    for number, line in enumerate(markdown.splitlines(), 1):
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence_char is None:
                fence_char, fence_size = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char, fence_size = None, 0
            continue
        if fence_char is None:
            result.append((number, line))
    return result


def _slug(heading: str) -> str:
    text = re.sub(r"[`*_~]", "", heading).replace("\\", "")
    text = re.sub(r"<[^>]*>", "", text).casefold().strip()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return re.sub(r"\s+", "-", text)


def _canonical_footnote(facts: Mapping[str, Any], claim: Mapping[str, Any]) -> str:
    evidence_by_id = {row["id"]: row for row in facts["evidence"]}
    citations = []
    for evidence_id in claim["evidence_ids"]:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            continue
        locator = {key: evidence["locator"][key] for key in ("path", "symbol", "line_start", "line_end") if key in evidence["locator"]}
        citations.append({"id": evidence_id, "level": evidence["level"], "locator": locator})
    payload = json.dumps({"evidence": citations}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"[^CLAIM-{claim['id'].removeprefix('CLAIM-')}]: {payload}"


def audit_whole_book(
    assembly: Mapping[str, Any], markdown: str,
    chapter_facts: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Check only mechanically bound links, unit order, and project citations."""
    selected = [row for row in assembly["units"] if row["selection_state"] == "SELECTED"]
    findings: list[dict[str, Any]] = []

    def add(code: str, *, severity: str = "ERROR", row: Mapping[str, Any] | None = None,
            related: list[str] | None = None, claim_id: str | None = None,
            fragment: str | None = None, line: int | None = None,
            file: str = "WHOLE-BOOK.md") -> None:
        findings.append({
            "code": code, "severity": severity,
            "unit_id": row["unit_id"] if row else None,
            "related_unit_ids": related or [], "claim_id": claim_id,
            "fragment_sha256": hashlib.sha256(fragment.encode("utf-8")).hexdigest() if fragment else None,
            "file": file, "line": line,
        })

    visible = _visible_lines(markdown)
    anchor_lines: dict[str, list[int]] = defaultdict(list)
    for number, line in visible:
        for match in _HTML_ID.finditer(line):
            anchor_lines[match.group(1)].append(number)
        heading = _HEADING.fullmatch(line)
        if heading:
            anchor_lines[_slug(heading.group(1))].append(number)

    expected_anchors = {_anchor(row["unit_id"]): row for row in selected}
    row_lines: dict[str, int] = {}
    for anchor, row in expected_anchors.items():
        lines = [number for number, line in visible if re.search(rf'<a\s+id="{re.escape(anchor)}"></a>', line)]
        if not lines:
            add("ANCHOR_MISSING", row=row)
        else:
            row_lines[row["unit_id"]] = lines[0]
            anchor_lines[anchor] = lines
            if len(lines) > 1:
                add("ANCHOR_DUPLICATE", row=row, line=lines[1])

    contents_start = next((i for i, (_, line) in enumerate(visible) if line.strip() == "## Contents"), None)
    lessons_start = next((i for i, (_, line) in enumerate(visible) if line.strip() == "## Lessons"), None)
    toc_range = range(contents_start + 1, lessons_start) if contents_start is not None and lessons_start is not None and contents_start < lessons_start else range(0)
    toc_links: list[tuple[int, str]] = []
    for index in toc_range:
        number, line = visible[index]
        toc_links.extend((number, match.group(1)) for match in _LINK.finditer(line))
    toc_targets = [target for _, target in toc_links]
    for row in selected:
        anchor = _anchor(row["unit_id"])
        if toc_targets.count(anchor) != 1:
            add("TOC_LINK_MISSING", row=row)
    for number, target in toc_links:
        if len(anchor_lines.get(target, [])) != 1:
            add("TOC_LINK_UNRESOLVED", line=number, fragment=target)

    body_indexes: set[int] = set()
    ordered_anchor_indices = [
        next((i for i, (number, line) in enumerate(visible) if row_lines.get(row["unit_id"]) == number and f'id="{_anchor(row["unit_id"])}"' in line), None)
        for row in selected
    ]
    for pos, start in enumerate(ordered_anchor_indices):
        if start is None:
            continue
        end = next((candidate for candidate in ordered_anchor_indices[pos + 1:] if candidate is not None), len(visible))
        body_indexes.update(range(start, end))
    same_book_count = 0
    toc_indexes = set(toc_range)
    for index, (number, line) in enumerate(visible):
        if index in toc_indexes or index not in body_indexes:
            continue
        for match in _LINK.finditer(line):
            target = match.group(1)
            same_book_count += 1
            matches = anchor_lines.get(target, [])
            eligible = [item for item in selected if row_lines.get(item["unit_id"]) is not None and row_lines[item["unit_id"]] <= number]
            row = max(eligible, key=lambda item: item["position"]) if eligible else None
            if not matches:
                add("FRAGMENT_UNRESOLVED", row=row, line=number, fragment=target)
            elif len(matches) > 1:
                add("FRAGMENT_AMBIGUOUS", row=row, line=number, fragment=target)

    selected_positions = {row["unit_id"]: row["position"] for row in selected}
    prerequisite_count = 0
    for row in selected:
        for prerequisite in row["prerequisite_ids"]:
            prerequisite_count += 1
            earlier = selected_positions.get(prerequisite)
            if earlier is None:
                add("PREREQUISITE_NOT_SELECTED", row=row, related=[prerequisite])
            elif earlier >= row["position"]:
                add("PREREQUISITE_ORDER", row=row, related=[prerequisite])

    claim_reference_count = 0
    repeated_count = 0
    bindings: dict[str, list[tuple[str, str]]] = defaultdict(list)
    book_definitions: dict[str, list[tuple[Mapping[str, Any], int]]] = defaultdict(list)
    for selected_index, row in enumerate(selected):
        if row["route"] != "PHASE6A_PROJECT_CLAIM":
            continue
        facts = chapter_facts.get(row["unit_id"])
        if facts is None or row["unit_id"] not in row_lines:
            continue
        claims = {claim["id"]: claim for claim in facts["claims"]}
        evidence = {item["id"]: item for item in facts["evidence"]}
        start = ordered_anchor_indices[selected_index]
        end = next((candidate for candidate in ordered_anchor_indices[selected_index + 1:] if candidate is not None), len(visible))
        lines = visible[start:end] if start is not None else []
        definitions: dict[str, list[tuple[int, str]]] = defaultdict(list)
        references: list[tuple[int, str]] = []
        for number, line in lines:
            definition = _CLAIM_DEFINITION.fullmatch(line)
            if definition:
                raw_id, content = definition.groups()
                claim_id = "CLAIM-" + raw_id
                definitions[claim_id].append((number, line))
                continue
            references.extend((number, "CLAIM-" + token) for token in _CLAIM_TOKEN.findall(line))
        claim_reference_count += len(references)
        for number, claim_id in references:
            if not re.fullmatch(r"CLAIM-[0-9a-f]{64}", claim_id) or claim_id not in claims:
                add("CLAIM_REFERENCE_UNBOUND", row=row, claim_id=claim_id if re.fullmatch(r"CLAIM-[0-9a-f]{64}", claim_id) else None, line=number)
            elif not definitions.get(claim_id):
                add("CLAIM_DEFINITION_MISSING", row=row, claim_id=claim_id, line=number)
        for claim_id, items in definitions.items():
            book_definitions[claim_id].extend((row, number) for number, _ in items)
            if claim_id not in claims:
                add("CLAIM_REFERENCE_UNBOUND", row=row, claim_id=claim_id if re.fullmatch(r"CLAIM-[0-9a-f]{64}", claim_id) else None, line=items[0][0])
                continue
            if len(items) > 1:
                add("CLAIM_DEFINITION_DUPLICATE", row=row, claim_id=claim_id, line=items[1][0])
            if items[0][1] != _canonical_footnote(facts, claims[claim_id]):
                add("CLAIM_DEFINITION_MISMATCH", row=row, claim_id=claim_id, line=items[0][0])
        for claim_id, claim in claims.items():
            signature = {
                key: claim[key] for key in ("statement_sha256", "evidence_ids", "resolved_evidence_levels", "graph_node_ids", "graph_edge_ids", "file_paths")
            }
            signature["evidence"] = [evidence[item] for item in claim["evidence_ids"] if item in evidence]
            bindings[claim_id].append((row["unit_id"], json.dumps(signature, sort_keys=True, separators=(",", ":"))))

    for claim_id, definitions in book_definitions.items():
        if not re.fullmatch(r"CLAIM-[0-9a-f]{64}", claim_id):
            continue
        first_unit_id = definitions[0][0]["unit_id"]
        seen_units = {first_unit_id}
        for row, number in definitions[1:]:
            unit_id = row["unit_id"]
            if unit_id not in seen_units:
                add("CLAIM_DEFINITION_DUPLICATE", row=row, related=[first_unit_id, unit_id], claim_id=claim_id, line=number)
                seen_units.add(unit_id)

    for claim_id, entries in bindings.items():
        if len(entries) > 1:
            repeated_count += 1
            if len({signature for _, signature in entries}) > 1:
                add("CLAIM_BINDING_CONFLICT", claim_id=claim_id, related=[unit_id for unit_id, _ in entries])

    if assembly["source_status"] == "PARTIAL":
        add("SOURCE_PARTIAL", severity="INFO", file="QUALITY-AND-GAPS.md")
    if assembly["unknown_files"]:
        add("UNKNOWN_FILES", severity="INFO", file="QUALITY-AND-GAPS.md")
    for row in selected:
        review_state = (row.get("project_answer") or {}).get("d1b_review_state")
        if row["answer_status"] == "NOT_SUPPLIED" or (row["route"] == "PHASE6A_PROJECT_CLAIM" and review_state != "REVIEWED_DRAFT"):
            add("ANSWER_GAP", severity="INFO", row=row, file="QUALITY-AND-GAPS.md")

    findings.sort(key=lambda item: (item["file"], item["line"] or 0, item["code"], item["unit_id"] or "", item["claim_id"] or ""))
    counts = {
        "anchor_count": len(expected_anchors), "toc_link_count": len(toc_links),
        "same_book_fragment_count": same_book_count, "prerequisite_count": prerequisite_count,
        "claim_reference_count": claim_reference_count, "repeated_claim_id_count": repeated_count,
        "finding_count": len(findings),
    }
    return findings, counts


def render_audit_report(artifact: Mapping[str, Any]) -> str:
    lines = [
        "# Whole-Book Consistency Audit\n\n",
        "Report-only deterministic integrity/coverage checks; this is not an independent semantic review or verification.\n",
        "Semantic consistency and glossary review: `NOT_RUN`. Assembly statuses remain `DRAFT`/`PARTIAL`.\n\n",
        f"- Assembly: `{artifact['assembly_id']}`\n- Audit: `{artifact['audit_id']}`\n",
        f"- Source: `{artifact['source_status']}`; unknown files: `{artifact['unknown_files']}`\n",
        f"- Checks: `{artifact['check_counts']}`\n\n",
        "## Findings\n\n",
    ]
    if not artifact["findings"]:
        lines.append("No deterministic findings. This does not establish semantic correctness.\n")
    for item in artifact["findings"]:
        subject = item["unit_id"] or item["claim_id"] or "assembly"
        location = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
        lines.append(f"- `{item['severity']}` `{item['code']}` for `{subject}` at `{location}`.\n")
    return "".join(lines)

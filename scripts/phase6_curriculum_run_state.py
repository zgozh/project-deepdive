#!/usr/bin/env python3
"""Strict append-only state helpers for the opt-in Phase 6D2B2 runner."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, _strict_json_loads, dumps_artifact, validate_artifact
from phase4_graph import canonical_tuple_sha256


_STATE_NAME = re.compile(r"^run-state-([0-9]{6})\.json$")
_STATE_V1 = "1.0.0"
_STATE_V1_1 = "1.1.0"
_BLOCKED_ROUTES = {"UPSTREAM_REQUIRED", "BLOCKED_UNSUPPORTED"}
_TERMINAL = {
    "BLOCKED_UPSTREAM", "BLOCKED_PREREQUISITE", "BLOCKED_REVIEW", "REVIEWED_DRAFT",
    "UPSTREAM_REQUIRED", "STOP_UNSUPPORTED", "REVIEW_INCOMPLETE_STOP", "REPAIR_LIMIT_REACHED",
}


class CurriculumRunStateError(ValueError):
    """A fixed, redacted run-state validation or publication failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise CurriculumRunStateError(code)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _identity_bytes(identity: Mapping[str, Any]) -> bytes:
    return json.dumps(identity, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8")


def state_id(plan: Mapping[str, Any], plan_raw: bytes) -> str:
    suffix = canonical_tuple_sha256(("phase6d2b2-state-v1", plan["run_plan_id"], sha256(plan_raw)))
    return "CURRICULUM-RUN-STATE-" + suffix


def _run_state(rows: list[Mapping[str, Any]]) -> str:
    statuses = [row["execution_state"] for row in rows]
    if all(status == "BLOCKED_UPSTREAM" for status in statuses):
        return "PARTIAL_BLOCKED"
    if any(status in _TERMINAL - {"REVIEWED_DRAFT"} for status in statuses):
        return "PARTIAL_BLOCKED"
    if all(status == "REVIEWED_DRAFT" for status in statuses):
        return "DRAFTS_REVIEWED"
    if all(status == "PLANNED" for status in statuses):
        return "READY"
    if any(status in {"PREPARED", "AWAITING_REVIEW", "REPAIR_READY"} for status in statuses):
        return "WAITING_EXTERNAL"
    return "ACTIVE"


def _state_row(plan_row: Mapping[str, Any]) -> dict[str, Any]:
    identity = plan_row["unit_identity"]
    return {
        "position": plan_row["position"],
        "unit_id": identity["id"],
        "unit_identity_sha256": sha256(_identity_bytes(identity)),
        "route": plan_row["route"],
        "plan_state": plan_row["state"],
        "prerequisite_ids": list(identity["prerequisite_ids"]),
        "reason_codes": list(plan_row["reason_codes"]),
        "blocked_by": list(plan_row["blocked_by"]),
        "execution_state": "BLOCKED_UPSTREAM" if plan_row["state"] == "BLOCKED" else "PLANNED",
        "attempt_id": None,
        "artifact_digests": [],
    }


def initial_state(plan: Mapping[str, Any], plan_raw: bytes, *, generated_at: str | None = None) -> dict[str, Any]:
    """Project one complete initial snapshot; no unit is dispatched here."""
    rows = [_state_row(row) for row in plan["units"]]
    state = {
        "artifact_kind": "curriculum-run-state",
        "schema_version": "1.0.0",
        "generated_at": generated_at or utc_now(),
        "run_state_id": state_id(plan, plan_raw),
        "run_plan_id": plan["run_plan_id"],
        "run_plan_sha256": sha256(plan_raw),
        "state_revision": 1,
        "previous_state_sha256": None,
        "repository_revision": plan["repository_revision"],
        "snapshot_kind": plan["snapshot_kind"],
        "source_metadata": plan["source_metadata"],
        "source_status": plan["source_status"],
        "source_run_manifest_sha256": plan["source_run_manifest_sha256"],
        "unknown_files": plan["unknown_files"],
        "curriculum_schema_version": plan["curriculum_schema_version"],
        "curriculum_sha256": plan["curriculum_sha256"],
        "unit_count": plan["unit_count"],
        "input_digests": plan["input_digests"],
        "run_state": _run_state(rows),
        "units": rows,
    }
    try:
        validate_artifact(state)
    except ArtifactValidationError as exc:
        raise CurriculumRunStateError("STATE_INVALID") from exc
    return state


def validate_state_against_plan(state: Mapping[str, Any], plan: Mapping[str, Any], plan_raw: bytes) -> None:
    """Validate exact ordered identity and immutable source/plan bindings."""
    try:
        validate_artifact(state)
    except ArtifactValidationError as exc:
        raise CurriculumRunStateError("STATE_INVALID") from exc
    if state.get("schema_version") not in {_STATE_V1, _STATE_V1_1}:
        _fail("STATE_BINDING_INVALID")
    version = state["schema_version"]
    immutable = (
        "run_state_id", "run_plan_id", "run_plan_sha256", "repository_revision", "snapshot_kind",
        "source_metadata", "source_status", "source_run_manifest_sha256", "unknown_files",
        "curriculum_schema_version", "curriculum_sha256", "unit_count", "input_digests",
    )
    expected_values = {
        "run_state_id": state_id(plan, plan_raw), "run_plan_id": plan["run_plan_id"],
        "run_plan_sha256": sha256(plan_raw), "repository_revision": plan["repository_revision"],
        "snapshot_kind": plan["snapshot_kind"], "source_metadata": plan["source_metadata"],
        "source_status": plan["source_status"], "source_run_manifest_sha256": plan["source_run_manifest_sha256"],
        "unknown_files": plan["unknown_files"], "curriculum_schema_version": plan["curriculum_schema_version"],
        "curriculum_sha256": plan["curriculum_sha256"], "unit_count": plan["unit_count"],
        "input_digests": plan["input_digests"],
    }
    if any(state.get(key) != expected_values[key] for key in immutable):
        _fail("STATE_BINDING_INVALID")
    if len(state["units"]) != len(plan["units"]):
        _fail("STATE_BINDING_INVALID")
    seen: set[str] = set()
    for position, (row, plan_row) in enumerate(zip(state["units"], plan["units"], strict=True), start=1):
        identity = plan_row["unit_identity"]
        expected_row = {
            **_state_row(plan_row),
            "execution_state": row.get("execution_state"),
            "attempt_id": row.get("attempt_id"),
            "artifact_digests": row.get("artifact_digests"),
        }
        if version == _STATE_V1_1:
            expected_row.update({
                "repair_disposition": row.get("repair_disposition"),
                "attempts": row.get("attempts"),
            })
        if row != expected_row:
            _fail("STATE_BINDING_INVALID")
        if row["position"] != position or row["unit_id"] in seen:
            _fail("STATE_BINDING_INVALID")
        seen.add(row["unit_id"])
        if row["execution_state"] == "BLOCKED_UPSTREAM" and plan_row["state"] != "BLOCKED":
            _fail("STATE_TRANSITION_INVALID")
        if plan_row["state"] == "BLOCKED" and row["execution_state"] != "BLOCKED_UPSTREAM":
            _fail("STATE_TRANSITION_INVALID")
        if row["execution_state"] in {"PREPARED", "BUILT_DRAFT", "AWAITING_REVIEW", "REVIEWED_DRAFT", "BLOCKED_REVIEW"} and plan_row["state"] == "PLANNED" and row["attempt_id"] not in ({"attempt-0001"} if version == _STATE_V1 else {"attempt-0001", "attempt-0002", "attempt-0003"}):
            _fail("STATE_BINDING_INVALID")
        if plan_row["state"] == "BLOCKED" and row["artifact_digests"]:
            _fail("STATE_BINDING_INVALID")
        if row["execution_state"] in {"BUILT_DRAFT", "AWAITING_REVIEW", "REVIEWED_DRAFT", "BLOCKED_REVIEW"} and not row["artifact_digests"]:
            _fail("STATE_BINDING_INVALID")
        if version == _STATE_V1_1:
            _validate_v1_1_row(row, plan_row)
    expected_run_state = _run_state(state["units"])
    if state["run_state"] != expected_run_state:
        _fail("STATE_TRANSITION_INVALID")


def advance_revision(previous: Mapping[str, Any], previous_raw: bytes, rows: list[Mapping[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    """Return the next immutable snapshot with a digest link to its parent."""
    if len(rows) != len(previous["units"]):
        _fail("STATE_TRANSITION_INVALID")
    state = dict(previous)
    state.update({
        "generated_at": generated_at or utc_now(),
        "state_revision": previous["state_revision"] + 1,
        "previous_state_sha256": sha256(previous_raw),
        "units": rows,
        "run_state": _run_state(rows),
    })
    try:
        validate_artifact(state)
    except ArtifactValidationError as exc:
        raise CurriculumRunStateError("STATE_INVALID") from exc
    return state


def upgrade_to_v1_1(
    previous: Mapping[str, Any], previous_raw: bytes, plan: Mapping[str, Any], plan_raw: bytes,
    *, generated_at: str | None = None,
) -> dict[str, Any]:
    """Append an additive 1.1.0 snapshot without changing the v1 head bytes."""
    if previous.get("schema_version") != _STATE_V1 or dumps_artifact(previous).encode("utf-8") != previous_raw:
        _fail("STATE_CHAIN_INVALID")
    validate_state_against_plan(previous, plan, plan_raw)
    upgraded = copy.deepcopy(previous)
    upgraded["schema_version"] = _STATE_V1_1
    upgraded["generated_at"] = generated_at or utc_now()
    upgraded["state_revision"] = previous["state_revision"] + 1
    upgraded["previous_state_sha256"] = sha256(previous_raw)
    for row in upgraded["units"]:
        row["repair_disposition"] = None
        row["attempts"] = []
        if row["attempt_id"] is not None:
            if row["attempt_id"] != "attempt-0001":
                _fail("STATE_BINDING_INVALID")
            row["attempts"].append({
                "attempt_id": "attempt-0001",
                "execution_state": row["execution_state"],
                "disposition": "ORIGINAL",
                "predecessor_attempt_id": None,
                "predecessor_review_status_sha256": None,
                "predecessor_report_digests": [],
                "artifact_digests": copy.deepcopy(row["artifact_digests"]),
            })
    try:
        validate_state_against_plan(upgraded, plan, plan_raw)
    except CurriculumRunStateError:
        raise
    return upgraded


def _validate_v1_1_row(row: Mapping[str, Any], plan_row: Mapping[str, Any]) -> None:
    attempts = row["attempts"]
    if len(attempts) > 3:
        _fail("STATE_BINDING_INVALID")
    expected_ids = [f"attempt-{number:04d}" for number in range(1, len(attempts) + 1)]
    if [item["attempt_id"] for item in attempts] != expected_ids:
        _fail("STATE_BINDING_INVALID")
    if row["attempt_id"] != (expected_ids[-1] if expected_ids else None):
        _fail("STATE_BINDING_INVALID")
    flattened: list[dict[str, Any]] = []
    for index, attempt in enumerate(attempts):
        expected_predecessor = attempts[index - 1] if index else None
        if index == 0:
            if (attempt["disposition"] != "ORIGINAL" or attempt["predecessor_attempt_id"] is not None
                    or attempt["predecessor_review_status_sha256"] is not None or attempt["predecessor_report_digests"]):
                _fail("STATE_BINDING_INVALID")
        else:
            if (attempt["disposition"] != "SAME_SNAPSHOT_REPAIR"
                    or attempt["predecessor_attempt_id"] != expected_predecessor["attempt_id"]
                    or attempt["predecessor_review_status_sha256"] is None):
                _fail("STATE_BINDING_INVALID")
            expected_reports = [
                {"role": entry["role"], "sha256": entry["sha256"]}
                for entry in expected_predecessor["artifact_digests"]
                if entry["role"] in {"evidence_report", "beginner_report", "factuality_report"}
            ]
            if attempt["predecessor_report_digests"] != expected_reports:
                _fail("STATE_BINDING_INVALID")
        for entry in attempt["artifact_digests"]:
            if f"/attempt-{index + 1:04d}/" not in entry["relative_path"]:
                _fail("STATE_BINDING_INVALID")
        flattened.extend(attempt["artifact_digests"])
    if flattened != row["artifact_digests"]:
        _fail("STATE_BINDING_INVALID")
    if attempts and row["execution_state"] in {"REPAIR_READY", "PREPARED", "BUILT_DRAFT", "AWAITING_REVIEW", "REVIEWED_DRAFT", "BLOCKED_REVIEW", "REVIEW_INCOMPLETE_STOP"}:
        if attempts[-1]["execution_state"] != row["execution_state"]:
            _fail("STATE_BINDING_INVALID")
    if not attempts and (row["attempt_id"] is not None or row["artifact_digests"]):
        _fail("STATE_BINDING_INVALID")
    if row["execution_state"] == "REPAIR_READY" and row["repair_disposition"] != "SAME_SNAPSHOT_REPAIR":
        _fail("STATE_TRANSITION_INVALID")
    if row["execution_state"] == "UPSTREAM_REQUIRED" and row["repair_disposition"] != "UPSTREAM_REQUIRED":
        _fail("STATE_TRANSITION_INVALID")
    if row["execution_state"] == "STOP_UNSUPPORTED" and row["repair_disposition"] != "STOP_UNSUPPORTED":
        _fail("STATE_TRANSITION_INVALID")
    if row["execution_state"] == "REVIEW_INCOMPLETE_STOP" and row["repair_disposition"] != "REVIEW_INCOMPLETE":
        _fail("STATE_TRANSITION_INVALID")
    if row["execution_state"] == "REPAIR_LIMIT_REACHED" and row["repair_disposition"] != "REPAIR_LIMIT_REACHED":
        _fail("STATE_TRANSITION_INVALID")
    if plan_row["state"] == "BLOCKED" and attempts:
        _fail("STATE_BINDING_INVALID")


def _safe_state_dir(path: Path, *, must_exist: bool) -> Path:
    output = path.absolute()
    if output.is_symlink():
        _fail("STATE_DIR_INVALID")
    if must_exist:
        if not output.is_dir():
            _fail("STATE_DIR_INVALID")
    elif output.exists() or os.path.lexists(output):
        _fail("STATE_DIR_EXISTS")
    return output


def load_state_chain(state_dir: Path, plan: Mapping[str, Any], plan_raw: bytes) -> tuple[list[dict[str, Any]], list[bytes]]:
    """Load every canonical contiguous revision and reject an ambiguous head."""
    directory = _safe_state_dir(state_dir, must_exist=True)
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise CurriculumRunStateError("STATE_DIR_INVALID") from exc
    state_entries = []
    for entry in entries:
        if entry.name == "units":
            if entry.is_symlink() or not entry.is_dir():
                _fail("STATE_CHAIN_INVALID")
            continue
        if entry.is_symlink() or not entry.is_file():
            _fail("STATE_CHAIN_INVALID")
        state_entries.append(entry)
    indexed: list[tuple[int, Path]] = []
    for entry in state_entries:
        match = _STATE_NAME.fullmatch(entry.name)
        if match is None:
            _fail("STATE_CHAIN_INVALID")
        indexed.append((int(match.group(1)), entry))
    indexed.sort(key=lambda row: row[0])
    if not indexed or [number for number, _ in indexed] != list(range(1, len(indexed) + 1)):
        _fail("STATE_CHAIN_INVALID")
    states: list[dict[str, Any]] = []
    raws: list[bytes] = []
    parent_digest = None
    for number, path in indexed:
        try:
            raw = path.read_bytes()
            value = _strict_json_loads(raw.decode("utf-8"))
            if not isinstance(value, dict) or dumps_artifact(value).encode("utf-8") != raw:
                _fail("STATE_CHAIN_INVALID")
            validate_state_against_plan(value, plan, plan_raw)
        except (OSError, UnicodeError, ValueError, ArtifactValidationError) as exc:
            if isinstance(exc, CurriculumRunStateError):
                raise
            raise CurriculumRunStateError("STATE_CHAIN_INVALID") from exc
        if value["state_revision"] != number or value["previous_state_sha256"] != parent_digest:
            _fail("STATE_CHAIN_INVALID")
        if not states:
            expected_first = initial_state(plan, plan_raw, generated_at=value["generated_at"])
            if value != expected_first:
                _fail("STATE_CHAIN_INVALID")
        if states:
            previous = states[-1]
            if previous["schema_version"] == _STATE_V1 and value["schema_version"] == _STATE_V1_1:
                expected_upgrade = upgrade_to_v1_1(
                    previous, raws[-1], plan, plan_raw, generated_at=value["generated_at"],
                )
                if expected_upgrade != value:
                    _fail("STATE_TRANSITION_INVALID")
            elif previous["schema_version"] != value["schema_version"]:
                _fail("STATE_CHAIN_INVALID")
            elif not _valid_transition(previous, value):
                _fail("STATE_TRANSITION_INVALID")
        if (states and states[-1]["schema_version"] == value["schema_version"]
                and sum(old != new for old, new in zip(states[-1]["units"], value["units"], strict=True)) > 1):
            _fail("STATE_TRANSITION_INVALID")
        parent_digest = sha256(raw)
        states.append(value)
        raws.append(raw)
    return states, raws


def _valid_transition(previous: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    before = previous["units"]
    after = current["units"]
    if previous["schema_version"] == _STATE_V1_1:
        return _valid_v1_1_transition(before, after)
    allowed = {
        "BLOCKED_UPSTREAM": {"BLOCKED_UPSTREAM"},
        "PLANNED": {"PLANNED", "PREPARED", "BLOCKED_PREREQUISITE"},
        "PREPARED": {"PREPARED", "BUILT_DRAFT"},
        "BUILT_DRAFT": {"BUILT_DRAFT", "AWAITING_REVIEW"},
        "AWAITING_REVIEW": {"AWAITING_REVIEW", "REVIEWED_DRAFT", "BLOCKED_REVIEW"},
        "REVIEWED_DRAFT": {"REVIEWED_DRAFT"},
        "BLOCKED_PREREQUISITE": {"BLOCKED_PREREQUISITE"},
        "BLOCKED_REVIEW": {"BLOCKED_REVIEW"},
    }
    for old, new in zip(before, after, strict=True):
        if new["execution_state"] not in allowed[old["execution_state"]]:
            return False
        if old["attempt_id"] is not None and new["attempt_id"] != old["attempt_id"]:
            return False
        if old["artifact_digests"] and new["artifact_digests"][:len(old["artifact_digests"])] != old["artifact_digests"]:
            return False
    return True


def _valid_v1_1_transition(before: list[Mapping[str, Any]], after: list[Mapping[str, Any]]) -> bool:
    allowed = {
        "BLOCKED_UPSTREAM": {"BLOCKED_UPSTREAM"},
        "PLANNED": {"PLANNED", "PREPARED", "BLOCKED_PREREQUISITE"},
        "PREPARED": {"PREPARED", "BUILT_DRAFT"},
        "BUILT_DRAFT": {"BUILT_DRAFT", "AWAITING_REVIEW"},
        "AWAITING_REVIEW": {"AWAITING_REVIEW", "REVIEWED_DRAFT", "BLOCKED_REVIEW", "REVIEW_INCOMPLETE_STOP"},
        "REVIEWED_DRAFT": {"REVIEWED_DRAFT"},
        "BLOCKED_PREREQUISITE": {"BLOCKED_PREREQUISITE"},
        "BLOCKED_REVIEW": {"BLOCKED_REVIEW", "REPAIR_READY", "UPSTREAM_REQUIRED", "STOP_UNSUPPORTED", "REVIEW_INCOMPLETE_STOP", "REPAIR_LIMIT_REACHED"},
        "REPAIR_READY": {"REPAIR_READY", "PREPARED"},
        "UPSTREAM_REQUIRED": {"UPSTREAM_REQUIRED"},
        "STOP_UNSUPPORTED": {"STOP_UNSUPPORTED"},
        "REVIEW_INCOMPLETE_STOP": {"REVIEW_INCOMPLETE_STOP"},
        "REPAIR_LIMIT_REACHED": {"REPAIR_LIMIT_REACHED"},
    }
    immutable_fields = (
        "position", "unit_id", "unit_identity_sha256", "route", "plan_state",
        "prerequisite_ids", "reason_codes", "blocked_by",
    )
    for old, new in zip(before, after, strict=True):
        if any(old[field] != new[field] for field in immutable_fields):
            return False
        if new["execution_state"] not in allowed[old["execution_state"]]:
            return False
        old_attempts = old["attempts"]
        new_attempts = new["attempts"]
        if len(new_attempts) < len(old_attempts) or len(new_attempts) > len(old_attempts) + 1:
            return False
        if new["artifact_digests"][:len(old["artifact_digests"])] != old["artifact_digests"]:
            return False
        if new["repair_disposition"] != old["repair_disposition"]:
            allowed_dispositions = {
                (None, "SAME_SNAPSHOT_REPAIR"), (None, "UPSTREAM_REQUIRED"),
                (None, "STOP_UNSUPPORTED"), (None, "REVIEW_INCOMPLETE"),
                (None, "REPAIR_LIMIT_REACHED"), ("SAME_SNAPSHOT_REPAIR", "UPSTREAM_REQUIRED"),
                ("SAME_SNAPSHOT_REPAIR", "STOP_UNSUPPORTED"), ("SAME_SNAPSHOT_REPAIR", "REVIEW_INCOMPLETE"),
                ("SAME_SNAPSHOT_REPAIR", "REPAIR_LIMIT_REACHED"),
            }
            if (old["repair_disposition"], new["repair_disposition"]) not in allowed_dispositions:
                return False
        if len(new_attempts) == len(old_attempts) + 1:
            next_attempt = new_attempts[-1]
            if old["execution_state"] == "BLOCKED_REVIEW":
                if (new["execution_state"] != "REPAIR_READY" or next_attempt["execution_state"] != "REPAIR_READY"
                        or next_attempt["disposition"] != "SAME_SNAPSHOT_REPAIR"
                        or next_attempt["predecessor_attempt_id"] != old_attempts[-1]["attempt_id"]):
                    return False
                prior_status = next((item["sha256"] for item in old_attempts[-1]["artifact_digests"]
                                     if item["role"] in {"chapter_review_status", "general_review_status"}), None)
                if next_attempt["predecessor_review_status_sha256"] != prior_status:
                    return False
            elif old["execution_state"] == "PLANNED":
                if (next_attempt["attempt_id"] != "attempt-0001" or next_attempt["execution_state"] != "PREPARED"
                        or next_attempt["disposition"] != "ORIGINAL" or next_attempt["predecessor_attempt_id"] is not None):
                    return False
            else:
                return False
        elif len(new_attempts) == len(old_attempts):
            if old_attempts and new_attempts[:-1] != old_attempts[:-1]:
                return False
            if old_attempts:
                previous_attempt = old_attempts[-1]
                current_attempt = new_attempts[-1]
                attempt_allowed = {
                    "REPAIR_READY": {"REPAIR_READY", "PREPARED"},
                    "PREPARED": {"PREPARED", "BUILT_DRAFT"},
                    "BUILT_DRAFT": {"BUILT_DRAFT", "AWAITING_REVIEW"},
                    "AWAITING_REVIEW": {"AWAITING_REVIEW", "REVIEWED_DRAFT", "BLOCKED_REVIEW", "REVIEW_INCOMPLETE_STOP"},
                    "REVIEWED_DRAFT": {"REVIEWED_DRAFT"},
                    "BLOCKED_REVIEW": {"BLOCKED_REVIEW"},
                    "UPSTREAM_REQUIRED": {"UPSTREAM_REQUIRED"},
                    "STOP_UNSUPPORTED": {"STOP_UNSUPPORTED"},
                    "REVIEW_INCOMPLETE_STOP": {"REVIEW_INCOMPLETE_STOP"},
                    "REPAIR_LIMIT_REACHED": {"REPAIR_LIMIT_REACHED"},
                }
                if (current_attempt["attempt_id"] != previous_attempt["attempt_id"]
                        or current_attempt["disposition"] != previous_attempt["disposition"]
                        or current_attempt["predecessor_attempt_id"] != previous_attempt["predecessor_attempt_id"]
                        or current_attempt["predecessor_review_status_sha256"] != previous_attempt["predecessor_review_status_sha256"]
                        or current_attempt["predecessor_report_digests"] != previous_attempt["predecessor_report_digests"]
                        or current_attempt["execution_state"] not in attempt_allowed[previous_attempt["execution_state"]]
                        or current_attempt["artifact_digests"][:len(previous_attempt["artifact_digests"])] != previous_attempt["artifact_digests"]):
                    return False
        if new["attempt_id"] != (new_attempts[-1]["attempt_id"] if new_attempts else None):
            return False
        if new["artifact_digests"] != [entry for item in new_attempts for entry in item["artifact_digests"]]:
            return False
    return True


def publish_revision(state_dir: Path, state: Mapping[str, Any]) -> bytes:
    """Atomically expose one complete canonical revision without overwriting a race."""
    directory = _safe_state_dir(state_dir, must_exist=True)
    raw = dumps_artifact(state).encode("utf-8")
    path = directory / f"run-state-{state['state_revision']:06d}.json"
    descriptor = -1
    stage_path: Path | None = None
    stage_identity: tuple[int, int] | None = None
    try:
        descriptor, stage_name = tempfile.mkstemp(
            prefix=".curriculum-run-state-", suffix=".tmp", dir=directory.parent,
        )
        stage_path = Path(stage_name)
        stat = os.fstat(descriptor)
        stage_identity = (stat.st_dev, stat.st_ino)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-volume hard-link creation is atomic and fails if another writer
        # already published this revision. The staging file is a sibling so a
        # process crash cannot leave a partial canonical file in the state chain.
        os.link(stage_path, path)
    except FileExistsError as exc:
        raise CurriculumRunStateError("STATE_CHANGED") from exc
    except OSError as exc:
        raise CurriculumRunStateError("STATE_PUBLISH_FAILED") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if stage_path is not None and stage_identity is not None:
            try:
                current = stage_path.lstat()
                if (current.st_dev, current.st_ino) == stage_identity:
                    stage_path.unlink()
            except OSError:
                pass
    return raw


def create_state_dir(path: Path) -> Path:
    """Create a new, empty external directory without following a final link."""
    directory = _safe_state_dir(path, must_exist=False)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise CurriculumRunStateError("STATE_DIR_EXISTS") from exc
    except OSError as exc:
        raise CurriculumRunStateError("STATE_DIR_INVALID") from exc
    return directory


__all__ = [
    "CurriculumRunStateError", "advance_revision", "create_state_dir", "initial_state",
    "load_state_chain", "publish_revision", "sha256", "state_id", "utc_now",
    "upgrade_to_v1_1", "validate_state_against_plan",
]

# Phase 1 Core Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the smallest stable, versioned Project DeepDive artifact contract and prove it against real repository fixture data without changing existing replicate-learning behavior.

**Architecture:** Keep the current installable Skill boundary. JSON schema documents define the public artifact shapes; one standard-library Python module provides registry lookup, actionable validation and canonical JSON round-tripping; a thin CLI exposes it to later pipeline phases. The first fixtures describe committed repository files and intentionally report later quality gates as `NOT_RUN` or `PARTIAL` instead of pretending those phases exist.

**Tech Stack:** Python 3.12 standard library, JSON Schema documents as portable contracts, `unittest`, existing Skill layout.

**Spec:** `PROJECT_DEEPDIVE_SPEC.md`, `ARCHITECTURE.md`, `REQUIREMENTS.md`, `QUALITY_GATES.md`, and `docs/project-deepdive/phase-0-analysis.md`

## Global Constraints

- Preserve all existing `replicate-learning` commands, v2.31 gate behavior and stored artifact compatibility.
- Add no external dependency in Phase 1.
- Every top-level artifact records `artifact_kind`, `schema_version`, `repository_revision`, and `generated_at`.
- Supported Phase 1 major schema version is exactly `1`; unsupported majors fail closed.
- Status values use only `PASS`, `PASS_WITH_WARNINGS`, `PARTIAL`, `FAIL`, or `NOT_RUN` where a quality status is required.
- Project-specific claims never receive E3 unless a recorded runtime command/observation exists.
- Do not create analyzer, adapter, handbook, interview or runtime placeholders.
- Production behavior is written only after its failing test has been observed.

---

### Task 1: Contract test and v1 schema documents

**Files:**
- Create: `skills/replicate-learning/scripts/test_artifact_contract.py`
- Create: `skills/replicate-learning/schemas/README.md`
- Create: `skills/replicate-learning/schemas/v1/project-index.schema.json`
- Create: `skills/replicate-learning/schemas/v1/stack-profile.schema.json`
- Create: `skills/replicate-learning/schemas/v1/evidence.schema.json`
- Create: `skills/replicate-learning/schemas/v1/coverage.schema.json`
- Create: `skills/replicate-learning/schemas/v1/knowledge-graph.schema.json`
- Create: `skills/replicate-learning/schemas/v1/curriculum.schema.json`
- Create: `skills/replicate-learning/schemas/v1/quality-report.schema.json`

**Interfaces:**
- Produces: seven parseable schema documents with `$id`, `$schema`, `title`, `type`, required fields and `additionalProperties` policy.
- Consumes: none.

- [ ] **Step 1: Write tests that enumerate exactly seven schema kinds, parse every file, and assert their common identity/version requirements.**
- [ ] **Step 2: Run `python -m unittest scripts.test_artifact_contract.SchemaDocumentTests -v` from `skills/replicate-learning/`; expect failure because schemas do not exist.**
- [ ] **Step 3: Add the seven minimal schemas and document public/compatibility policy.**
- [ ] **Step 4: Re-run the focused test; expect PASS.**

### Task 2: Standard-library artifact validator and canonical serializer

**Files:**
- Modify: `skills/replicate-learning/scripts/test_artifact_contract.py`
- Create: `skills/replicate-learning/scripts/artifact_contract.py`

**Interfaces:**
- Produces: `ARTIFACT_KINDS`, `ArtifactValidationError`, `schema_path(kind, version)`, `validate_artifact(data)`, `load_artifact(path)`, and `dumps_artifact(data)`.
- Consumes: schemas from Task 1.

- [ ] **Step 1: Add failing tests for valid minimal artifacts, required fields, nested field paths, unknown kinds, unsupported major versions, stable round-trip and non-finite JSON rejection.**
- [ ] **Step 2: Run the focused validator tests and confirm failures are caused by missing implementation.**
- [ ] **Step 3: Implement the minimum JSON-schema subset used by the v1 contracts (`type`, `required`, `properties`, `items`, `enum`, `const`, `pattern`, numeric/string/array bounds, and `additionalProperties`).**
- [ ] **Step 4: Implement canonical UTF-8 JSON output with sorted keys, two-space indentation and a final newline.**
- [ ] **Step 5: Re-run focused tests; expect PASS.**

### Task 3: CLI validation and real v1 fixture set

**Files:**
- Modify: `skills/replicate-learning/scripts/test_artifact_contract.py`
- Create: `skills/replicate-learning/scripts/validate_artifact.py`
- Create: `skills/replicate-learning/tests/fixtures/artifacts/v1/project-index.json`
- Create: `skills/replicate-learning/tests/fixtures/artifacts/v1/stack-profile.json`
- Create: `skills/replicate-learning/tests/fixtures/artifacts/v1/evidence.json`
- Create: `skills/replicate-learning/tests/fixtures/artifacts/v1/coverage.json`
- Create: `skills/replicate-learning/tests/fixtures/artifacts/v1/knowledge-graph.json`
- Create: `skills/replicate-learning/tests/fixtures/artifacts/v1/curriculum.json`
- Create: `skills/replicate-learning/tests/fixtures/artifacts/v1/quality-report.json`

**Interfaces:**
- Produces CLI: `python scripts/validate_artifact.py <path> [<path> ...]`.
- CLI success output contains kind/version and returns 0; validation errors include file plus JSON-style path and return 1.

- [ ] **Step 1: Add failing CLI tests for success, invalid input and multiple files.**
- [ ] **Step 2: Add the seven fixtures using real paths and measured revision `1463a06437fc903edec24722ecbb686a46d8f9da`; later-phase gates must be honest `NOT_RUN`/`PARTIAL`.**
- [ ] **Step 3: Run tests and confirm failure because the CLI is missing.**
- [ ] **Step 4: Implement the thin CLI using `artifact_contract.py`.**
- [ ] **Step 5: Run the CLI across all fixture files and the focused tests; expect PASS.**

### Task 4: Documentation integration and phase verification

**Files:**
- Modify: `README.md`
- Modify: `skills/replicate-learning/SKILL.md`
- Modify: `docs/project-deepdive/phase-0-analysis.md` only if implementation evidence changes a recorded Phase 1 fact.

**Interfaces:**
- Produces documented artifact validation command and an explicit statement that scanning/generation are later phases.
- Consumes: implemented CLI and schemas from Tasks 1–3.

- [ ] **Step 1: Add concise README and Skill routing references only for the implemented validation capability.**
- [ ] **Step 2: Run `python scripts/validate_artifact.py tests/fixtures/artifacts/v1/*.json` using PowerShell-expanded paths.**
- [ ] **Step 3: Run `python -m unittest discover -s scripts -p "test_*.py" -t scripts`.**
- [ ] **Step 4: Run `python scripts/skill_selfcheck.py` and `python scripts/v2_selfcheck.py`.**
- [ ] **Step 5: Run `python -m compileall -q scripts`.**
- [ ] **Step 6: Inspect `git diff --check`, `git diff --stat`, and the complete Phase 1 diff.**
- [ ] **Step 7: Request an independent code review against this plan and repair Critical/Important findings before moving to Phase 2.**

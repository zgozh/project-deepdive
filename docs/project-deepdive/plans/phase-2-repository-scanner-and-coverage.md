# Phase 2 Repository Scanner & Coverage Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with test-first development. Do not commit, push, change remotes, or modify unrelated user work. Checkboxes (`- [ ]`) are the handoff record.

**Goal:** Build a deterministic Git tracked-file scanner that emits compatible v1.1 `project-index.json` and `coverage.json` artifacts, resolves explicit classification unknowns, and audits G01-relevant integrity without changing existing replicate-learning behavior.

**Architecture:** Keep the existing installable Skill boundary. Use a pure file-classification module, a Git-backed inventory module, a reusable semantic audit module, and two thin CLIs. Reuse Phase 1's schema registry, validator and canonical serializer; defer AST, stack and framework adapters to Phase 3.

**Tech Stack:** Python 3.12 standard library, Git CLI with NUL-delimited output, JSON Schema contracts, `unittest`, existing `skills/replicate-learning/` layout.

**Spec:** `docs/project-deepdive/phase-2-design.md`

## Global Constraints

- Preserve every existing `replicate-learning` command, v2.31 gate, fixture and test.
- Add no runtime dependency.
- Analyze tracked files only; a non-Git root fails explicitly in Phase 2.
- Never execute target-repository code or use `shell=True`.
- Never emit or log target file bodies or environment values.
- All artifact paths are analysis-root-relative POSIX paths with no escape.
- Old `1.0.0` artifacts remain valid; scanner output for `project-index` and `coverage` is `1.1.0`.
- Phase 2 never emits coverage classification `COVERED`.
- Ambiguity is `UNKNOWN`; final completeness requires an explicit evidence-bearing classification.
- Production behavior is written only after the corresponding test has failed for the expected reason.
- The implementation worker must not commit, push or change Git remotes.

---

### Task 0: Establish the Phase 1 entry gate

**Files:**
- Read only: `skills/replicate-learning/scripts/artifact_contract.py`
- Read only: `skills/replicate-learning/scripts/test_artifact_contract.py`
- Read only: `skills/replicate-learning/schemas/README.md`
- Read only: `skills/replicate-learning/schemas/v1/*.schema.json`

**Interfaces:**
- Consumes: Phase 1 artifact validation API and seven real fixture artifacts.
- Produces: a recorded baseline; no file changes.

- [ ] **Step 1: Record the worktree without modifying it.**

Run from repository root:

```powershell
git status --short
git diff --stat
```

Expected: existing user/Phase 0/Phase 1 changes may be present. Save the output for the final handoff and do not reset, stage or clean it.

- [ ] **Step 2: Run the Phase 1 artifact gate.**

Run from `skills/replicate-learning/`:

```powershell
$fixtures = Get-ChildItem tests/fixtures/artifacts/v1/*.json | ForEach-Object FullName
python scripts/validate_artifact.py @fixtures
python -m unittest scripts.test_artifact_contract -v
```

Expected: seven CLI passes and the focused tests pass. If not, stop and report the exact pre-existing failure.

- [ ] **Step 3: Run the full baseline suite.**

```powershell
python -W ignore::ResourceWarning -m unittest discover -s scripts -p "test_*.py" -t scripts
python scripts/skill_selfcheck.py
python scripts/v2_selfcheck.py
```

Expected at planning time: 190 unit tests, 122 skill self-check entries and 6 V2 entries pass. If repository evolution has changed the counts, report the observed counts and prove all discovered checks passed rather than editing tests to restore old numbers.

### Task 1: Evolve only the required artifact contracts to v1.1

**Files:**
- Modify: `skills/replicate-learning/scripts/test_artifact_contract.py`
- Modify: `skills/replicate-learning/schemas/v1/project-index.schema.json`
- Modify: `skills/replicate-learning/schemas/v1/coverage.schema.json`
- Modify: `skills/replicate-learning/schemas/README.md`

**Interfaces:**
- Consumes: `validate_artifact(data)`, `dumps_artifact(data)` and current v1.0 fixtures.
- Produces: `project-index` and `coverage` schema support for exactly `1.0.0` and `1.1.0`; all other artifact versions remain unchanged.

- [ ] **Step 1: Add failing compatibility tests.**

Add tests equivalent to:

```python
def test_project_index_accepts_legacy_1_0_and_scanner_1_1(self):
    legacy = minimal_artifacts()["project-index"]
    self.assertIs(legacy, validate_artifact(legacy))

    current = copy.deepcopy(legacy)
    current["schema_version"] = "1.1.0"
    current["files"][0].update({
        "content_kind": "text",
        "media_type": "text/x-python",
        "extension": "py",
        "vcs_object_id": "a" * 40,
    })
    self.assertIs(current, validate_artifact(current))

def test_coverage_accepts_legacy_1_0_and_scanner_1_1(self):
    legacy = minimal_artifacts()["coverage"]
    self.assertIs(legacy, validate_artifact(legacy))

    current = copy.deepcopy(legacy)
    current["schema_version"] = "1.1.0"
    current["classification_policy_version"] = "1.0.0"
    current["entries"][0].update({
        "secondary_surfaces": [],
        "rule_id": "path:test",
    })
    self.assertIs(current, validate_artifact(current))

def test_unlisted_v1_minor_versions_fail_closed(self):
    artifact = minimal_artifacts()["project-index"]
    artifact["schema_version"] = "1.2.0"
    with self.assertRaisesRegex(ArtifactValidationError, "schema_version"):
        validate_artifact(artifact)
```

Also assert that `evidence` at `1.1.0` is rejected, proving the minor version was not globally enabled.

- [ ] **Step 2: Run only the new tests and observe the expected red state.**

```powershell
python -m unittest scripts.test_artifact_contract.SchemaDocumentTests scripts.test_artifact_contract.ArtifactValidationTests -v
```

Expected: new v1.1 artifacts fail at `$.schema_version` or their new fields.

- [ ] **Step 3: Make the minimum schema changes.**

For `project-index`:

- replace the exact version constant with an enum containing `1.0.0` and `1.1.0`;
- add optional `content_kind`, `media_type`, `extension`, and `vcs_object_id` properties to file entries;
- keep every v1.0 required field and `additionalProperties: false`.

For `coverage`:

- accept only `1.0.0` and `1.1.0`;
- add optional top-level `classification_policy_version`;
- add optional entry fields `secondary_surfaces` and `rule_id`;
- reuse exactly the existing surface vocabulary.

Do not relax unrelated artifact schemas and do not add unsupported schema keywords.

- [ ] **Step 4: Re-run contract tests and independently validate schema syntax.**

```powershell
python -m unittest scripts.test_artifact_contract -v
python -c "import json,pathlib; [json.loads(p.read_text(encoding='utf-8')) for p in pathlib.Path('schemas/v1').glob('*.json')]; print('PASS schema JSON')"
```

Expected: all focused tests pass and all schema documents parse.

- [ ] **Step 5: Record the task checkpoint.**

```powershell
git diff --check -- skills/replicate-learning/schemas skills/replicate-learning/scripts/test_artifact_contract.py
git diff --stat -- skills/replicate-learning/schemas skills/replicate-learning/scripts/test_artifact_contract.py
```

Expected: no whitespace errors. Do not commit.

### Task 2: Implement pure file typing and coverage classification

**Files:**
- Create: `skills/replicate-learning/scripts/file_classification.py`
- Create: `skills/replicate-learning/scripts/test_file_classification.py`

**Interfaces:**
- Consumes: normalized file facts and optional exact-path overrides.
- Produces:

```python
CLASSIFICATION_POLICY_VERSION: str = "1.0.0"

@dataclass(frozen=True)
class FileFacts:
    path: str
    content_kind: str
    media_type: str
    extension: str

@dataclass(frozen=True)
class CoverageOverride:
    surface: str
    classification: str
    reason: str

@dataclass(frozen=True)
class CoverageDecision:
    surface: str
    secondary_surfaces: tuple[str, ...]
    classification: str
    teaching_status: str
    reason: str
    rule_id: str

def classify_file(
    facts: FileFacts,
    override: CoverageOverride | None = None,
) -> CoverageDecision: ...

def detect_media_type(path: str, content_kind: str, sample: bytes) -> tuple[str, str]: ...
```

- [ ] **Step 1: Write table-driven tests for deterministic typing.**

The test table must include these exact behavioral cases:

| Path/input | Expected result |
|---|---|
| `src/app.py` | extension `py`, `text/x-python` |
| `Dockerfile` | empty extension, `text/x-dockerfile` |
| `public/logo.png` with PNG prefix | `image/png` |
| `notes.unknown` containing UTF-8 text | `text/plain` fallback |
| unknown bytes containing NUL | `application/octet-stream` |
| gitlink fact | `application/x-gitlink` |

Do not assert host `mimetypes` output.

- [ ] **Step 2: Write table-driven tests for ordered surface rules.**

At minimum assert:

```python
CASES = {
    ".github/workflows/ci.yml": ("ci", "CLASSIFIED"),
    "Dockerfile": ("infrastructure", "CLASSIFIED"),
    "src/test/java/acme/AppTest.java": ("test", "CLASSIFIED"),
    "src/main/resources/db/migration/V1__init.sql": ("migration", "CLASSIFIED"),
    "frontend/src/pages/Home.tsx": ("frontend", "CLASSIFIED"),
    "backend/routes/users.py": ("backend", "CLASSIFIED"),
    "api/openapi.yml": ("api", "CLASSIFIED"),
    "domain/order.py": ("domain", "CLASSIFIED"),
    "agent/tools/search.py": ("agent", "CLASSIFIED"),
    "db/schema.sql": ("database", "CLASSIFIED"),
    "tests/fixtures/user.json": ("fixture", "CLASSIFIED"),
    "config/settings.yml": ("configuration", "CLASSIFIED"),
    "scripts/release.py": ("script", "CLASSIFIED"),
    "cli/main.py": ("cli", "CLASSIFIED"),
    "mcp/server.py": ("mcp", "CLASSIFIED"),
    "docs/overview.md": ("documentation", "CLASSIFIED"),
    "assets/logo.svg": ("asset", "CLASSIFIED"),
    ".pre-commit-config.yaml": ("tooling", "CLASSIFIED"),
    "vendor/lib/source.c": ("vendor", "VENDOR"),
    "dist/assets/app.js": ("generated", "GENERATED"),
    ".env": ("environment", "IGNORED_WITH_REASON"),
    ".env.example": ("environment", "CLASSIFIED"),
    "mystery.odd": ("other", "UNKNOWN"),
}
```

For all non-unknown decisions assert non-empty `reason` and `rule_id`. Assert generated/vendor/ignored decisions use `NOT_APPLICABLE`; owned and unknown files use `LOCATED`. Assert no built-in or override case returns `COVERED`.

- [ ] **Step 3: Run the new tests and observe import failure.**

```powershell
python -m unittest scripts.test_file_classification -v
```

Expected: failure because `file_classification.py` does not exist.

- [ ] **Step 4: Implement immutable facts/decision types, internal maps and precedence.**

Use internal exact-name, extension and path-component maps. Normalize input before classification; do not perform filesystem or Git I/O in `classify_file`. Sort and deduplicate secondary surfaces before returning them.

Use exactly the stable rule IDs listed in `docs/project-deepdive/phase-2-design.md`; do not generate rule IDs from arbitrary path text.

- [ ] **Step 5: Add negative override tests and minimal override behavior.**

Assert an override can resolve `mystery.odd`, but empty reasons and classifications `UNKNOWN` or `COVERED` are rejected with actionable `ValueError` messages.

- [ ] **Step 6: Run focused tests and checkpoint the diff.**

```powershell
python -m unittest scripts.test_file_classification -v
git diff --check -- skills/replicate-learning/scripts/file_classification.py skills/replicate-learning/scripts/test_file_classification.py
```

Expected: focused tests pass; no commit.

### Task 3: Implement Git-backed repository inventory

**Files:**
- Create: `skills/replicate-learning/scripts/repository_scan.py`
- Create: `skills/replicate-learning/scripts/test_repository_scan.py`

**Interfaces:**
- Consumes: a Git analysis root, explicit snapshot kind and generated timestamp.
- Produces:

```python
@dataclass(frozen=True)
class GitContext:
    analysis_root: Path
    repository_root: Path
    analysis_prefix: str
    revision: str
    dirty: bool

@dataclass(frozen=True)
class IndexedFile:
    path: str
    byte_count: int
    sha256: str
    content_kind: str
    media_type: str
    extension: str
    vcs_object_id: str

@dataclass(frozen=True)
class ScanOptions:
    root: Path
    snapshot_kind: str
    generated_at: str
    overrides_path: Path | None = None

@dataclass(frozen=True)
class ScanArtifacts:
    project_index: dict[str, object]
    coverage: dict[str, object]

class RepositoryScanError(RuntimeError): ...

def discover_git_context(root: Path) -> GitContext: ...
def enumerate_indexed_files(context: GitContext, snapshot_kind: str) -> tuple[IndexedFile, ...]: ...
def scan_repository(options: ScanOptions) -> ScanArtifacts: ...
```

- [ ] **Step 1: Add a temporary-Git-repository test helper.**

The helper must:

```python
def init_git_repo(root: Path, files: dict[str, bytes]) -> str:
    # write only below root
    # git init
    # git config user.name "Project DeepDive Test"
    # git config user.email "deepdive@example.invalid"
    # git config core.autocrlf false
    # git add --all
    # git commit -m fixture
    # return full HEAD
```

All Git subprocesses use argument arrays, `check=False` plus explicit return-code handling, captured stdout/stderr and no shell.

- [ ] **Step 2: Add failing tests for Git context and tracked enumeration.**

Tests must cover:

- root repository and nested analysis root;
- untracked file exclusion;
- spaces, Unicode and newline characters in tracked paths;
- lexicographically sorted POSIX artifact paths;
- full revision capture;
- tracked dirty-state changes below the analysis root;
- invalid UTF-8 Git path rejection with an actionable error on platforms that permit such a fixture;
- non-Git root error;
- unresolved index stage error.

Run:

```powershell
python -m unittest scripts.test_repository_scan.GitInventoryTests -v
```

Expected: import or missing-symbol failure.

- [ ] **Step 3: Implement NUL-safe Git discovery and enumeration.**

Use `git rev-parse --show-toplevel`, `git rev-parse HEAD`, and `git status --porcelain=v1 -z --untracked-files=no`. Worktree mode enumerates the current index with `git ls-files --stage -z`; git-tree mode enumerates the declared commit with `git ls-tree -r -z`. Parse modes, object types and IDs without splitting paths on whitespace. Reject worktree index stage numbers other than zero. A staged addition/deletion must affect worktree-mode membership but not leak into git-tree-mode membership.

- [ ] **Step 4: Add failing snapshot-content tests.**

Tests must prove:

- worktree mode hashes modified tracked bytes and marks dirty;
- git-tree mode hashes committed bytes after the same modification;
- a deleted tracked worktree file raises an error;
- fixed content yields expected byte counts and SHA-256;
- symlink behavior is covered where the test platform permits it;
- a gitlink payload, when constructed, hashes the ASCII object ID and is not traversed.

- [ ] **Step 5: Implement streaming worktree reads and Git-object reads.**

Read regular worktree files in chunks for hashing. In worktree mode, retain the index object ID even when current worktree bytes differ. For git-tree mode, read the object IDs returned by `git ls-tree` for the selected revision; never substitute the current index object ID. Use a bounded content prefix for media detection. Do not write target files.

- [ ] **Step 6: Run inventory tests.**

```powershell
python -m unittest scripts.test_repository_scan.GitInventoryTests -v
```

Expected: all inventory and snapshot tests pass.

### Task 4: Load exact-path overrides and assemble both artifacts

**Files:**
- Modify: `skills/replicate-learning/scripts/repository_scan.py`
- Modify: `skills/replicate-learning/scripts/test_repository_scan.py`

**Interfaces:**
- Consumes: `IndexedFile` entries and `classify_file`.
- Produces: `load_coverage_overrides(path, tracked_paths)` and complete `ScanArtifacts` using schema `1.1.0`.

- [ ] **Step 1: Add strict override-loader tests.**

Explicitly test:

- valid exact-path override;
- duplicate JSON object key;
- invalid UTF-8;
- unsupported override schema version;
- absolute and `..` paths;
- path not present in tracked set;
- empty reason;
- forbidden `COVERED` and `UNKNOWN` classifications;
- one override is applied exactly once.

Use a duplicate-preserving JSON loader strategy consistent with `artifact_contract.py`; do not silently accept last-key-wins input.

- [ ] **Step 2: Run the override tests and observe failure.**

```powershell
python -m unittest scripts.test_repository_scan.CoverageOverrideTests -v
```

- [ ] **Step 3: Implement the loader and artifact assembly.**

The generated artifacts must have:

```python
assert project_index["artifact_kind"] == "project-index"
assert project_index["schema_version"] == "1.1.0"
assert project_index["project"]["root"] == "."
assert project_index["file_count"] == len(project_index["files"])

assert coverage["artifact_kind"] == "coverage"
assert coverage["schema_version"] == "1.1.0"
assert coverage["classification_policy_version"] == "1.0.0"
assert coverage["tracked_file_count"] == len(project_index["files"])
assert coverage["unknown_count"] == sum(
    entry["classification"] == "UNKNOWN" for entry in coverage["entries"]
)
```

Both arrays use identical path order and identical `repository_revision`/`generated_at` values. Validate both in memory with `validate_artifact` before returning.

- [ ] **Step 4: Add and pass fixed-time determinism tests.**

Run the same scan twice with `generated_at="2026-09-22T00:00:00Z"` and assert `dumps_artifact` returns byte-identical strings for both artifacts.

```powershell
python -m unittest scripts.test_repository_scan -v
```

### Task 5: Implement cross-artifact and snapshot coverage audit

**Files:**
- Create: `skills/replicate-learning/scripts/coverage_audit.py`
- Create: `skills/replicate-learning/scripts/test_coverage_audit.py`

**Interfaces:**
- Consumes: validated or untrusted project-index/coverage mappings and analyzed root.
- Produces:

```python
@dataclass(frozen=True)
class AuditViolation:
    code: str
    path: str
    message: str

@dataclass(frozen=True)
class CoverageAuditResult:
    status: str
    measurements: dict[str, int]
    violations: tuple[AuditViolation, ...]

def audit_coverage(
    project_index: Mapping[str, object],
    coverage: Mapping[str, object],
    root: Path,
    require_complete: bool = False,
) -> CoverageAuditResult: ...
```

Stable violation codes are:

```text
SCHEMA_INVALID
REVISION_MISMATCH
TIMESTAMP_MISMATCH
COUNT_MISMATCH
DUPLICATE_PATH
PATH_SET_MISMATCH
UNSAFE_PATH
V11_FIELD_MISSING
SURFACE_INVALID
REASON_MISSING
UNKNOWN_COUNT_MISMATCH
UNKNOWN_REMAINS
SNAPSHOT_FILE_MISSING
SNAPSHOT_SIZE_MISMATCH
SNAPSHOT_HASH_MISMATCH
GIT_OBJECT_MISSING
```

- [ ] **Step 1: Add failing semantic-audit tests.**

Build artifacts from a temporary repository, then mutate one property per subtest. Assert the exact violation code for:

- a missing coverage entry;
- duplicate path;
- count mismatch;
- revision mismatch;
- generated/vendor/ignored entry without a reason;
- unknown count mismatch;
- `require_complete=True` with one unknown;
- missing worktree file;
- modified worktree bytes;
- missing git-tree object/path.

- [ ] **Step 2: Run and observe the expected red state.**

```powershell
python -m unittest scripts.test_coverage_audit -v
```

- [ ] **Step 3: Implement accumulation without early semantic exit.**

Schema-invalid roots may return `SCHEMA_INVALID` without unsafe deeper access. For schema-valid artifacts, collect all independent violations in stable code/path order. Status rules:

- no violations and zero unknowns → `PASS`;
- no structural violations but unknowns remain and completeness is not required → `PARTIAL`;
- any violation → `FAIL`.

- [ ] **Step 4: Add positive audit tests for both snapshot modes and resolved overrides.**

Assert measurements include at least `indexed_files`, `coverage_entries`, and `unknown_files`.

- [ ] **Step 5: Run focused tests and diff check.**

```powershell
python -m unittest scripts.test_coverage_audit -v
git diff --check -- skills/replicate-learning/scripts/coverage_audit.py skills/replicate-learning/scripts/test_coverage_audit.py
```

### Task 6: Add thin scan and audit CLIs with staged publication

**Files:**
- Create: `skills/replicate-learning/scripts/scan_repository.py`
- Create: `skills/replicate-learning/scripts/validate_coverage.py`
- Create: `skills/replicate-learning/scripts/test_repository_scan_cli.py`

**Interfaces:**
- Produces:

```text
python scripts/scan_repository.py --root ROOT --out OUT [options]
python scripts/validate_coverage.py --project-index FILE --coverage FILE --root ROOT [--require-complete]
```

- [ ] **Step 1: Add CLI parsing and exit-code tests before implementation.**

Test exact outcomes:

- complete scan: exit `0`, two files, summary includes `PASS`;
- honest unknown without strict flag: exit `0`, summary includes `PARTIAL` and count;
- same unknown with `--require-complete`: exit `1` and existing outputs remain byte-for-byte unchanged;
- non-Git root: exit `2` with no traceback and no outputs;
- malformed override: exit `2` with path and reason;
- audit violation: validator exit `1` and stable code in stdout;
- argparse misuse: exit `2`.

- [ ] **Step 2: Run the CLI tests and observe missing-script failures.**

```powershell
python -m unittest scripts.test_repository_scan_cli -v
```

- [ ] **Step 3: Implement thin CLIs.**

The CLIs parse arguments, call the library, format results and map exceptions to exit codes. They contain no classification rules. Catch expected operational/validation errors; unexpected programming errors retain a traceback during tests rather than being mislabeled as user errors.

- [ ] **Step 4: Implement staged output publication.**

Build, canonicalize and audit both documents before touching final files. Write temporary files in `--out`, flush and close them, then replace `project-index.json` and `coverage.json`. Do not delete unrelated destination files. Clean only temporary files created by the current invocation after a handled failure.

- [ ] **Step 5: Run CLI tests.**

```powershell
python -m unittest scripts.test_repository_scan_cli -v
```

Expected: all CLI and no-partial-overwrite tests pass.

### Task 7: Add the three mandatory repository fixture families

**Files:**
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/python/pyproject.toml`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/python/src/sample/service.py`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/python/tests/test_service.py`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/python/scripts/dev.py`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/python/.github/workflows/ci.yml`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/python/Dockerfile`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/python/docs/overview.md`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/java/pom.xml`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/java/src/main/java/example/OrderService.java`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/java/src/test/java/example/OrderServiceTest.java`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/java/src/main/resources/application.yml`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/java/src/main/resources/db/migration/V1__init.sql`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/java/.mvn/wrapper/maven-wrapper.properties`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/java/target/generated.txt`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/package.json`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/package-lock.json`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/src/pages/Home.tsx`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/src/components/Button.tsx`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/src/api/client.ts`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/vite.config.ts`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/tests/Home.test.tsx`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/public/logo.svg`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/dist/assets/app.js`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/vendor/tiny.js`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/frontend/mystery.odd`
- Create: `skills/replicate-learning/tests/fixtures/repository_scanner/overrides/frontend.json`
- Modify: `skills/replicate-learning/scripts/test_repository_scan.py`
- Modify: `skills/replicate-learning/scripts/test_coverage_audit.py`
- Modify: `skills/replicate-learning/scripts/test_repository_scan_cli.py`

**Interfaces:**
- Consumes: fixture templates copied into temporary Git repositories.
- Produces: end-to-end evidence for Python, Java and frontend-containing repositories.

- [ ] **Step 1: Create minimal, valid-text fixture templates.**

Fixture code exists only to provide realistic paths and contents. It does not need runnable application frameworks or external dependencies. Do not add package caches, real binaries or dependency trees.

- [ ] **Step 2: Add fixture-copy and Git-init integration tests.**

For each family, copy the template, initialize/commit it, scan it and assert:

```python
raw_paths = run_git_bytes(repo, "ls-files", "-z").stdout
git_count = len([path for path in raw_paths.split(b"\0") if path])
self.assertEqual(git_count, result.project_index["file_count"])
self.assertEqual(git_count, result.coverage["tracked_file_count"])
self.assertEqual(
    {entry["path"] for entry in result.project_index["files"]},
    {entry["path"] for entry in result.coverage["entries"]},
)
```

Use NUL-delimited counting in the actual helper so a newline in a path remains one path.

- [ ] **Step 3: Add meaningful surface assertions.**

Assert the fixtures collectively expose at least:

```text
backend, frontend, database, migration, test, fixture/configuration,
infrastructure, build, script, ci, documentation, asset, lockfile,
generated, vendor
```

If `fixture` is not naturally present within the copied target repositories, do not fake it merely to satisfy the list; record that fixture is the host role while target files exercise the remaining surfaces.

- [ ] **Step 4: Prove the unknown-resolution flow.**

Scan frontend without overrides and assert `mystery.odd` is `UNKNOWN` and strict audit fails. Scan again with `overrides/frontend.json`; assert it is `CLASSIFIED`, rule ID is `override:exact-path`, reason is preserved, `unknown_count` is zero and strict audit passes.

- [ ] **Step 5: Run all Phase 2 focused tests.**

```powershell
python -m unittest scripts.test_file_classification scripts.test_repository_scan scripts.test_coverage_audit scripts.test_repository_scan_cli -v
```

Expected: all focused tests pass without network access.

### Task 8: Integrate documentation and perform the handoff verification

**Files:**
- Modify: `README.md`
- Modify: `skills/replicate-learning/SKILL.md`
- Modify: `skills/replicate-learning/schemas/README.md`
- Create: `skills/replicate-learning/references/coverage-policy.md`
- Create: `docs/project-deepdive/phase-2-verification.md`

**Interfaces:**
- Consumes: real implemented CLI flags and test results.
- Produces: user-facing commands, classification/override policy, and an evidence-only Phase 2 verification record.

- [ ] **Step 1: Document only implemented behavior.**

README and SKILL must show one minimal scan command and one strict audit command. `coverage-policy.md` records rule precedence, stable rule IDs, snapshot semantics, override JSON shape, sensitive-file behavior and the distinction between `CLASSIFIED` and `COVERED`.

- [ ] **Step 2: Run one real CLI smoke scan against a temporary committed fixture.**

Use the frontend fixture with its override and a fixed timestamp. Record commands, exit codes, indexed count, unknown count and output paths. Do not label this as a dogfood run.

- [ ] **Step 3: Run the complete verification matrix from `skills/replicate-learning/`.**

```powershell
$fixtures = Get-ChildItem tests/fixtures/artifacts/v1/*.json | ForEach-Object FullName
python scripts/validate_artifact.py @fixtures
python -W ignore::ResourceWarning -m unittest discover -s scripts -p "test_*.py" -t scripts
python scripts/skill_selfcheck.py
python scripts/v2_selfcheck.py
python -m compileall -q scripts
```

Record exact observed test counts and exit codes. Resource warnings may be reported separately but must not be hidden if newly introduced by Phase 2.

- [ ] **Step 4: Run repository-level diff checks.**

From repository root:

```powershell
git diff --check
git diff --stat
git status --short
```

Inspect the complete diff. Specifically check accidental Phase 1 fixture rewrites, formal-spec edits, unrelated formatting and secret-like fixture content.

- [ ] **Step 5: Fill `phase-2-verification.md` with evidence, not predicted results.**

The record includes:

- baseline and final commands with exit codes;
- focused/full test counts;
- fixture file counts and surface sets;
- strict unknown failure and override pass;
- negative missing-file/path-set evidence;
- known warnings and limitations;
- changed-file list;
- explicit statement that no commit/push/remote mutation occurred.

- [ ] **Step 6: Return the review packet and stop.**

Do not begin Phase 3. Return the output contract defined in `docs/project-deepdive/prompts/phase-2-deepseek-implementation.md` so the Principal Engineer can review the actual diff and evidence.

## Plan self-review

- Spec coverage: R-003/R-004/R-005/R-044, G01/G02, AC-01/AC-04/AC-05 and every Phase 2 Done When item map to an explicit task.
- Scope: no Phase 3 analyzer/adapter, runtime, graph, handbook or dogfood implementation is included.
- Compatibility: old v1.0 fixtures are preserved; only two artifact kinds gain a named compatible minor.
- Type consistency: the scanner returns `ScanArtifacts`; the audit consumes the emitted mappings; both CLIs are thin callers of those libraries.
- Evidence integrity: the plan requires observed red/green tests, fixed-time determinism, negative cases and exact command outputs.

# Project DeepDive Phase 0 — Baseline, Archaeology, and Migration Map

> Status: Phase 0 implementation record  
> Repository revision: `1463a06437fc903edec24722ecbb686a46d8f9da`  
> Recorded: 2026-09-22  
> Evidence convention: repository facts below are E1 (source), E2 (config/test), or E3 (observed command output). Proposed changes are explicitly labeled as decisions.

## 1. Executive finding

The existing repository is not an application framework waiting to be filled in. It is a mature, installable Agent Skill centered on a deterministic teaching-batch compiler and a heavily defended quality gate. Its strongest reusable kernel is:

```text
target repository
→ persisted project maps
→ evidence manifest
→ 17-section teaching batch skeleton
→ source injection with real line numbers
→ deterministic rebuild
→ machine-readable gate result
→ hash-bound gate stamping
→ atomic publication of learning state
```

Project DeepDive should therefore grow around this kernel. The first migration step is a versioned intermediate-artifact contract that the current Skill can produce and consume. It is not a parallel rewrite and does not create the aspirational architecture tree in `ARCHITECTURE.md` ahead of working behavior.

## 2. Baseline evidence

### Repository state

- E3: `git ls-files` reports **115 tracked files**.
- E3: the filesystem contains **124 non-`.git` files in 31 directories**.
- E3: 9 root files are currently untracked: the eight Project DeepDive contract documents plus `text.md`.
- E3: `git rev-parse HEAD` reports `1463a06437fc903edec24722ecbb686a46d8f9da` on branch `master`.
- E3: `origin` currently points to `git@github.com:zgozh/replicate-learning.git`; the user-authorized Project DeepDive remote has not yet replaced it.
- E2: no repository CI workflow, Python packaging manifest, Java build manifest, Node manifest, Dockerfile, or Compose manifest exists in this repository.
- E2: the implementation uses Python standard-library scripts and Markdown/JSON contracts under `skills/replicate-learning/`.

### Baseline commands

Run from `skills/replicate-learning/` on Python 3.12.4:

| Command | Observed result | Status |
|---|---:|---|
| `python scripts/skill_selfcheck.py` | 122 checks, 0 failures | PASS |
| `python scripts/v2_selfcheck.py` | 6 contract entries | PASS |
| `python -m unittest discover -s scripts -p "test_*.py" -t scripts` | 165 tests passed in 5.520s | PASS_WITH_WARNINGS |
| `python tests/e2e_scripted_run.py` | scripted fixture pipeline completed; environment-dependent steps retained explicit skip behavior | PASS |

The unittest run emitted pre-existing `ResourceWarning` messages for unclosed files in `publish_batch.py`. This is recorded as baseline debt, not treated as a Phase 1 regression and not repaired outside scope.

## 3. Existing System Map

### Distribution and execution boundary

```text
repository root
├── README.md / historical design and delivery records
└── skills/replicate-learning/
    ├── SKILL.md                 natural-language orchestration entry
    ├── references/              protocols, templates, examples, checklists
    ├── spec/                    quality SSOT and operational records
    ├── scripts/                 deterministic build/gate/publish tools
    ├── docs/                    user-facing method and boundary docs
    ├── examples/                Java and Python teaching examples
    └── tests/fixtures/          Java and Python regression inputs
```

The installed product is the nested `skills/replicate-learning/` directory. Existing commands and relative paths assume this boundary. Moving implementation to a new top-level package immediately would break the documented installation and self-check contracts.

### Current operational data flow

```text
natural-language request
→ SKILL.md intent route
→ target-project archaeology and persisted NOTES state
→ batch_manifest.py (source hashes)
→ new_batch.py (17-section skeleton + stable source slots)
→ batch_preflight.py (plan, coverage, injection safety)
→ author writes complete section parts
→ batch_build.py (rebuild + source injection)
→ gate_lecture.py (versioned deterministic checks)
→ sync_gate_result.py (hash-bound quality stamp + recheck)
→ publish_batch.py (atomic derived-view update)
```

### State and truth sources

- `skills/replicate-learning/spec/00-质量契约.json` is the existing teaching-gate SSOT.
- E2: that SSOT currently contains 55 entries: 33 L1, 18 L2, and 4 L3 requirements.
- `skills/replicate-learning/scripts/lecture_checks.py` owns structured gate result vocabulary and rule registration.
- Target-project disk artifacts, not chat memory, are the resume source of truth.
- Existing source manifests bind teaching output to file hashes and Git revision.
- Existing publication is fail-closed and all-or-nothing for its declared target files.

## 4. Existing Capability Matrix

| Capability | Evidence | Current disposition | Change type | Breaking change |
|---|---|---|---|---|
| Natural-language Skill routing | `SKILL.md`, `study_scope.py` | Preserve | Extend with DeepDive modes later | No |
| Beginner-oriented business-first teaching | README, 17-section template, quality rules | Preserve and strengthen | Add global prerequisite graph and beginner gate | No |
| Complete target-file accounting philosophy | stage workflow, coverage templates | Preserve | Replace manual-only accounting with machine index/coverage artifacts | Artifact migration required |
| Source fidelity and real line numbers | `inject_source.py`, `gate_lecture.py` | Preserve unchanged | Connect evidence IDs later | No |
| Resumable evidence manifests | `batch_manifest.py` | Reuse/refactor | Generalize into repository index without removing batch manifest | No |
| Deterministic preflight/build/gate/publish | batch toolchain | Preserve unchanged through early phases | Feed/consume canonical artifacts incrementally | No |
| Versioned quality contract | `00-质量契约.json`, `lecture_checks.py` | Preserve | Add separate DeepDive artifact schemas and audit gates | No |
| Hash-bound results and anti-vacuous PASS | `lecture_checks.py`, stamping tests | Preserve as design precedent | Reuse semantics in quality report | No |
| Java teaching regression fixture | `tests/fixtures/batch48/` | Preserve | Reuse for Java adapter/dogfood where applicable | No |
| Python injection fixture | `tests/fixtures/py_mini/` | Preserve | It is not sufficient as full Python dogfood | No |
| Full knowledge graph | No implementation found | Add | New canonical artifact and builders | New capability |
| Stack/symbol/route/call analyzers | No general implementation found | Add | Adapter-based analyzers | New capability |
| Beginner prerequisite graph/curriculum | Concept templates only | Add | Structured graph and ordering engine | New capability |
| Handbook compiler from structured facts | Existing batch compiler is prose/source oriented | Refactor/extend | Retain legacy compiler; add fact/evidence compilation stages | Compatible parallel mode |
| Full-stack feature graph | Philosophy exists; no extractor | Add | Frontend analyzer plus cross-boundary relationships | New capability |
| VibeCoding full canonical loop | Existing 8-step workflow and section ⑫ | Preserve and extend | Map to BASELINE…RETRO artifacts | Workflow version change |
| Project-aware interview engine | Section ⑨ content contract only | Add | Graph-backed progressive interview chains | New capability |
| Revision-aware selective rebuild | Batch hashes exist; chapter invalidation does not | Add | Impact graph in Phase 12 | New capability |
| Machine-readable end-to-end quality report | Gate JSON exists only for teaching batches | Refactor/extend | Add top-level quality report without replacing batch result | No |

## 5. Repository Surface Map

### Measured surfaces

| Surface | Measured inventory | Role |
|---|---:|---|
| Markdown | 67 files | Product docs, methods, templates, examples, specs |
| Python | 37 files | 21 operational scripts, script tests, E2E runner, Python fixture |
| Java | 8 files | Real-source regression fixture for a Java teaching batch |
| JSON | 9 files | Quality contracts, gate sample, manifests/plans/expectations |
| Other | `.gitignore`, `LICENSE`, one TXT | Repository policy/license/whitelist |
| Skill scripts path | 33 files | Operational scripts plus colocated unit tests |
| Skill tests path | 25 files | E2E runner and Java/Python fixtures |
| References | 30 files | Execution contracts, templates, samples |
| Skill specs | 7 files | Quality SSOT, workflow, gate operations, incident evidence |
| Skill docs/examples | 7 files | Method, boundaries, archived runbook, golden examples |

### Surface gaps

- No CI/CD configuration is present.
- No declared dependency/build packaging exists for the Python tools.
- No production frontend, database, migration, container, or service runtime exists in this Skill repository; those are target-repository surfaces Project DeepDive must learn to analyze.
- Current fixtures exercise Java source teaching and Python injection edge cases, not two complete Project DeepDive dogfoods.

## 6. Current Skill Workflow

The current Skill defaults to the first book and treats second/third books as explicitly requested scope. A batch follows:

1. start trace and identify target-project state;
2. gather exact source, callers, tests, revision/hashes;
3. generate a 17-section skeleton with stable source slots;
4. preflight annotation/source coverage before injection;
5. write section fragments and rebuild deterministically;
6. run the full batch gate with structured result output;
7. stamp only a matching body/manifest hash;
8. re-run the gate and atomically publish coverage/index/state views.

Compatibility decision: legacy book routing and batch publication remain available while DeepDive modes are introduced. A generic `learn-project` request must not silently reinterpret an existing stored three-book scope, and a legacy “continue” must not discard its persisted scope.

## 7. Existing Teaching Model

The teaching model already contains several DeepDive invariants:

- whole system and business context before local code;
- project/file maps stored on disk;
- business-workflow batching rather than arbitrary directory order;
- complete source injection for core classes;
- callers, callees, usage, upstream/downstream, extension entry points;
- mechanism levels from source evidence through failure evidence;
- explicit separation of source fact, runtime fact, history, external fact, and inference;
- failure paths, tests, VibeCoding prompts, review and known limitations;
- machine gates plus human semantic review.

The main gap is representation: most knowledge exists as Markdown and batch-specific gate structures. Project DeepDive requires canonical versioned artifacts so knowledge can be reused by curriculum, handbook, interview, extension, revision and audit stages.

## 8. Existing Scripts and Tools

The 21 documented operational scripts form six reusable groups:

| Group | Tools | Preserve because |
|---|---|---|
| Scope/state | `study_scope.py`, `batch_trace.py` | explicit scope and measurable resumability |
| Evidence preparation | `batch_manifest.py`, `callsite.py`, `annotate_gaps.py`, `batch_preflight.py` | deterministic evidence and early failure |
| Batch construction | `new_batch.py`, `assemble_batch.py`, `batch_build.py`, `inject_source.py` | reproducible content build and source fidelity |
| Editing/repair | `safe_edit.py`, `fix_lineno.py`, `fix_circled_sections.py`, `polish_fix.py` | bounded repairs with safety guards |
| Verification | `gate_lecture.py`, `gate_all.py`, `lecture_checks.py`, `sync_gate_result.py` | versioned, structured, hash-bound quality evidence |
| Publication/self-check | `publish_batch.py`, `skill_selfcheck.py`, `v2_selfcheck.py` | atomic publication and contract drift detection |

## 9. Existing Test and Validation System

- 12 colocated `test_*.py` modules are discovered by standard `unittest`.
- 165 baseline unit tests cover scope selection, manifests, source slots, rebuild guards, preflight, gate result integrity, version gates, publication atomicity and language-specific injection.
- `skill_selfcheck.py` performs 122 documentation/SSOT/tool consistency checks.
- `v2_selfcheck.py` validates six workflow contract entries.
- `tests/e2e_scripted_run.py` exercises the deterministic batch pipeline and records step timings; environment-dependent real target steps are explicitly skipped rather than fabricated.
- Fixtures include a real Java batch source set and a synthetic Python edge-case module.

## 10. Capabilities To Preserve

1. Nested installable Skill packaging until a tested migration exists.
2. Existing natural-language routing and legacy book-scope semantics.
3. The 17-section teaching-batch contract and v2.31 gate semantics.
4. Real source injection, source hashes, stable slots, and line verification.
5. Distinct scanned/taught/source-verified/build/test/user-accepted states.
6. Fail-closed rebuild, gate stamping and publication.
7. Existing quality SSOT, blood-evidence/incident record, and change discipline.
8. Java and Python regression fixtures and all 165 baseline tests.
9. Disk-first resumability and explicit unknown/unverified states.

## 11. Capabilities To Refactor

1. Manual coverage tables → canonical coverage JSON plus rendered views.
2. Batch manifest → keep unchanged, then adapt into repository-wide project index.
3. Five-category legacy evidence labels → compatibility mapping to E0–E6.
4. Batch-only gate JSON → top-level quality report that can reference batch gate evidence.
5. Prose-first project maps → graph/fact artifacts that can render prose.
6. Eight-step legacy engineering flow → canonical BASELINE through RETRO without deleting the useful legacy teaching material.
7. Flat script growth → introduce focused modules only when a working phase requires them.

## 12. Capabilities To Add

- versioned project index, stack profile, evidence, coverage, knowledge graph, curriculum and quality report contracts;
- repository scanner with zero-unknown final classification;
- language/framework adapters and generic fallbacks;
- symbols, dependencies, routes, call graph and frontend map;
- Claim–Evidence Matrix and provisional/inference semantics;
- prerequisite graph and beginner-first curriculum ordering;
- multi-stage handbook compiler with verifier and beginner critic;
- full VibeCoding artifacts, extension labs and interview chains;
- deterministic cross-artifact auditor;
- safe runtime evidence capture;
- real Python/Agent and Java/enterprise dogfoods;
- revision impact and selective rebuild.

## 13. Compatibility Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Formal Project DeepDive contracts are untracked | implementation contract could be omitted from commits/handoffs | include them in the first coherent Project DeepDive change set |
| Existing remote still names replicate-learning | accidental push/pull against the old product remote | preserve the old URL as `upstream` and bind `origin` to the authorized Project DeepDive remote before first push |
| Renaming/moving `skills/replicate-learning/` | breaks installation paths, references and self-checks | retain path/name through compatibility window; add aliases only with tests |
| Evidence taxonomy mismatch | old artifacts could become unreadable or be mislabeled | explicit legacy-to-E0–E6 mapping; never infer E3 from old prose |
| Coverage status mismatch | old “已讲解/排除” cannot directly satisfy G01 | migrate to new classifications and keep teaching status as a separate dimension |
| “user accepts” vs automated quality status | machine PASS could be mistaken for human acceptance | separate artifact/gate status from engineering acceptance |
| 17-section batch vs new handbook tree | one might replace the other and lose proven depth | legacy batch becomes a source-guide compiler/compatibility output, not discarded |
| No dependency manifest | adding schema library would make installs non-reproducible | Phase 1 uses standard library and JSON contracts; dependency decision deferred |
| E2E fixture depends on optional external Java project for some steps | false full-e2e confidence | preserve SKIP semantics and add self-contained dogfoods later |
| Large `gate_lecture.py` and `skill_selfcheck.py` | unrelated refactors are high-risk | no Phase 1 changes to these files |

## 14. Architecture Conflicts and Rulings

### Conflict A: target architecture tree vs current installable layout

Ruling: treat `ARCHITECTURE.md` section 2 as an incremental destination. Phase 1 adds only `schemas/`, one validator module/CLI, tests, fixtures and documentation inside the existing installable Skill.

### Conflict B: handbook pipeline vs direct batch authoring

Ruling: keep current batch authoring operational. Later phases insert facts/evidence/prerequisites before draft generation. Existing batches remain valid according to their declared contract versions.

### Conflict C: full coverage vocabulary

Ruling: an in-progress scan may use `UNKNOWN`; G01 cannot pass until it reaches zero. Final repository coverage classification is exactly `COVERED`, `CLASSIFIED`, `GENERATED`, `VENDOR`, or `IGNORED_WITH_REASON`. Teaching progress remains a separate field and is not overloaded into coverage classification.

### Conflict D: evidence labels

Ruling: canonical new artifacts use E0–E6. Compatibility imports map `事实-源码→E1`, config/test-backed facts to E2, `事实-实测→E3`, `外部事实→E4`, `事实-历史→E5`, and `推断→E6`. Ambiguous old “实测” text is not upgraded without a command/run record.

### Conflict E: product completion vs current user acceptance rule

Ruling: quality gates determine artifact status; they do not impersonate user acceptance for a development task. Both statuses are stored separately.

## 15. Proposed Migration

```text
existing Skill + gates + fixtures
→ v1 canonical artifact schemas and validator
→ deterministic repository index/coverage scanner
→ adapters and static analysis
→ evidence-linked knowledge graph
→ beginner prerequisite graph and curriculum
→ fact/evidence-driven handbook compiler
→ VibeCoding, extension and interview consumers
→ cross-artifact quality auditor
→ safe runtime evidence
→ two real dogfoods and repair
→ revision-aware selective rebuild
```

The first end-to-end vertical target is deliberately small:

```text
real fixture repository
→ valid project-index/coverage/evidence/graph/curriculum/quality artifacts
→ schema/semantic validation
→ canonical serialization round-trip
```

## 16. Phase-by-Phase Execution Plan

| Phase | Repository-grounded delivery | Primary verification |
|---|---|---|
| 0 | this baseline/migration record and detailed Phase 1 plan | baseline commands, inventory reconciliation |
| 1 | v1 core artifact schemas, standard-library validator/CLI, real fixture set | schema tests, round-trip, version rejection, full regression |
| 2 | tracked-file scanner and deterministic surface classifier | Git count parity, zero unknown after resolution, Python/Java/frontend fixtures |
| 3 | adapter protocol and analyzers chosen from dogfood stacks | real symbols/routes/dependencies with fixture evidence |
| 4 | canonical evidence IDs, claims and graph cross-references | dangling evidence and unsupported critical claim failures |
| 5 | prerequisite graph and beginner curriculum | ordering/undefined-term golden tests |
| 6 | structured handbook compiler and compatibility source-guide output | one coherent draft/audit pipeline |
| 7 | BASELINE…RETRO development artifacts and teaching templates | real extension lab with recorded commands |
| 8 | graph-backed extension and interview engines | actual extension points and connected follow-ups |
| 9 | deterministic gates first, model-assisted gates explicitly separated | machine/human quality reports and false-completeness negatives |
| 10 | safe Deep Mode runtime capture | real command, input, output, log and NOT_RUN/PARTIAL paths |
| 11 | Python/Agent and Java/enterprise dogfood/repair | G21 and AC-19/20 evidence, beginner samples |
| 12 | revision diff and selective invalidation/rebuild | A→B fixture proves only impacted artifacts rebuild |

## 17. Phase 1 Exact Change Scope

Allowed Phase 1 scope:

- add `skills/replicate-learning/schemas/v1/` for the seven minimum artifact schemas;
- add a schema registry and standard-library artifact validator/serializer under `skills/replicate-learning/scripts/`;
- add a CLI that validates one artifact and returns a non-zero status with actionable paths on failure;
- add self-contained, placeholder-free v1 artifacts describing a real committed fixture;
- add unit tests discovered by the existing unittest command;
- document artifact layout, version policy, status vocabulary and compatibility boundary;
- update high-level README/Skill references only after the implementation is real.

Explicitly out of scope:

- repository scanning;
- AST/static analysis;
- runtime execution;
- model calls;
- knowledge generation from arbitrary repositories;
- moving or renaming the existing Skill;
- changing v2.31 teaching-gate behavior;
- adding external dependencies;
- claiming any later Project DeepDive phase is implemented.

## 18. Phase 1 Done When

Phase 1 is complete only when all of the following have fresh evidence:

1. all seven schema files parse and identify artifact kind plus semantic version;
2. valid fixture artifacts pass the CLI and library validator;
3. missing required fields fail with a precise artifact path;
4. unknown artifact kind and unsupported major versions fail closed;
5. serialization → parse → serialization is stable;
6. fixture references contain real paths/IDs and no placeholder tokens;
7. new tests pass;
8. the existing 165-test suite, 122-item self-check and six-entry V2 self-check do not regress;
9. the actual diff is reviewed for compatibility, error paths and architecture consistency;
10. documentation describes only implemented commands and artifacts.

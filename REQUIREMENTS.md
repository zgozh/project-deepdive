# Project DeepDive — Requirements

Keywords **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, **MAY** are normative.

---

## R-001 Global Beginner Default

The system MUST assume an almost-complete beginner unless the user explicitly provides a higher skill level.

It MUST NOT require unstated prerequisite knowledge.

It MUST provide just-in-time explanations for required language, framework, infrastructure, frontend, backend, database and tooling concepts.

---

## R-002 Beginner Prerequisite Detection

For every core learning unit, the system MUST identify prerequisite concepts.

A prerequisite MUST be introduced before it is relied upon.

The system SHOULD avoid teaching unrelated prerequisites far in advance.

---

## R-003 Existing Capability Preservation

The upgrade MUST preserve the valuable behavior of the existing `replicate-learning` system, especially:

- business-first teaching;
- architecture;
- source analysis;
- call chains;
- full-file coverage philosophy;
- framework penetration;
- underlying principles;
- evidence;
- runtime verification;
- modification/extension learning.

Breaking changes MUST be documented.

---

## R-004 Repository-Wide Inventory

The system MUST inventory the complete tracked repository.

It MUST NOT restrict discovery to source directories.

Every file MUST have a final classification.

Final `UNKNOWN` file count MUST be zero.

---

## R-005 Repository Surface Classification

At minimum, detect/classify:

- backend;
- frontend;
- database;
- tests;
- configuration;
- infrastructure;
- build;
- scripts;
- CI/CD;
- docs;
- tooling;
- assets;
- generated;
- vendor.

---

## R-006 Stack Detection

The system MUST detect language/framework/build/dependency stack using repository evidence.

Uncertain detection MUST be represented with confidence/unknown state.

---

## R-007 Cross-Language Architecture

The universal core MUST be language-independent.

Language-specific behavior MUST be implemented as adapters or pluggable analyzers where practical.

---

## R-008 Static Source Analysis

The system SHOULD use AST/tree-sitter/LSP/language-native analysis when available.

It MUST extract enough source structure to support:

- symbols;
- imports;
- dependencies;
- interfaces/inheritance;
- likely calls;
- routes;
- models;
- tests.

---

## R-009 Runtime Analysis

Deep Mode SHOULD run representative project flows when safe and feasible.

If runtime verification cannot be performed, the system MUST state that clearly.

It MUST NOT present static inference as observed runtime behavior.

---

## R-010 Knowledge Graph

The system MUST persist a structured knowledge graph linking:

- files;
- symbols;
- workflows;
- business capabilities;
- modules;
- mechanisms;
- concepts;
- prerequisites;
- evidence;
- tests;
- extension points.

---

## R-011 Evidence Levels

The system MUST support:

- E0 user;
- E1 source;
- E2 config/dependency/test;
- E3 runtime;
- E4 official docs;
- E5 history/issues;
- E6 inference.

E6 MUST be visibly distinguishable from verified project facts.

---

## R-012 Claim-Evidence Matrix

Important handbook claims MUST map to evidence.

Critical architectural/call-chain claims without adequate evidence MUST fail or be marked unverified.

---

## R-013 Business Modeling

The system MUST explain what business/problem the project solves before deep source teaching.

Core workflows MUST identify actors, trigger, major steps, state and result.

---

## R-014 Architecture Modeling

The system MUST explain:

- major modules;
- responsibilities;
- dependencies;
- boundaries;
- data flow;
- request/async flow;
- deployment/infrastructure where relevant.

---

## R-015 Call Chain

Core workflows MUST contain forward call chains.

Core symbols SHOULD include backward caller traces where technically available.

Incomplete graphs MUST be labeled partial.

---

## R-016 Full-Stack Chain

When the project contains a frontend and backend, core user features MUST be traced across both.

The frontend MUST receive first-class coverage.

---

## R-017 Source Teaching

A core symbol explanation MUST cover more than its local implementation.

It SHOULD include:

- why it exists;
- business context;
- callers;
- callees;
- state/data;
- framework behavior;
- lower-level mechanism;
- tests;
- extension impact.

---

## R-018 Mechanism Penetration

For important framework calls, the system MUST NOT stop at “framework X handles it”.

It SHOULD explain the hidden mechanism and a minimal equivalent implementation when pedagogically useful.

---

## R-019 Project-Aware General Knowledge

General interview/CS/framework knowledge SHOULD be linked back to the actual project.

Generic knowledge MUST be labeled as general knowledge rather than project fact.

---

## R-020 Depth Prioritization

The system MUST classify learning depth by importance.

Full repository coverage MUST NOT force equal prose depth for all files.

---

## R-021 Handbook Compilation

The handbook MUST be generated from structured plans/facts/evidence, not as one unconstrained whole-repository prose generation.

Recommended pipeline:

```text
chapter plan
→ facts
→ evidence
→ prerequisites
→ draft
→ verify
→ beginner review
→ final
```

---

## R-022 Beginner Teaching Quality

Each core chapter MUST:

- explain necessary terms;
- provide intuition;
- connect concept to project;
- avoid unexplained abstraction jumps;
- show at least one concrete flow/example where appropriate.

---

## R-023 Commands and Environment

When teaching setup/build/run/test commands, explain:

- what the command does;
- where to run it;
- expected result;
- common failure;
- how it relates to project architecture.

---

## R-024 VibeCoding Workflow

The system MUST implement/teach:

```text
BASELINE
DISCOVER
SPEC
PLAN
SLICE
BUILD
VERIFY
REVIEW
REPAIR
FULL-CHAIN
RETRO
```

---

## R-025 Prompt Contract

Core coding-agent prompts SHOULD use:

- GOAL;
- CONTEXT;
- CONSTRAINTS;
- SCOPE;
- EVIDENCE;
- VERIFICATION;
- DONE WHEN;
- OUTPUT CONTRACT.

---

## R-026 Deterministic Verification

Tests/build/lint/typecheck MUST be performed by real commands where feasible.

Model confidence MUST NOT substitute for deterministic checks.

---

## R-027 Change Scope

VibeCoding tasks MUST define allowed scope.

Agents SHOULD avoid unrelated refactors unless explicitly approved.

---

## R-028 Vertical Slices

Large development tasks MUST be decomposed into independently verifiable slices.

The implementation process SHOULD prefer working behavior over empty scaffolding.

---

## R-029 Review

Each implementation slice MUST receive a diff/behavior review before final acceptance.

Complex/security-sensitive reviews SHOULD use stronger reasoning than simple mechanical checks.

---

## R-030 Full-Chain Validation

User-facing feature labs SHOULD verify the complete behavior chain, not only unit-level changes.

Unverified stages MUST be reported.

---

## R-031 Enterprise Hardening

Extension labs SHOULD evaluate applicable production concerns without blindly applying every concern.

---

## R-032 Extension Labs

The system MUST generate project-specific extension exercises based on real extension points.

Labs MUST include impact analysis and tests.

---

## R-033 Interview

The system MUST generate project-specific progressive interview chains.

Questions MUST come from actual learned project knowledge.

---

## R-034 Failure and Debugging

The handbook MUST include realistic failure paths for core mechanisms.

It SHOULD teach where to inspect logs/state/tests and why.

---

## R-035 Quality Gates

The final handbook MUST pass the gates defined in `QUALITY_GATES.md` or be reported as failed/partial.

---

## R-036 Audit Artifacts

The system MUST output a machine-readable quality report.

It SHOULD also output a human-readable summary.

---

## R-037 Revision Pinning

Generated artifacts MUST record the repository revision they describe.

---

## R-038 Incremental Update

When project files change, the system SHOULD invalidate and regenerate only affected analysis/chapters where feasible.

---

## R-039 Disk Persistence

The system MUST persist key intermediate outputs so later study sessions do not depend on chat memory.

---

## R-040 Model Independence

Core architecture MUST not depend on a single provider/model.

Role-based generation and verification should be replaceable.

---

## R-041 Multi-Agent Optionality

The system MAY use subagents for parallel/narrow work.

It MUST continue to function in a single-agent environment.

---

## R-042 Small Model Safety

A small/fast model MAY perform bounded repetitive work such as inventory checks, test command operation and mechanical review.

It MUST NOT be the sole authority for difficult architecture/security/final integration decisions.

---

## R-043 No False Completion

The system MUST distinguish:

- PASS;
- PASS WITH WARNINGS;
- PARTIAL;
- FAIL;
- NOT RUN.

It MUST NOT report “complete” when required gates are not met.

---

## R-044 Unknowns

Unknown information MUST remain explicit.

The system MUST prefer `UNKNOWN`/`UNVERIFIED` over fabricated certainty during analysis.

Note: repository file classification `UNKNOWN` must be resolved before final coverage pass; semantic/project unknowns may remain documented.

---

## R-045 Documentation of Design Decisions

Significant internal architecture changes to Project DeepDive SHOULD use ADR-style records.

---

## R-046 Testability

New deterministic analyzers/validators MUST have automated tests.

Golden fixtures SHOULD cover at least two materially different ecosystems during dogfooding.

---

## R-047 Dogfooding

Before considering the upgrade complete, run it against:

- at least one Python/Agent-oriented repository;
- at least one Java/enterprise-style repository.

Dogfooding MUST inspect backend, frontend/auxiliary files where present, evidence and teaching output.

---

## R-048 No Empty Architecture Theater

The implementation MUST NOT create a large tree of unused placeholder modules merely to match a diagram.

Each new module should have working responsibility and tests.

---

## R-049 Security of Analysis

The system MUST treat repository commands as potentially unsafe.

Automatic execution SHOULD respect user/agent sandbox and permission settings.

Secrets MUST NOT be printed into generated books.

---

## R-050 Completion

The upgrade is complete only when the end-to-end acceptance criteria in `ACCEPTANCE_CRITERIA.md` pass.

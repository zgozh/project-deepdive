# Project DeepDive — Implementation Plan

## 1. Execution principle

Do not rewrite the entire repository in one pass.

Use:

```text
inspect
→ preserve
→ create a working vertical foundation
→ test
→ expand
→ dogfood
→ repair
```

Each phase must leave the repository in a coherent state.

---

# Phase 0 — Baseline & Existing-System Archaeology

## Goals

- read current README/SKILL/docs/scripts/templates;
- map current capabilities;
- run existing tests/checks;
- identify compatibility constraints.

## Outputs

- existing-system-map;
- capability matrix;
- baseline report;
- proposed migration map;
- list of current artifacts to preserve.

## Done when

- current behavior is understood;
- baseline failures are recorded;
- no major existing feature is unaccounted for.

## Model routing

Use small/fast subagents for file inventory or repetitive docs scan if available.  
Use the main strong model for compatibility decisions.

---

# Phase 1 — Core Schemas & Artifact Layout

## Goals

Introduce minimal stable schemas for:

- project index;
- stack profile;
- evidence;
- coverage;
- knowledge graph;
- curriculum;
- quality report.

## Rules

Do not build a giant schema up front. Start with fields required by Phase 2–4.

## Tests

- schema validation;
- serialization round-trip;
- version field behavior.

## Done when

A fixture repository can produce and validate placeholder-free real artifacts.

---

# Phase 2 — Repository Scanner & Coverage

## Goals

Implement deterministic repository-wide scan.

Detect:

- tracked files;
- file types;
- surfaces;
- likely generated/vendor;
- manifests/build/config/test/CI/tooling.

## Outputs

`project-index.json`, `coverage.json`

## Mandatory test fixtures

At least:

- Python-style fixture;
- Java-style fixture;
- frontend-containing fixture.

## Done when

- tracked file count matches Git/filesystem expectations;
- `UNKNOWN` can be resolved/classified;
- validation catches missing files.

---

# Phase 3 — Stack + Static Analysis

## Goals

Introduce adapter interfaces and initial analyzers.

Start with technologies present in the selected dogfood projects rather than implementing every language simultaneously.

## Priority

1. technologies already used by `replicate-learning` dogfood;
2. Python Agent project;
3. Java enterprise project;
4. frontend stack used by dogfood target.

## Outputs

- stack profile;
- symbol index;
- dependency graph;
- routes/endpoints;
- basic call graph;
- frontend map when relevant.

## Done when

Real symbols/routes from dogfood projects are correctly found and test-covered.

---

# Phase 4 — Evidence + Knowledge Graph

## Goals

Build canonical Evidence IDs and Claim-Evidence links.

Construct knowledge entities:

- business;
- modules;
- workflows;
- source symbols;
- mechanisms;
- concepts;
- tests;
- extension points.

## Key rule

Model-generated edges are provisional until verified or labeled E6.

## Tests

- nonexistent evidence reference fails;
- unsupported critical claim fails audit;
- inference remains labeled.

---

# Phase 5 — Beginner Prerequisite Engine + Curriculum

## Goals

Implement the global beginner assumption.

Produce:

- prerequisite concept graph;
- just-in-time prerequisite lessons;
- ordered curriculum;
- depth assignment.

## Beginner acceptance

A learner should be able to start a complex Agent/Spring/full-stack project knowing only basic programming concepts.

## Tests

Golden tests should detect:

- undefined critical terms;
- prerequisite appearing after dependent lesson;
- source explanation before business purpose.

---

# Phase 6 — Handbook Compiler

## Pipeline

```text
outline
→ chapter fact table
→ evidence set
→ prerequisite set
→ draft
→ verifier
→ beginner critic
→ repair
→ final
```

## Initial chapter families

- project map;
- prerequisites;
- business;
- architecture;
- core workflow;
- source;
- mechanism;
- engineering;
- full-stack;
- failures.

## Done when

One dogfood project can generate a coherent audited handbook rather than isolated notes.

---

# Phase 7 — VibeCoding Studio

Implement:

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

## Outputs

- prompt templates;
- playbooks;
- development-plan schema;
- review checklist;
- beginner explanation for every phase.

## Required demonstration

Generate one real extension lab for a dogfood project.

Do not require the Skill runtime itself to make unsafe automatic modifications; respect host permissions.

---

# Phase 8 — Extension & Interview Engines

## Extension

Use graph extension points to generate project-specific labs.

## Interview

Use graph + studied topics to generate progressive follow-ups.

## Done when

Questions/labs cite actual project components and survive verifier audit.

---

# Phase 9 — Quality Auditor

Implement all practical gates from `QUALITY_GATES.md`.

Prioritize deterministic gates first:

- coverage;
- evidence references;
- schema;
- cross-reference;
- revision consistency.

Then model-assisted gates:

- beginner teaching;
- mechanism depth;
- explanation quality;
- interview quality.

---

# Phase 10 — Runtime Analysis (Deep Mode)

This phase may be pulled earlier if existing project structure already supports it.

Implement safe runtime capture:

- documented start commands;
- test/smoke runs;
- logs;
- representative request;
- trace artifact.

Never claim runtime support for ecosystems not actually handled.

---

# Phase 11 — Dogfood & Repair

## Target A

Python Agent project, preferably non-trivial and full-stack if available.

## Target B

Java enterprise project.

## Procedure

1. clean scan;
2. compile handbook;
3. run audit;
4. manually inspect sampled claims;
5. test beginner readability;
6. generate VibeCoding extension lab;
7. execute at least one safe extension workflow in a branch/worktree when practical;
8. feed failures back into implementation.

---

# Phase 12 — Incremental Revision Support

Implement:

```text
revision A
→ diff to B
→ impacted files/symbols
→ impacted graph nodes
→ impacted chapters/claims
→ selective rebuild
```

---

# Multi-agent execution strategy for Codex

Use multi-agent capability when available, but do not make correctness depend on it.

## Good small-model tasks

- enumerate/classify files;
- compare index vs repository;
- run test commands and capture output;
- locate duplicate terminology;
- check links/references;
- mechanical fixture review;
- scan many low-complexity config/docs files.

## Good medium-model tasks

- inspect a module;
- summarize a bounded source area;
- triage ordinary test failures;
- implement a clearly specified isolated slice.

## Main/strong-model tasks

- architecture;
- migration design;
- conflicting evidence;
- cross-module integration;
- security;
- difficult debugging;
- final acceptance.

## Important testing rule

A small model may **operate** tests, but the test runner is the source of truth.

Use:

```text
Luna/fast worker
→ run command / collect output / categorize
→ main or stronger reviewer
→ decide root cause / architecture implications
```

for complex failures.

---

# Commit/change discipline

Prefer one coherent phase/slice per change.

Before leaving a phase:

- format/lint/typecheck as applicable;
- run relevant tests;
- inspect diff;
- update docs that describe implemented behavior;
- do not claim future modules already exist.

---

# Stop conditions

Pause broad implementation and reassess when:

- existing architecture differs materially from the spec;
- a planned replacement would destroy valuable original behavior;
- tests reveal hidden compatibility requirements;
- dogfood shows an abstraction is language-specific;
- implementation is accumulating empty scaffolding.

The correct response is to revise the plan, not force the original diagram.

# AGENTS.md — Project DeepDive Development Instructions

## Mission

Upgrade this repository from `replicate-learning` into **Project DeepDive** while preserving its proven teaching strengths.

Project DeepDive is a cross-language system for:

- repository archaeology;
- business/architecture understanding;
- source/call-chain learning;
- framework and lower-level mechanism teaching;
- evidence-backed handbook compilation;
- beginner-first learning;
- VibeCoding/agentic development training;
- project extension;
- project-aware interview practice;
- quality auditing.

---

## Global invariant: default learner is a true beginner

Unless a user explicitly states otherwise, assume the learner:

- has only basic programming awareness;
- knows little or nothing about the project;
- may not know the framework/tooling/frontend/backend/database stack;
- cannot be expected to infer prerequisites.

Any learner-facing content must explain required concepts before relying on them.

Do not simplify away depth. Use:

```text
intuition
→ prerequisite
→ project context
→ architecture
→ source
→ mechanism
→ failure
→ extension
```

---

## Read before major implementation

Treat these files as the current implementation contract:

1. `PROJECT_DEEPDIVE_SPEC.md`
2. `ARCHITECTURE.md`
3. `REQUIREMENTS.md`
4. `VIBECODING_SPEC.md`
5. `QUALITY_GATES.md`
6. `ACCEPTANCE_CRITERIA.md`
7. `IMPLEMENTATION_PLAN.md`

If they conflict with the real existing repository, do not blindly overwrite the repository. Document the conflict and choose the smallest compatible evolution.

---

## First action in a fresh implementation session

Before broad edits:

1. inspect repository structure;
2. read existing `SKILL.md`, README and docs;
3. identify existing scripts/templates;
4. run current tests/checks where possible;
5. produce/update an implementation plan grounded in actual files.

Do not create a speculative architecture tree first.

---

## Preservation rule

Prefer:

```text
reuse
→ refactor
→ extend
```

over replacement.

Do not remove an existing capability merely because a new abstraction is cleaner.

---

## Deterministic-first rule

Use code/tools for:

- file inventory;
- AST/symbol extraction;
- graphs;
- coverage;
- schema validation;
- test execution;
- lint/typecheck;
- cross-reference validation;
- revision/diff analysis.

Use LLM reasoning for interpretation and teaching.

Do not ask a model to guess what a script can prove.

---

## Evidence rule

Project-specific facts require evidence.

Use the evidence levels defined in the spec.

Never present model inference as direct source/runtime fact.

Unknown is acceptable.

Fabricated file/symbol/call chain is not.

---

## Scope rule

For each implementation task:

- define the goal;
- define allowed scope;
- avoid unrelated refactors;
- keep the repository runnable;
- prefer a working vertical slice over empty scaffolding.

---

## Verification rule

Before declaring a slice complete, run applicable real checks:

- build;
- formatter check;
- lint;
- typecheck;
- unit tests;
- integration tests;
- e2e/smoke tests.

Do not weaken/delete tests simply to make a run green.

Distinguish pre-existing failures from new failures.

---

## Review rule

After implementation:

1. inspect the actual diff;
2. check correctness;
3. check regressions;
4. check edge/error paths;
5. check architecture consistency;
6. check security/concurrency when relevant;
7. rerun impacted checks after repairs.

---

## Multi-agent / subagent policy

Use subagents when available and beneficial.

### Fast/small workers

Suitable for bounded, explicit, repetitive work:

- repository enumeration;
- classification checks;
- running deterministic test commands;
- collecting logs/results;
- mechanical cross-reference checks;
- low-risk documentation scans.

### Medium workers

Suitable for:

- bounded module exploration;
- ordinary code review;
- clear isolated implementation slices;
- straightforward failure triage.

### Strong main/reviewer

Retain or escalate for:

- architecture;
- ambiguous requirements;
- cross-module integration;
- difficult root-cause analysis;
- security-sensitive work;
- final quality acceptance.

A small model may run tests; the test runner is authoritative.

Do not claim a particular worker/model was used unless runtime configuration confirms it.

If model-specific delegation is unavailable, continue with available agents rather than blocking.

---

## Beginner quality review

Any generated learner-facing fixture/template must be checked for:

- undefined jargon;
- missing prerequisite;
- unexplained commands;
- unexplained framework magic;
- abstraction jumps;
- source details without business context.

---

## VibeCoding implementation philosophy

The product must teach:

```text
BASELINE
→ DISCOVER
→ SPEC
→ PLAN
→ SLICE
→ BUILD
→ VERIFY
→ REVIEW
→ REPAIR
→ FULL-CHAIN
→ RETRO
```

Do not reduce VibeCoding to a prompt library.

---

## Frontend rule

If a target project has frontend code, treat it as first-class.

Do not generate backend-deep/frontend-shallow learning materials.

Connect feature flow across UI, state, API client, backend, persistence and UI update.

---

## Compatibility rule

Schemas and generated artifacts should be versioned.

If an existing artifact format is changed:

- provide migration/compatibility where reasonable;
- update tests;
- update documentation;
- explain the breaking change.

---

## Security rule

Repository commands can be unsafe.

Respect host sandbox/approval settings.

Do not expose secrets in generated artifacts or logs.

---

## Completion rule

A task is not complete because code was written.

It is complete only when:

- implementation exists;
- relevant tests/checks run;
- diff reviewed;
- documentation matches reality;
- required acceptance conditions/gates pass or are explicitly reported as pending.

Use `QUALITY_GATES.md` and `ACCEPTANCE_CRITERIA.md` as the final authority.

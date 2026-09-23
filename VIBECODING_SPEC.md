# Project DeepDive — VibeCoding / Agentic Development Specification

## 1. Definition

Within Project DeepDive, **VibeCoding** means disciplined AI-assisted software engineering.

It does **not** mean:

```text
one vague prompt
→ large code generation
→ “looks good”
→ merge
```

It means the learner can supervise a coding agent through a controlled engineering loop.

---

## 2. Default learner

Assume the learner has almost no practical experience with agentic development.

Therefore every lab must teach:

- what the current development phase is;
- why that phase exists;
- what the learner decides;
- what the agent decides;
- what evidence is required;
- how to review the agent;
- what a bad result looks like;
- how to recover.

Never say “review the diff” without first teaching what a diff is and what to inspect.

Never say “run integration tests” without explaining how they differ from unit/e2e tests in this project.

---

## 3. Canonical workflow

```text
DISCOVER
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

Each phase has an explicit gate.

---

## 4. Phase 0 — BASELINE

Before a modification:

- locate repository root;
- identify current revision;
- identify documented build/start/test commands;
- install/sync dependencies when permitted;
- run the smallest representative baseline checks;
- record pre-existing failures;
- establish what “working before our change” means.

Output:

```text
baseline-report.md/json
```

A learner must understand that otherwise a pre-existing failure may be falsely blamed on the new change.

---

## 5. DISCOVER

### Goal

Understand the current system before proposing code.

### Agent tasks

- search relevant files;
- identify entry point;
- identify current workflow;
- find analogous features;
- find tests;
- find configs;
- identify frontend/backend/database/infrastructure surfaces;
- list unknowns.

### Learner tasks

- check whether the agent found the correct workflow;
- reject unsupported assumptions;
- ask why proposed files are relevant.

### Prompt pattern

```text
GOAL
Understand how [feature] currently works. Do not modify code.

CONTEXT
[requirement/background]

SCOPE
Search the repository broadly enough to include frontend, backend,
tests, config and infrastructure that participate in this feature.

EVIDENCE
Every factual statement about current behavior must reference real
files/symbols/config/tests/runtime evidence.

OUTPUT
- current end-to-end workflow
- key files and symbols
- analogous implementation
- extension points
- constraints
- unknowns
```

### Gate

No implementation until current behavior is sufficiently evidenced.

---

## 6. SPEC

### Goal

Turn an ambiguous request into an implementable contract.

The specification should contain:

- user/business problem;
- actors;
- in-scope behavior;
- out-of-scope behavior;
- API/UI changes;
- data/state changes;
- errors;
- edge cases;
- compatibility constraints;
- security implications;
- acceptance criteria.

### Beginner teaching

Explain the difference between:

- requirement;
- implementation idea;
- acceptance criterion.

The agent must not smuggle an architectural choice into a requirement without labeling it as a proposal.

---

## 7. PLAN

### Goal

Produce a repository-grounded change plan before coding.

For each planned slice:

- purpose;
- files/symbols;
- new files;
- changed interfaces;
- data changes;
- call-chain changes;
- tests;
- runtime verification;
- risks;
- rollback/compatibility concerns;
- done conditions.

A plan must prefer existing patterns over unnecessary new abstraction.

### Plan challenge questions

The learner should ask:

- Why is a new abstraction needed?
- Can an existing extension point be reused?
- Is this the smallest safe change?
- Which stable interface changes?
- What breaks if this assumption is wrong?
- Which test proves the behavior?

---

## 8. SLICE

Large work is decomposed into independently verifiable vertical slices.

Good slice:

```text
frontend control
→ API contract
→ backend behavior
→ persistence/state
→ test
```

when the feature is inherently end-to-end.

Good infrastructure slice:

```text
schema/config
→ service integration
→ health/smoke test
```

Bad slice:

```text
“create 30 empty classes”
```

Slices should minimize unverified code.

---

## 9. BUILD

Recommended task prompt:

```text
GOAL
Implement only Slice N: [goal].

CONTEXT
[verified current workflow and approved plan]

CONSTRAINTS
- no unrelated refactor
- preserve current style and architecture
- reuse existing abstractions where reasonable
- no unnecessary dependency
- do not weaken tests
- if the plan conflicts with real code, report the conflict
- keep scope within the approved slice

SCOPE
Allowed files/directories: [...]
Protected files/interfaces: [...]

EVIDENCE
Treat repository source/config/tests as source of truth.

VERIFICATION
Run: [...]

DONE WHEN
[objective acceptance criteria]

OUTPUT CONTRACT
Summarize changes, tests, results and remaining risks.
```

---

## 10. VERIFY

Verification must use real tools.

Possible commands:

- compiler/build;
- lint;
- format check;
- typecheck;
- unit tests;
- integration tests;
- e2e tests;
- smoke test;
- migration validation;
- container health check.

Rules:

- “the code looks correct” is not verification;
- “should pass” is not verification;
- model-generated reasoning cannot replace a failing test;
- commands and exit status must be recorded when practical;
- pre-existing failures must be distinguished from new failures.

---

## 11. REVIEW

Review the actual diff and behavior.

Checklist:

### Correctness

- requirement satisfied?
- edge cases?
- error path?
- stale state?
- race conditions?

### Architecture

- follows existing pattern?
- unnecessary abstraction?
- inappropriate coupling?
- breaks module boundary?

### API/data

- backward compatible?
- validation?
- migration?
- serialization?
- versioning?

### Security

- input trust?
- permissions?
- secrets?
- injection?
- unsafe tool access?

### Operations

- timeout?
- retry?
- idempotency?
- logs?
- metrics/traces?
- cleanup?

### Frontend

- loading/error/empty state?
- state synchronization?
- accessibility where applicable?
- API contract mismatch?

### Tests

- behavior or implementation detail?
- regression covered?
- failure path covered?
- false-positive tests?

---

## 12. REPAIR

Repair must be scoped to confirmed findings.

Avoid “while here, I rewrote the subsystem”.

After repair:

- rerun relevant failing tests;
- rerun affected quality checks;
- re-review changed diff.

---

## 13. FULL-CHAIN

For end-user features, validate the real path.

Example:

```text
UI action
→ frontend state
→ API request
→ route
→ service
→ agent/domain
→ DB/cache/tool
→ response/stream
→ frontend state
→ rendered result
```

Document any untested stage.

---

## 14. RETRO

The most valuable VibeCoding step.

Record:

- what the agent misunderstood;
- what repository context was missing;
- which prompt constraint prevented failure;
- which test caught a bug;
- which new invariant belongs in `AGENTS.md`;
- which repeated workflow should become a Skill;
- which regression deserves a permanent test;
- which architecture knowledge should enter the handbook.

The system should teach that good agentic development improves the environment for the next run.

---

## 15. Prompt Contract

Core prompts SHOULD use:

```text
GOAL
CONTEXT
CONSTRAINTS
SCOPE
EVIDENCE
VERIFICATION
DONE WHEN
OUTPUT CONTRACT
```

### Why each field exists

**GOAL**  
Prevents the agent from optimizing an adjacent problem.

**CONTEXT**  
Provides business and repository facts the task depends on.

**CONSTRAINTS**  
Defines invariants and forbidden shortcuts.

**SCOPE**  
Controls blast radius.

**EVIDENCE**  
Prevents unsupported claims about current behavior.

**VERIFICATION**  
Forces objective checks.

**DONE WHEN**  
Defines the finish line.

**OUTPUT CONTRACT**  
Makes the handoff reviewable.

---

## 16. Bad prompt teaching

Bad:

```text
Add a security agent and make everything production ready.
```

Why bad:

- unclear behavior;
- unknown architecture;
- no scope;
- no acceptance criteria;
- no evidence requirement;
- “production ready” is undefined;
- encourages broad speculative change.

Improved process:

1. Discover existing Agent/Tool/Permission architecture.
2. Define SecurityAgent user stories and permissions.
3. Plan minimal extension points.
4. Implement one vertical slice.
5. Verify.
6. Harden only relevant dimensions.

---

## 17. Model/subagent routing policy

Project DeepDive should be model-provider neutral, but the coding-agent workflow MAY use multiple model tiers.

General policy:

### Small/fast worker

Good for:

- file enumeration;
- repetitive classification;
- checksum/index checking;
- narrow documentation review;
- running deterministic test commands;
- collecting failures;
- mechanical formatting;
- simple consistency checks.

### Medium worker

Good for:

- repository exploration;
- larger file review;
- ordinary isolated implementation;
- test-failure triage;
- straightforward refactors with clear constraints.

### Strong orchestrator/reviewer

Use for:

- ambiguous requirements;
- architecture;
- cross-module integration;
- security-sensitive reasoning;
- difficult bugs;
- conflicting evidence;
- final acceptance judgment.

Important:

- test execution is deterministic; model size does not make a failing test pass;
- a small model may operate the commands and summarize output;
- final root-cause and integration decisions should escalate when complexity warrants;
- never claim a specific subagent model ran unless the runtime actually confirms it.

---

## 18. Enterprise hardening

After functional completion, evaluate only relevant concerns:

```text
auth
authorization
multi-tenancy
validation
secrets
timeouts
retries
idempotency
concurrency
transactions
cache consistency
message semantics
observability
resource cleanup
performance
cost
migrations
deployability
rollback
compatibility
security
```

Teach the learner how to decide relevance.

---

## 19. VibeCoding labs

Each project should generate project-specific labs such as:

- add an Agent;
- add a Tool;
- replace an LLM provider;
- add a frontend workflow;
- add streaming;
- add permission isolation;
- add audit trail;
- add cache;
- add queue/job;
- add tenant isolation;
- add observability;
- add CI check;
- refactor a bottleneck;
- perform a migration.

Do not generate a lab that the current project architecture cannot meaningfully support.

---

## 20. Lab output format

Every lab:

```text
01 Problem
02 Beginner prerequisites
03 Current project behavior
04 Requirement specification
05 Repository reconnaissance prompt
06 Findings
07 Architecture impact
08 Plan prompt
09 Approved slices
10 Build prompt per slice
11 Verification commands
12 Diff review
13 Failure/debug branch
14 Full-chain test
15 Enterprise hardening
16 Retrospective
17 Interview follow-up
```

---

## 21. Anti-patterns

The VibeCoding system MUST teach against:

- giant one-shot prompts;
- changing code before reading it;
- “fix tests” by deleting assertions;
- broad refactor inside feature work;
- accepting generated code without running it;
- trusting model claims about files it did not inspect;
- using a small model as sole judge of complex correctness;
- merging because diff is large and tiring to review;
- generating architecture not found in the project;
- treating every tool result as success without exit status/behavior evidence.

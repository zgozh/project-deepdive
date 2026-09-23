# Project DeepDive — Product & System Specification

> Status: implementation specification  
> Target: upgrade `replicate-learning` into a cross-language project-learning, source-analysis, handbook-compilation, VibeCoding, extension and interview system.  
> Primary implementer: Codex or another repository-aware coding agent.  
> Compatibility principle: **evolve the existing project; do not discard its proven teaching kernel.**

---

## 1. Product definition

**Project DeepDive** is a repository-to-knowledge compiler and learning system.

Given a local or remote software project, it should:

1. inspect the complete repository;
2. understand business intent, architecture, runtime paths and source relationships;
3. build a structured project knowledge graph;
4. identify what a learner must know before reading each part;
5. compile a high-quality, evidence-backed project handbook;
6. guide a learner through the shortest useful learning path;
7. teach source code, framework mechanisms, low-level principles and design trade-offs;
8. teach how to extend the project using modern VibeCoding / Agentic Coding workflows;
9. provide project-aware interview drilling;
10. audit coverage, evidence, teaching quality and hallucination risk.

The system is not merely a longer `SKILL.md`. `SKILL.md` is the orchestration entry point. Deterministic analysis should be implemented in scripts/tools; domain differences should be handled through adapters; teaching and generation should be driven by structured intermediate artifacts.

---

## 2. Global learner definition — NON-NEGOTIABLE

### 2.1 Default learner

Unless the user explicitly says otherwise, **assume the learner is a true beginner**:

- has only vague concepts;
- does not know the project;
- does not know its architecture;
- may not know the language/framework beyond basic syntax;
- may not know frontend, backend, database, deployment, testing or infrastructure;
- may have heard terms such as RAG, Agent, Redis, Docker, React or Spring, but cannot be assumed to understand them;
- should never be expected to infer missing prerequisite knowledge.

This is a **global invariant**, not a chapter-level preference.

### 2.2 Beginner Contract

Before relying on a concept, the teaching system MUST answer, at an appropriate depth:

1. **What is it?**
2. **Why does software need it?**
3. **What problem does it solve here?**
4. **Where is it in this repository?**
5. **What is the smallest understandable example?**
6. **What happens if it is removed or misunderstood?**
7. **What should the learner know before continuing?**

The system MUST NOT casually use unexplained jargon.

Examples of terms that require first-use explanation when relevant:

- route / controller / service / repository;
- dependency injection;
- coroutine / async / event loop;
- state machine;
- middleware;
- WebSocket / SSE;
- transaction;
- cache / distributed lock;
- vector database / embedding;
- tool calling / agent loop / memory;
- component / hook / store;
- Docker image / container / volume;
- CI/CD;
- typecheck / lint / unit test / integration test / e2e.

### 2.3 Adaptive depth

Beginner-first does **not** mean shallow.

The sequence is:

```text
Intuition
→ minimal prerequisite
→ project use case
→ architecture position
→ source code
→ runtime chain
→ framework mechanism
→ lower-level equivalent
→ design trade-off
→ failure mode
→ extension
```

The goal is to take a beginner to deep project competence without silently skipping steps.

### 2.4 No “tutorial cliff”

A chapter fails the Beginner Contract if it:

- explains code before explaining why the code exists;
- names a framework feature without explaining its purpose;
- uses a command without explaining what it does;
- jumps from architecture diagram to complex implementation;
- assumes frontend/backend/database knowledge without prerequisites;
- lists files without showing how they participate in a real workflow;
- gives an answer that can only be understood by someone who already knows the answer.

---

## 3. Core philosophy retained from replicate-learning

Project DeepDive MUST retain and strengthen these existing ideas:

- whole-system understanding before local details;
- business before architecture before code;
- complete repository/file coverage;
- business workflow-oriented learning;
- real call chains;
- class/method usage context;
- forward and backward upstream/downstream tracing;
- source evidence;
- framework mechanism penetration;
- no-framework/minimal-equivalent implementations where useful;
- lower-level principles;
- failure analysis;
- runtime verification;
- disk-persisted learning artifacts;
- modification/extension learning;
- verification and audit.

The new system is an expansion, not a replacement.

---

## 4. Product modes

The Skill SHOULD expose natural-language triggering and MAY expose command-style entry points.

Recommended logical modes:

```text
learn-project
project-scan
project-map
project-book
project-study
project-interview
project-extend
project-vibecode
project-audit
project-update
```

### 4.1 `learn-project`

Full pipeline:

```text
Repository
→ Archaeology
→ Stack Detection
→ Static Analysis
→ Runtime Analysis (when possible)
→ Knowledge Graph
→ Curriculum
→ Handbook
→ Audit
```

### 4.2 `project-study`

Teach from persisted artifacts. Avoid re-archaeology unless evidence is missing or repository revision changed.

### 4.3 `project-interview`

Generate progressive project-aware questioning from the knowledge graph and the learner’s studied material.

### 4.4 `project-extend`

Teach modification design: impact analysis, files, interfaces, state, tests, failure cases and trade-offs.

### 4.5 `project-vibecode`

Run a real development teaching workflow:

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

---

## 5. Supported project families

Architecture MUST be language-agnostic.

Initial adapter targets:

- Python;
- Java;
- JavaScript / TypeScript;
- Go;
- C / C++;
- Rust.

Framework adapters SHOULD be composable and optional.

Examples:

- FastAPI / Django;
- Spring / Spring Boot;
- React / Next.js / Vue;
- LangGraph and Agent frameworks;
- common ORMs;
- Redis;
- relational databases;
- messaging systems;
- container/deployment tooling.

Unknown frameworks MUST degrade gracefully to generic static/runtime analysis rather than pretending support.

---

## 6. Full repository surface coverage

Project DeepDive MUST inventory the complete project, not only `src/`.

Repository surfaces include:

- backend;
- frontend;
- domain/core;
- API;
- agent/RAG/tooling;
- database schemas;
- migrations;
- seed data;
- config;
- `.env` examples;
- Dockerfiles;
- Compose;
- Kubernetes;
- Nginx/reverse proxy;
- CI/CD;
- tests;
- fixtures;
- scripts;
- CLI;
- MCP/tool servers;
- build systems;
- package managers;
- lint/format/typecheck config;
- static assets;
- docs;
- lockfiles;
- generated files;
- vendored/third-party code.

Every tracked file MUST receive one classification:

```text
COVERED
CLASSIFIED
GENERATED
VENDOR
IGNORED_WITH_REASON
```

`UNKNOWN` MUST be zero at final audit.

“Full coverage” does not mean equal teaching depth. It means every relevant artifact is known, classified, connected and intentionally handled.

---

## 7. Depth model

Recommended content depth:

```text
D0 Record only
D1 Know it exists
D2 Understand role and relationships
D3 Understand workflow participation
D4 Read important implementation
D5 Framework/mechanism penetration
D6 Lower-level equivalent / principles
D7 Modification and design capability
D8 Interview / adversarial reasoning
```

The compiler assigns depth according to:

- business criticality;
- call-chain centrality;
- architectural importance;
- novelty;
- failure impact;
- extension relevance;
- learner prerequisite needs.

---

## 8. Knowledge model

The handbook MUST be generated from structured knowledge, not directly from raw files.

Minimum entities:

- Project;
- RepositorySurface;
- Module;
- File;
- Symbol;
- BusinessCapability;
- BusinessWorkflow;
- RuntimeWorkflow;
- Feature;
- API;
- DataModel;
- Dependency;
- Framework;
- Mechanism;
- Concept;
- Prerequisite;
- Evidence;
- Claim;
- Test;
- ExtensionPoint;
- FailureMode;
- InterviewTopic.

Minimum relationships:

```text
CALLS
CALLED_BY
DEPENDS_ON
IMPLEMENTS
EXTENDS
USES
ROUTES_TO
READS
WRITES
PERSISTS_TO
PUBLISHES
CONSUMES
RENDERS
TRIGGERS
PART_OF
REQUIRES_CONCEPT
EVIDENCED_BY
TESTED_BY
EXTENSION_OF
FAILS_WHEN
```

---

## 9. Evidence model

Project facts must be traceable.

Evidence levels:

```text
E0 user-provided information
E1 direct source-code evidence
E2 config/dependency/test evidence
E3 runtime trace / observed execution
E4 official external documentation
E5 project history / commit / issue
E6 model inference
```

Rules:

- E6 MUST be explicitly marked as inference.
- E6 MUST NOT be rendered as a verified project fact.
- Critical architecture/call-chain claims SHOULD prefer E1–E3.
- General knowledge must be labeled separately from project-specific fact.
- Unsupported speculation does not enter the final handbook.

Maintain a Claim-Evidence Matrix.

---

## 10. Handbook structure

Recommended generated structure:

```text
PROJECT-LEARNING-BOOK/
├── 00-README.md
├── 01-project-map/
├── 02-prerequisites/
├── 03-business/
├── 04-architecture/
├── 05-core-workflows/
├── 06-source-guide/
├── 07-mechanisms/
├── 08-framework-internals/
├── 09-fullstack/
├── 10-engineering/
├── 11-failures-debugging/
├── 12-vibecoding/
├── 13-extension-labs/
├── 14-interview/
├── 15-review/
└── 99-evidence/
```

The prerequisite section is mandatory under the Beginner Contract.

---

## 11. Source-code teaching contract

For each core class/function/method, explain as applicable:

1. beginner prerequisite;
2. business scenario;
3. architecture position;
4. why it exists;
5. callers;
6. callees;
7. forward trace;
8. backward trace;
9. input/output/state;
10. important implementation, line-by-line only where useful;
11. framework behavior hidden behind APIs;
12. minimal no-framework equivalent;
13. design trade-offs;
14. failure behavior;
15. tests;
16. extension points;
17. interview questions.

Do not reduce explanation to “method X calls Y to do Z.”

---

## 12. Full-stack feature contract

A core feature should be traceable end to end when applicable:

```text
User interaction
→ Page/Component
→ Hook/Store
→ API client
→ HTTP/WebSocket/SSE
→ Backend route
→ Service/use-case
→ Domain/core
→ Agent/RAG/tool
→ Persistence/infrastructure
→ Response/stream
→ Frontend state
→ Rendered UI
→ Tests
```

If a stage does not exist, state that explicitly.

---

## 13. VibeCoding learning contract

VibeCoding is taught as engineering, not “prompt and hope”.

Every development lab must teach:

- requirement clarification;
- repository reconnaissance;
- plan before code;
- change-scope control;
- vertical slicing;
- acceptance criteria;
- deterministic validation;
- diff review;
- debugging;
- full-chain verification;
- enterprise hardening;
- retrospective;
- rule/skill/test extraction from lessons learned.

Prompt design uses:

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

The learner must understand why each section exists.

---

## 14. Enterprise engineering dimensions

Extension/VibeCoding labs should consider, when relevant:

- authentication;
- authorization/RBAC;
- multi-tenancy;
- secrets;
- validation;
- retries;
- timeouts;
- idempotency;
- concurrency;
- transactions;
- cache correctness;
- message delivery semantics;
- observability;
- logs;
- traces;
- metrics;
- error handling;
- compatibility;
- migrations;
- deployment;
- rollback;
- security;
- performance;
- cost;
- tests;
- CI/CD.

Do not force every concern into every feature; teach how to decide relevance.

---

## 15. Interview model

Questions must be project-aware and evidence-backed.

Depth progression:

```text
L1 project purpose
L2 business workflow
L3 architecture
L4 source code
L5 framework mechanism
L6 lower-level principle
L7 failure/debug
L8 concurrency/performance
L9 scaling/design trade-off
L10 extension
L11 challenge/counterargument
L12 redesign
```

The interviewer should ask follow-ups based on previous answers, not random question banks.

---

## 16. Learning path optimization

The system should derive prerequisite edges and rank learning units to maximize:

```text
understanding gained / learner effort
```

Rules:

- explain prerequisites just in time;
- do not front-load an entire language textbook;
- do not skip prerequisites needed for the next core workflow;
- compress previously mastered concepts when reliable learning history exists;
- default to beginner depth when history is absent.

---

## 17. Persistence and versioning

Persist:

- repository revision;
- file index;
- stack profile;
- knowledge graph;
- call graph;
- workflow graph;
- evidence;
- coverage;
- curriculum;
- handbook;
- audit report;
- learner progress;
- extension lab state.

A handbook release should be tied to a repository revision.

On repository change:

```text
git diff / file diff
→ affected entities
→ affected claims
→ affected chapters
→ selective regeneration
→ re-audit
```

---

## 18. Hallucination control

Required controls:

1. deterministic repository inventory;
2. structured intermediate representations;
3. evidence-tagged claims;
4. separate Writer and Verifier roles;
5. verifier must search evidence, not agree semantically;
6. unsupported project facts fail;
7. general knowledge is labeled;
8. inference is labeled;
9. coverage and consistency are machine-audited;
10. generated handbook is not declared complete until quality gates pass.

---

## 19. Non-goals

The system is not:

- a generic documentation generator;
- a file-by-file dump;
- an API encyclopedia;
- a static “100 interview questions” generator;
- a prompt collection;
- a replacement for actual builds/tests/runtime verification;
- an excuse to explain every generated/vendor file in equal depth;
- a system that assumes expert prior knowledge.

---

## 20. Success definition

Project DeepDive succeeds when a beginner can progress from:

```text
“I only know roughly what this project does”
```

to:

```text
“I can explain the business,
trace an end-to-end request,
read the important source,
explain the hidden mechanisms,
debug failures,
use a coding agent responsibly,
implement and verify an extension,
and defend the design in an interview.”
```

without the system silently skipping prerequisite knowledge or inventing project facts.

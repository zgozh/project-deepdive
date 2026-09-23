# Project DeepDive — Architecture

## 1. Architectural goal

Build a **compiler-like pipeline**:

```text
Repository
   │
   ▼
Intake / Inventory
   │
   ├──────────────┐
   ▼              ▼
Static Analysis   Runtime Analysis
   │              │
   └──────┬───────┘
          ▼
Evidence Store
          │
          ▼
Knowledge Graph
          │
   ┌──────┼─────────┐
   ▼      ▼         ▼
Business Architecture Mechanisms
   └──────┼─────────┘
          ▼
Prerequisite Graph
          │
          ▼
Curriculum Compiler
          │
          ▼
Handbook Compiler
          │
   ┌──────┼───────────┬─────────────┐
   ▼      ▼           ▼             ▼
Study  Interview   Extension    VibeCoding
   └──────┴───────────┴─────────────┘
          │
          ▼
Quality Auditor
```

A language/framework adapter enriches this pipeline; it must not replace the universal core.

---

## 2. Proposed repository structure

```text
project-deepdive/
├── SKILL.md
├── AGENTS.md
├── README.md
│
├── core/
│   ├── intake/
│   ├── archaeology/
│   ├── business/
│   ├── architecture/
│   ├── runtime/
│   ├── knowledge_graph/
│   ├── prerequisite_graph/
│   ├── curriculum/
│   ├── handbook/
│   ├── evidence/
│   ├── coverage/
│   └── audit/
│
├── analyzers/
│   ├── repository/
│   ├── source/
│   ├── symbols/
│   ├── dependencies/
│   ├── callgraph/
│   ├── routes/
│   ├── data/
│   ├── frontend/
│   ├── tests/
│   └── runtime/
│
├── adapters/
│   ├── languages/
│   │   ├── python/
│   │   ├── java/
│   │   ├── typescript/
│   │   ├── javascript/
│   │   ├── go/
│   │   ├── cpp/
│   │   └── rust/
│   ├── frameworks/
│   ├── databases/
│   ├── frontend/
│   └── infrastructure/
│
├── teaching/
│   ├── beginner/
│   ├── source/
│   ├── mechanism/
│   ├── fullstack/
│   ├── interview/
│   └── extension/
│
├── vibecoding/
│   ├── workflows/
│   ├── prompts/
│   ├── playbooks/
│   ├── reviewers/
│   └── checklists/
│
├── prompts/
│   ├── archaeology/
│   ├── business/
│   ├── architecture/
│   ├── runtime/
│   ├── curriculum/
│   ├── handbook/
│   └── audit/
│
├── schemas/
│   ├── project-index.schema.json
│   ├── stack-profile.schema.json
│   ├── knowledge-graph.schema.json
│   ├── evidence.schema.json
│   ├── claim.schema.json
│   ├── coverage.schema.json
│   ├── curriculum.schema.json
│   ├── development-plan.schema.json
│   └── quality-report.schema.json
│
├── scripts/
│   ├── scan_repository.*
│   ├── classify_files.*
│   ├── detect_stack.*
│   ├── extract_symbols.*
│   ├── build_dependency_graph.*
│   ├── build_call_graph.*
│   ├── validate_coverage.*
│   ├── validate_evidence.*
│   └── validate_handbook.*
│
├── templates/
│   ├── project-book/
│   ├── concept/
│   ├── workflow/
│   ├── source/
│   ├── mechanism/
│   ├── extension/
│   ├── vibecoding/
│   └── audit/
│
├── references/
│   ├── depth-policy.md
│   ├── evidence-policy.md
│   ├── coverage-policy.md
│   ├── beginner-policy.md
│   ├── teaching-policy.md
│   ├── vibecoding-policy.md
│   └── quality-policy.md
│
└── tests/
    ├── unit/
    ├── fixtures/
    ├── integration/
    └── golden/
```

Do not create empty directories merely to match this diagram. Introduce them incrementally when they serve working behavior.

---

## 3. Core components

### 3.1 Repository Intake

Responsibilities:

- identify repository root;
- capture current revision;
- enumerate tracked files;
- detect monorepo/workspace boundaries;
- detect package/build systems;
- record file sizes/types;
- apply safe default ignores;
- distinguish generated/vendor files from owned code.

Output: `project-index.json`.

### 3.2 Stack Detector

Combines deterministic evidence:

- manifests;
- imports;
- config;
- build files;
- lockfiles;
- Docker;
- common directory conventions.

Output: `stack-profile.json`.

Confidence must be represented. Unknown is valid.

### 3.3 Static Analyzer

Produces:

- symbol inventory;
- imports/dependencies;
- inheritance/interface relations;
- routes/endpoints;
- data models;
- likely entry points;
- test-to-source relationships;
- frontend page/component/API relations;
- call graph where technically feasible.

Prefer AST/tree-sitter/LSP or language-native tooling. Regex is a fallback, not the primary parser for languages with available parsers.

### 3.4 Runtime Analyzer

Optional but important in Deep Mode.

Responsibilities:

- discover documented start/test commands;
- start isolated services when safe and permitted;
- capture logs/traces;
- exercise representative requests;
- record observed call/runtime paths;
- compare runtime path with static expectations.

Runtime findings are evidence E3.

Never fabricate runtime verification when the project cannot be started.

### 3.5 Evidence Store

Canonical store for evidence references.

Each evidence item includes:

- id;
- type/level;
- repository revision;
- file/path;
- symbol/range if known;
- command/runtime observation if applicable;
- confidence;
- notes.

### 3.6 Knowledge Graph Builder

Merges deterministic extraction and model reasoning.

LLM responsibilities:

- map symbols to business concepts;
- infer architectural roles;
- propose workflows;
- identify mechanisms;
- identify prerequisite concepts;
- propose importance/depth.

Verifier responsibilities:

- reject unsupported factual edges;
- downgrade inference to E6;
- locate missing evidence;
- preserve unknowns.

### 3.7 Prerequisite Graph

Critical for the default beginner.

Nodes:

- concepts;
- commands;
- language features;
- framework mechanisms;
- infrastructure concepts.

Edges:

```text
A REQUIRES B
```

Example:

```text
FastAPI dependency injection
    requires
Python functions
    requires
parameters / return values
```

The graph is used to generate just-in-time prerequisite lessons.

### 3.8 Curriculum Compiler

Inputs:

- knowledge graph;
- prerequisite graph;
- depth policy;
- importance;
- learner profile/history;
- optional time budget.

Produces an ordered learning path.

Ordering objective:

1. minimum required prerequisites;
2. project purpose;
3. one understandable end-to-end workflow;
4. core architecture;
5. core mechanisms;
6. source deep dives;
7. failure/debug;
8. extension;
9. interview.

### 3.9 Handbook Compiler

Generation pipeline:

```text
Chapter Plan
→ Required Facts
→ Required Evidence
→ Prerequisites
→ Draft
→ Evidence Verification
→ Beginner Review
→ Consistency Review
→ Final Chapter
```

No direct `raw repository → long prose` shortcut.

### 3.10 Study Engine

Reads persisted handbook/graph and:

- teaches progressively;
- checks understanding;
- fills prerequisite gaps;
- creates exercises;
- links concepts back to project source;
- records progress.

### 3.11 Interview Engine

Uses the same graph and evidence.

It should generate connected follow-up chains, not independent trivia.

### 3.12 Extension Engine

Creates project-specific modifications from real extension points.

Produces:

- requirement;
- impact graph;
- files/symbols;
- architecture decisions;
- changed call chain;
- tests;
- failure cases;
- hardening checklist.

### 3.13 VibeCoding Engine

Runs the workflow defined in `VIBECODING_SPEC.md`.

It teaches the learner how to supervise a coding agent rather than merely copy prompts.

### 3.14 Quality Auditor

Consumes all intermediate artifacts.

Outputs a machine-readable and human-readable report.

No subsystem may declare itself complete solely from prose.

---

## 4. Data flow

```text
Repository
→ inventory
→ project-index.json

Repository + project-index
→ stack detection
→ stack-profile.json

Repository + stack profile
→ static analyzers
→ symbols.json
→ dependencies.json
→ callgraph.json
→ routes.json
→ frontend-map.json

Optional runtime
→ runtime-traces.json

All evidence
→ evidence.json

All structure + model analysis
→ knowledge-graph.json

Knowledge graph + learner default
→ prerequisite-graph.json
→ curriculum.json

Curriculum + evidence
→ handbook/

All artifacts
→ quality-report.json
```

---

## 5. Universal core vs adapters

### Universal core knows

- files;
- modules;
- symbols;
- callers/callees;
- workflows;
- business roles;
- APIs;
- data flow;
- tests;
- evidence;
- concepts;
- prerequisites;
- teaching depth.

### Language adapter knows

- parser;
- symbol semantics;
- import/module semantics;
- inheritance/interface patterns;
- idiomatic entry points;
- test conventions.

### Framework adapter knows

- route registration;
- dependency injection;
- lifecycle;
- hidden runtime mechanisms;
- configuration conventions;
- extension hooks.

Adapters MUST enrich facts; they MUST NOT invent facts when project evidence conflicts.

---

## 6. Frontend architecture analysis

Frontend is first-class.

Extract, where possible:

- routing;
- pages/views;
- components;
- hooks/composables;
- state stores;
- API clients;
- types;
- streaming handlers;
- forms/validation;
- error/loading states;
- tests;
- build config.

Build feature edges such as:

```text
Page
→ Component
→ Hook
→ Store
→ API Client
→ Backend Endpoint
```

The handbook must connect frontend and backend by feature, not teach them as unrelated codebases.

---

## 7. Beginner-first rendering layer

Every content generator passes through a Beginner Renderer.

It checks:

- undefined terminology;
- missing prerequisites;
- unexplained commands;
- “magic framework” statements;
- missing intuition;
- missing minimal examples;
- jumps in abstraction level.

A concept can be rendered in layers:

```text
Quick intuition
Project-specific meaning
Minimal example
Source connection
Deep mechanism
```

The learner may later request compression, but default output uses this ladder.

---

## 8. Agent/role decomposition

Recommended logical roles:

- Extractor;
- Architect;
- Business Analyst;
- Runtime Investigator;
- Curriculum Planner;
- Beginner Teacher;
- Source Teacher;
- Mechanism Teacher;
- Writer;
- Verifier;
- Critic;
- Repairer;
- Interviewer;
- Extension Coach;
- VibeCoding Coach;
- Auditor.

These roles may run as prompts in one model, subagents, or separate model calls. Architecture must not depend on a specific model provider.

---

## 9. Deterministic-first rule

Use code/scripts for:

- inventory;
- checksums;
- classification scaffolding;
- AST extraction;
- dependency graphs;
- schema validation;
- coverage calculation;
- cross-reference validation;
- test execution;
- lint/typecheck;
- diff detection.

Use LLM reasoning for:

- business interpretation;
- architectural meaning;
- design rationale;
- teaching;
- curriculum;
- explanation;
- extension reasoning.

Never spend LLM tokens re-solving a deterministic enumeration problem if a script can establish the answer.

---

## 10. FAST vs DEEP mode

### FAST

- static analysis;
- stack detection;
- knowledge graph;
- prioritized handbook;
- evidence limited to available static/config/test data;
- no claim of runtime verification.

### DEEP

Adds:

- running project;
- representative requests;
- logs/traces;
- source/framework deep dives;
- broader verification;
- richer extension labs.

Quality reports must explicitly show which mode produced the result.

---

## 11. Revision-aware updates

Store repository revision in every top-level artifact.

On update:

1. compute changed files;
2. map changed files → symbols;
3. map symbols → workflows/claims/chapters;
4. invalidate impacted artifacts;
5. selectively rebuild;
6. re-run all impacted gates.

---

## 12. Failure behavior

The system should prefer:

```text
UNKNOWN / UNVERIFIED / NOT RUN
```

over invented certainty.

Examples:

- parser unsupported → generic file analysis + warning;
- project cannot start → no E3 evidence;
- call graph incomplete → mark graph partial;
- missing docs → do not infer undocumented commands as facts.

This is a feature, not a failure of presentation.

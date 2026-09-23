# Project DeepDive — Quality Gates

A gate is an executable or auditable condition. A chapter can be well written and still fail the system if evidence/coverage is inadequate.

---

## 1. Gate result vocabulary

```text
PASS
PASS_WITH_WARNINGS
PARTIAL
FAIL
NOT_RUN
```

Every gate records:

- result;
- measured values;
- failures;
- warnings;
- evidence;
- recommended repair.

---

## 2. G00 — Baseline Gate

Before repository modification or dogfooding:

- repository revision recorded;
- documented install/build/test commands discovered;
- representative baseline checks executed where feasible;
- pre-existing failures recorded.

Fail if the system cannot distinguish baseline failures from introduced regressions during its own development.

---

## 3. G01 — Repository Coverage Gate

Required:

- every tracked file indexed;
- every tracked file classified;
- `UNKNOWN` tracked files = 0;
- generated/vendor/ignored files contain a reason.

PASS does not require equal teaching depth.

---

## 4. G02 — Surface Coverage Gate

If a surface exists, it must appear in the project map:

- backend;
- frontend;
- database;
- tests;
- config;
- build;
- scripts;
- infrastructure;
- CI/CD;
- docs/tooling.

Fail if a major existing surface is silently omitted.

---

## 5. G03 — Core Source Coverage Gate

For all files/symbols classified `core`:

- 100% represented in knowledge graph;
- 100% connected to at least one module/workflow/mechanism or explicitly justified standalone role;
- 100% referenced by a source guide or coverage explanation.

---

## 6. G04 — Business Coverage Gate

For each core business capability:

- actor/trigger identified;
- happy path identified;
- major state/result identified;
- related code/workflows linked;
- failure/edge path documented where relevant.

---

## 7. G05 — Architecture Coverage Gate

Required:

- module map;
- dependency relations;
- entry points;
- data/infrastructure relations;
- request/async flows where applicable;
- frontend/backend boundary where applicable.

Warnings are allowed for unsupported static edges if marked partial.

---

## 8. G06 — Call Chain Gate

For each core runtime workflow:

- entry identified;
- important intermediate symbols identified;
- terminal side effect/result identified;
- missing edges explicitly marked;
- handbook chain matches graph evidence.

Fail if the handbook invents an edge absent from evidence without labeling inference.

---

## 9. G07 — Full-Stack Chain Gate

Applicable only when frontend and backend exist.

At least each core user-facing feature must attempt:

```text
UI
→ frontend logic/state
→ API client
→ backend entry
→ service/core
→ infrastructure
→ response
→ UI update
```

A missing stage must be stated, not hidden.

---

## 10. G08 — Evidence Gate

For critical claims:

- project-specific factual claims require evidence;
- architecture/call-chain claims prefer E1–E3;
- E6 is visibly inference;
- evidence references resolve to existing artifacts.

Suggested minimums for final PASS:

- critical project claims evidenced: 100%;
- unresolved critical claims: 0;
- non-critical unverified claims may remain only with explicit labels.

---

## 11. G09 — Hallucination Gate

Automatically/sample-check:

- cited file exists;
- cited symbol exists when extractable;
- claimed call edge exists or is labeled inferred;
- claimed config/dependency exists;
- claimed runtime observation has a recorded run.

Any fabricated file/symbol is a FAIL.

---

## 12. G10 — Mechanism Depth Gate

For each mechanism rated high importance:

- project usage explained;
- framework behavior explained;
- lower-level mental model explained;
- minimal equivalent/pseudocode included when useful;
- failure/trade-off included.

---

## 13. G11 — Beginner Teaching Gate

This is mandatory.

For every core chapter:

- prerequisite concepts listed;
- first-use jargon explained;
- an intuitive explanation exists;
- project-specific example exists;
- commands are explained before reliance;
- no unexplained jump from concept to advanced implementation.

Fail examples:

- “DI injects the bean” when DI/bean are unexplained;
- “use SSE” without describing SSE;
- source dump before business purpose;
- “run migrations” with no explanation of migration.

---

## 14. G12 — Learning Path Gate

Curriculum must:

- start from project purpose;
- introduce prerequisites before dependent concepts;
- reach at least one end-to-end workflow early;
- prioritize core concepts;
- avoid long detours into irrelevant textbook material.

---

## 15. G13 — Source Teaching Gate

For a sampled/required core symbol set, explanation includes:

- why;
- where in workflow;
- callers/callees;
- state/data;
- important code;
- tests or verification;
- extension impact.

---

## 16. G14 — VibeCoding Gate

At least one generated development lab must contain:

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

Prompts must include objective verification and scope control.

---

## 17. G15 — Verification Gate

Any lab claiming implementation success must have recorded real commands/results.

No PASS on “looks correct”.

---

## 18. G16 — Extension Gate

Extension lab must be based on actual project extension points.

It must specify:

- current architecture;
- changed files/symbols;
- call-chain impact;
- tests;
- risks.

---

## 19. G17 — Interview Gate

Interview set must contain project-specific questions and connected follow-ups.

At least some questions must require reasoning about:

- source;
- mechanism;
- failure;
- design;
- extension.

---

## 20. G18 — Consistency Gate

Cross-check:

- project map vs file index;
- knowledge graph vs handbook;
- call graph vs workflow diagrams;
- evidence vs claims;
- current revision vs generated revision.

Stale handbook revision is not PASS.

---

## 21. G19 — Runtime Verification Gate

Deep Mode only.

Record:

- start commands;
- environment assumptions;
- request/input;
- observed output;
- relevant logs/traces;
- failures.

If project cannot run, result is `NOT_RUN` or `PARTIAL`, never fabricated PASS.

---

## 22. G20 — Regression Gate

For Project DeepDive's own implementation:

- automated unit tests pass;
- integration/golden tests pass;
- lint/typecheck/build pass when configured;
- no expected fixture silently changes without review.

---

## 23. G21 — Dogfood Gate

Run against at least:

- one Python Agent project;
- one Java enterprise project.

Must demonstrate:

- repository-wide file classification;
- beginner prerequisites;
- source/call-chain teaching;
- evidence labels;
- VibeCoding lab generation;
- quality report.

---

## 24. Suggested quality report

```json
{
  "revision": "...",
  "mode": "DEEP",
  "status": "PASS_WITH_WARNINGS",
  "gates": {
    "repository_coverage": {
      "result": "PASS",
      "unknown_files": 0
    },
    "beginner_teaching": {
      "result": "PASS",
      "critical_undefined_terms": 0
    },
    "evidence": {
      "result": "PASS",
      "critical_unverified_claims": 0
    }
  },
  "critical_gaps": [],
  "warnings": []
}
```

---

## 25. Release rule

A generated handbook may be called:

- **Verified Handbook** only if all mandatory gates pass;
- **Draft Handbook** if generation completed but mandatory gates are pending;
- **Partial Handbook** if known unsupported areas remain.

Do not use “complete” as marketing language when the audit says otherwise.

---
name: project-deepdive
description: >-
  Use for learning, scanning, mapping, documenting, extending, verifying, updating,
  or auditing a local or open-source software project. Activate when a user asks
  to understand business and architecture, follow source or call chains, study
  mechanisms and failures, create beginner teaching materials, practice project
  interviews, perform VibeCoding, inspect repository coverage, review quality,
  or update learning material from a git diff. Covers the natural-language modes
  learn-project, project-scan, project-map, project-book, project-study,
  project-interview, project-extend, project-vibecode, project-audit, and
  project-update.
---

# Project DeepDive

Project DeepDive turns a real repository into an evidence-backed, beginner-first way to learn, extend, verify, and explain a project. The Skill retains its proven 17-part teaching workflow and the existing versioned scanners, analyzers, graph/course builders, reader tools, VibeCoding compiler, and author references.

The mode names below are **natural-language intent labels**, not commands. Route from what the user wants to accomplish; call a real script only when its documented input and output fit the task. The product is provider-neutral.

## Choose a mode

| Mode | User intent and response | Read for this task | Typical output |
|---|---|---|---|
| `learn-project` | Start from near zero and understand a project end to end. Establish business and architecture context, then choose a bounded learning topic. | [Foundation guide](references/project-foundation-guide.md), [reader contract](references/phase6-reader-handbook-contract.md), [source-topic writing card](references/phase6-source-topic-writing-card.md) | A project learning route and a beginner-led first topic; do not promise or generate a complete book by default. |
| `project-scan` | Inventory repository files, classify scope, or audit coverage. | [Foundation guide](references/project-foundation-guide.md), [coverage policy](references/coverage-policy.md) | Current project-index/coverage and a report of unknown, partial, or excluded files. |
| `project-map` | Understand system structure, business loops, source-backed relations, and learning prerequisites. | [Foundation guide](references/project-foundation-guide.md) | A map or prerequisite/course outline from valid existing evidence plus only the missing deterministic artifacts. |
| `project-book` | Author or update a learner-facing chapter or semantic handbook section. | [direct-source teaching guide](references/direct-source-teaching-guide.md), [reader contract](references/phase6-reader-handbook-contract.md), and [source-topic writing card](references/phase6-source-topic-writing-card.md); for a requested authenticated lifecycle, also read [source materialization](references/phase6-reader-source-materialization.md), [advanced workflows](references/legacy-and-advanced-workflows.md), and the [reader writer prompt](prompts/phase6-reader-handbook-writer.md). | One coherent Chinese, business-led chapter with source, full answers, and separate provenance; publish only if the user actually requests publication and applicable gates pass. |
| `project-study` | Teach or interactively study a file, mechanism, feature, request, or failure path. | [source-topic writing card](references/phase6-source-topic-writing-card.md), [learner AI collaboration card](references/learner-ai-collaboration-card.md), and [direct-source teaching guide](references/direct-source-teaching-guide.md) when turning the study into a reviewed chapter. | An explanation, diagram, source walk-through, value replay, and complete answers sized to the topic. |
| `project-interview` | Practice questions and follow-ups grounded in the target project. | [interview guide](references/project-interview-guide.md), [interview coach](prompts/project-interview-coach.md) | Project-specific questions, evidence-based answers, follow-ups, and feedback. |
| `project-extend` | Explore or implement a project-specific improvement. | [extension guide](references/project-extension-guide.md), [extension coach](prompts/project-extension-coach.md) | A requirement-to-architecture proposal or bounded change with source evidence and verification plan. |
| `project-vibecode` | Learn or practice AI-assisted development from requirement through verification and review. | [Phase 7 contract](references/phase7-vibecoding-contract.md), [beginner lab playbook](references/phase7-beginner-lab-playbook.md), [lab writer](prompts/phase7-beginner-lab-writer.md) | A taught lab that covers the full VibeCoding cycle; optionally compile an authored plan with the existing `scripts/project_vibecode.py`. |
| `project-audit` | Audit evidence, source/teaching coverage, quality, gaps, or acceptance status. | [quality audit guide](references/project-quality-audit-guide.md), [quality auditor](prompts/project-quality-auditor.md), [quality gates](references/product-quality-gates.md), [acceptance criteria](references/product-acceptance-criteria.md) | A scoped report with evidence, unknowns, uncovered work, and actual gate status. |
| `project-update` | Update project understanding from actual Git changes. | [update guide](references/project-update-guide.md), [update coach](prompts/project-update-coach.md) | Affected facts and topics, retained still-valid evidence, targeted revisions, and explicit remaining gaps. |

For `project-book`, choose from the user's request and actual inputs. Use the direct-source guide for a normal learning draft, missing/partial extraction, or absent authenticated inputs. Use the authenticated lifecycle only when that binding or publication is requested and its real inputs satisfy the documented prerequisites. If those prerequisites are absent, a human-reviewed draft can still be written; state that no formal receipt was issued. Do not run compilation, lifecycle, or target-project tests solely because the tools exist.

If intent spans modes, preserve the user's requested order and do only the needed steps. Existing files and valid evidence should be reused; do not rescan, regenerate a book, or run a runtime test merely because a mode can use those tools.

## Teaching contract

Assume a true beginner unless the user says otherwise. Build understanding in this order:

```text
intuition → prerequisites → project/business context → architecture
→ complete user-visible flow → source → mechanism → failure → extension
```

For learner-facing material:

- Explain concepts, abbreviations, framework behavior, and commands before relying on them. State unknowns instead of asking the learner to infer them.
- Start source teaching with a real scene and input. Present the relevant source in the place where the explanation needs it; cover the selected source scope in full rather than hiding omitted lines behind ellipses.
- Create a complete, file-specific source-line responsibility table at granularity suited to the code. Account for each line in scope and explain each important construction, call, and state change; do not use one generic table for unrelated files.
- Before, within, and after each source block, connect intent to code and replay actual values, branches, returns, handoffs, async continuation, error handling, side effects, and the next step. Walk both a normal path and a meaningful failure path.
- Use diagrams where they make a business loop, architecture, or cross-layer handoff easier to see. When the project has a frontend, connect UI, state, API client, backend, persistence, and UI update as far as evidence allows.
- Include complete exercise answers and reasoning. Do not leave answers as hints or claim students can fill in a missing explanation.
- Separate source facts, observed runtime results, external facts, and inference. A static candidate is not executed behavior; a test is not run unless it actually ran.

Keep all 17 responsibilities in [the legacy full teaching template](references/批次讲解全文模板.md) available for compatible batches. For an ordinary new theme, preserve those teaching duties through the source card and adapt the section layout to the learning route; do not mechanically force 17 headings or batch-based scheduling.

Evidence helpers prove only their stated scope. Source materialization and numeric line-range coverage help locate or account for evidence, but do not limit prose length, learner scope, or depth and do not establish semantic PASS. Hashes, IDs, schemas, counts, structural validity, and successful CLI exit do not certify teaching quality or release acceptance.

Use one coherent Chinese semantic directory for learner navigation and chapters. Keep machine-readable artifacts, internal IDs, provenance, audit receipts, and hash records alongside but outside the learner-facing body. Do not make internal phase names or production status part of learner concepts.

## Existing deterministic tools

Read [the foundation guide](references/project-foundation-guide.md) only when the current task needs its actual Phase 2–5 commands, input/output chain, schema-version limits, parser dependencies, or partial-result rules. It documents the existing flags for:

- Phase 2 Git snapshot scan and coverage validation;
- Phase 3A stack declarations, language analyzers, and the integrated Phase 3 runner;
- Phase 4 source graph, semantic-proposal import, claim-evidence audit, and explicit Markdown evidence selection;
- Phase 5 prerequisite graph and curriculum outline.

Use the relevant script's `--help` before an unfamiliar invocation. Do not invent a CLI from a mode name or run target code, builds, tests, or package managers while collecting static evidence.

## Phase 7–12 routes

| Work area | Read next | Current tool boundary |
|---|---|---|
| Phase 7 VibeCoding | [contract](references/phase7-vibecoding-contract.md) and [playbook](references/phase7-beginner-lab-playbook.md) | Existing `project_vibecode.py` compiles an explicitly authored plan; it does not perform implementation, execution, or semantic acceptance. |
| Phase 8 project extension/interview | [extension](references/project-extension-guide.md), [interview](references/project-interview-guide.md) | Reuse the project map and source evidence; adapt to the target language and mechanisms. |
| Phase 9 quality review | [audit guide](references/project-quality-audit-guide.md) | Pair deterministic reports with independent teaching review; report unrun checks as unrun. |
| Phase 10 runtime verification | [runtime guide](references/project-runtime-guide.md) | FAST does not execute the target; DEEP is explicit opt-in and requires safe, real execution evidence. |
| Phase 11 representative validation | [dogfood guide](references/project-dogfood-guide.md) | Validate a few representative cases and reuse valid evidence; this is not a requirement to publish multiple full books. |
| Phase 12 incremental update | [update guide](references/project-update-guide.md) | Map actual Git diffs to affected facts/topics, preserve still-valid evidence, and restore only invalidated evidence. |

These routes and their documents do not mean Phases 7–12 or final acceptance are complete. Do not turn a written guide, static compiler output, fixture, historical acceptance record, or representative sample into a claim of release readiness.

## Optional and compatible workflows

Answer books, general learning units, curriculum run/resume/repair, whole-book assembly/audit, old batch publication, and exact-byte reader-manuscript publication are opt-in. Their real tools, prerequisites, status boundaries, and safe order are indexed in [legacy and advanced workflows](references/legacy-and-advanced-workflows.md). Do not run a publication path unless the user asks to publish; never bypass its applicable formal gate.

## Formal product requirements

The package carries complete mirrors of [Quality Gates](references/product-quality-gates.md) and [Acceptance Criteria](references/product-acceptance-criteria.md) so installed use does not depend on repository-root files. The root `QUALITY_GATES.md` and `ACCEPTANCE_CRITERIA.md` remain authoritative; synchronize their package mirrors when either changes. Do not weaken or reinterpret a formal gate based on a helper's output.

Use [the reference index](references/README.md) to find other package-local templates and compatibility material. Repository plans and historical reports are optional archives, never required to use this Skill.

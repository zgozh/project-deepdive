# Project DeepDive — Acceptance Criteria

These criteria define when the V3 upgrade is actually usable.

---

## AC-01 Existing project can still be understood

**Given** the existing `replicate-learning` repository  
**When** the upgrade is complete  
**Then** its original core teaching behaviors remain accessible or have documented replacements  
**And** no major original capability is silently removed.

---

## AC-02 Beginner can start from near zero

**Given** a learner who knows only basic programming concepts  
**When** they begin a generated learning path  
**Then** the first modules explain project purpose, execution environment and required terms  
**And** no core chapter depends on an undefined prerequisite.

---

## AC-03 Beginner can follow a core request

**Given** a project with a representative request/workflow  
**When** the learner reaches the first core workflow chapter  
**Then** they can see:

- what triggers it;
- why it exists;
- major business steps;
- architecture path;
- important source;
- data/state changes;
- result;
- relevant failures.

---

## AC-04 Full repository is accounted for

**Given** any dogfood repository  
**When** repository scanning finishes  
**Then** every tracked file appears in the file index  
**And** final tracked `UNKNOWN` count is zero  
**And** generated/vendor/ignored classifications have reasons.

---

## AC-05 Auxiliary files are not lost

**Given** a project containing Docker, CI, scripts, configs, tests and docs  
**When** the handbook is compiled  
**Then** those surfaces appear in project/engineering maps at appropriate depth  
**And** their relationship to runtime/development is explained.

---

## AC-06 Frontend is first-class

**Given** a full-stack repository  
**When** a core user feature is analyzed  
**Then** frontend page/component/state/API-client participation is connected to the backend path  
**And** frontend is not reduced to a one-paragraph framework description.

---

## AC-07 Evidence-backed factual claims

**Given** a handbook chapter  
**When** a critical project-specific claim is inspected  
**Then** it maps to E0–E5 evidence or is explicitly marked E6 inference  
**And** no nonexistent file/symbol is cited.

---

## AC-08 Runtime honesty

**Given** a project that cannot be started in the environment  
**When** Deep Mode attempts runtime analysis  
**Then** runtime status is `NOT_RUN` or `PARTIAL`  
**And** no static inference is reported as observed behavior.

---

## AC-09 Core source deep dive

**Given** a core class/function  
**When** its source lesson is generated  
**Then** the lesson explains why it exists, callers, callees, business context, important code, mechanism and extension implications.

---

## AC-10 Framework penetration

**Given** a core framework call  
**When** the lesson discusses it  
**Then** it explains what the framework likely/actually does underneath with evidence/general-knowledge labeling  
**And** provides a simpler equivalent mental model or implementation when educationally useful.

---

## AC-11 VibeCoding from requirement to verified change

**Given** a real extension requirement  
**When** `project-vibecode` is used  
**Then** the workflow produces:

- baseline;
- discovery;
- requirement spec;
- plan;
- vertical slices;
- implementation prompts;
- test/verification plan;
- review;
- repair;
- full-chain validation;
- retrospective.

---

## AC-12 VibeCoding teaches, not only executes

**Given** a beginner learner  
**When** a VibeCoding phase starts  
**Then** the system explains what the phase is and why it matters  
**And** identifies what the human should review.

---

## AC-13 Deterministic checks are real

**Given** an implementation lab that claims success  
**When** completion is reported  
**Then** configured build/lint/typecheck/tests were actually invoked where feasible  
**And** command failures are visible rather than overwritten by model confidence.

---

## AC-14 Small-model delegation is bounded

**Given** multi-agent capability is available  
**When** narrow/repetitive work is delegated  
**Then** small/fast models may be used  
**But** architecture/security/final integration judgment is escalated when complexity requires it  
**And** actual model usage is not falsely reported.

---

## AC-15 Project-specific extension

**Given** a project with an Agent registry or analogous plugin extension point  
**When** an extension lab is generated  
**Then** the lab names the real extension points and affected call chain  
**And** does not teach a fictional architecture.

---

## AC-16 Interview is based on the project

**Given** a learner who completed a workflow lesson  
**When** interview mode starts  
**Then** questions reference that workflow and progressively deepen from business to source/mechanism/failure/design.

---

## AC-17 Quality report blocks false completeness

**Given** missing critical evidence or coverage  
**When** handbook generation ends  
**Then** overall status cannot be `PASS`  
**And** the report identifies required repair.

---

## AC-18 Incremental update

**Given** a generated handbook at revision A  
**And** repository revision B changes only a subset of files  
**When** update is run  
**Then** impacted entities/chapters are identified  
**And** unaffected content need not be regenerated.

---

## AC-19 Python dogfood

Run on a non-trivial Python/Agent repository.

Must demonstrate:

- file inventory;
- stack detection;
- at least one core workflow;
- source deep dive;
- beginner prerequisite section;
- evidence;
- extension/VibeCoding lab;
- quality report.

---

## AC-20 Java dogfood

Run on a non-trivial Java enterprise repository.

Must demonstrate equivalent capabilities despite different language/framework structure.

---

## AC-21 Full-stack dogfood if available

At least one dogfood target should contain a frontend or UI layer.

A core feature must be traced UI-to-backend-to-result.

---

## AC-22 No architecture theater

**When** the repository implementation is inspected  
**Then** newly introduced modules have real working responsibility  
**And** the implementation is not primarily empty placeholders/templates.

---

## AC-23 Stable persisted artifacts

After a successful compilation, a new chat/session can resume study using on-disk artifacts without depending on previous chat history.

---

## AC-24 Beginner Teaching Gate

Sample at least 10 core lessons across two dogfood projects.

PASS requires:

- no unexplained critical jargon;
- prerequisites before dependent concepts;
- project-specific examples;
- clear abstraction ladder;
- commands explained where used.

---

## AC-25 Final release condition

The upgrade is releasable only when:

- all mandatory quality gates pass;
- AC-19 and AC-20 pass;
- no critical unresolved regression exists;
- documentation reflects actual implemented commands/structure;
- a clean repository checkout can execute the documented core workflow.

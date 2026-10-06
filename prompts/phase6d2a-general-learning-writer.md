# Phase 6D2A General Learning Writer

Write one beginner-first lesson from the authenticated facts and Writer packet. This is teaching-only content: do not state what the target project does, does not do, or how it behaves. Do not add project claims, source facts, evidence IDs, citations, file paths, excerpts, or project references. The selected Phase 5 `scope` and `origin` describe curriculum provenance; they do not authorize project-behavior statements. A `PROJECT_SPECIFIC` prerequisite primer with empty project references still needs a general concept explanation only.

Use ordinary, clear language. Explain each listed prerequisite before depending on it, and explain every introduced concept. Make the worked example generic and independent of the target repository. Define jargon the first time it appears. Structural validation checks the fields and concept-key coverage only; it does not establish factual accuracy or beginner quality. General factual review, beginner review, and answer-book review remain `NOT_RUN`.

Keep structural labels, keys, IDs, markers, and canonical footnotes exactly; they are machine-readable.
Write learner-facing explanations and questions in the learner’s language; where claims are allowed, translate faithfully while preserving meaning and the exact citation marker.
Explain required English markers in the learner’s language where the format permits; retain fixed callouts and literal markers unchanged.

For HTTP request/protocol examples and any pseudocode or code whose meaning depends on formatting, use a fenced Markdown code block with an appropriate language tag (`text` for illustrative pseudocode, the actual language for code). Preserve meaningful line breaks, indentation, and blank lines; keep explanations outside the block. Label illustrative snippets as examples, not complete runnable code. After writing, inspect the final learner-facing lesson and answer-book rendering to confirm that the Markdown visibly preserves the intended formatting; checking only the JSON string is insufficient. Build multiline snippet values with actual newline characters and intended indentation, then serialize the JSON once. Decode the serialized value and inspect the final rendering to confirm intended line breaks appear as line breaks; literal `\n` text is appropriate when the lesson explicitly teaches that escaped sequence.

Write authored text as UTF-8 without a BOM and read it back as UTF-8. On Windows, avoid the default PowerShell pipeline for authored non-ASCII text; use `apply_patch` or .NET UTF-8 file writing. Preserve Unicode as written; do not force ASCII or replace non-ASCII characters.

Create `general-unit-candidate.json` beside the prepared facts using this shape. Copy the exact unit ID, prepared-facts SHA-256, `origin`, and `scope` from the packet. Copy all required concept keys exactly once.

```json
{
  "artifact_kind": "general-unit-candidate",
  "schema_version": "1.0.0",
  "curriculum_unit_id": "<exact selected unit id>",
  "facts_sha256": "<exact prepared-facts sha256>",
  "origin": "<exact origin>",
  "scope": "<exact curriculum scope>",
  "writer_alias": "<your alias>",
  "unit_key": "<copy only for CANDIDATE>",
  "primer_concept_key": "<copy only for PREREQUISITE_PRIMER>",
  "prerequisite_node_id": "<copy only for PREREQUISITE_PRIMER>",
  "concept_epistemic_status": "<copy only for PREREQUISITE_PRIMER>",
  "lesson": {
    "intuition": "<plain-language mental model>",
    "prerequisite_explanations": [
      {"concept_key": "<each required key>", "explanation": "<beginner-readable explanation>"}
    ],
    "concept_explanations": [
      {"concept_key": "<each introduced key>", "explanation": "<beginner-readable explanation>"}
    ],
    "worked_example": "<generic worked example>",
    "common_misconception": "<one possible misunderstanding and its correction; do not claim prevalence without evidence>",
    "learning_check": {
      "question": "<self-contained beginner-readable question>",
      "reference_answer": "<explicit worked answer to this exact question>",
      "hints": ["<early hint>", "<more direct hint>"],
      "rubric": ["<qualitative success criterion>"]
    }
  }
}
```

The schema allows either `unit_key` or the three primer fields, not both. Remove every placeholder and every field that does not belong to the selected origin. The question is rendered in the lesson without its answer. A separate answer booklet repeats that exact question and includes the reference answer, hints, and rubric. Keep all four learning-check parts general; do not imply that any answer has been verified.

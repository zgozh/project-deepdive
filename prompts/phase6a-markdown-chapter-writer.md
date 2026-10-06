# Phase 6A Markdown Chapter Writer

Write one beginner-first chapter draft using only the operator-provided `writer-packet.md` and Phase 4E1 claim-candidates artifact. For source verification, read only referenced bytes through the authorized read-only Git object reader at the exact revision in the packet; never inspect the current worktree for a `git-tree` snapshot or any other source path. This is an explicit human/model authoring step: do not access the network, call a model-provider API, execute target code, or write to the target repository. Return only Markdown text to the operator; the operator writes `chapter.md` separately. Do not ask the learner to answer or approve anything.

## Evidence boundary

- The exact revision, snapshot kind, Phase 3 manifest digest, claim IDs, evidence levels, and safe locators are in `writer-packet.md`. For `git-tree`, inspect referenced source bytes only through the pinned Git object reader at that revision. Never read the current worktree. For a worktree snapshot, use only the authenticated relative locators, without following links.
- Read only the selected claim text from the provided claim-candidates artifact when needed. E1/E2/4E1/4E2 establish a reference trail, not semantic entailment. Do not turn an ID, locator, parser observation, E6 proposal, or curriculum title into a project fact.
- A v1 `Claim:` sentence or v2 paragraph/bullet with a suffix marker may state only a packet claim explicitly marked `SUPPORTED`, with at least one E0–E5 level and no E6. Use its exact `CLAIM-<64 lowercase hex>` ID. If the packet does not support a project section, use the fixed evidence-gap callout. Do not invent a replacement fact.
- Keep source bodies, excerpts, secrets, credentials, absolute machine paths, and operational model/tool transcripts out of sidecars and logs. For v1, no excerpt is required. Any optional v1 excerpt goes only in `chapter.md` using the prompt's exact excerpt marker and must be copied byte-for-byte from the permitted snapshot.

## Beginner-first teaching

Assume the reader is new to programming and this project. Explain necessary terms in plain language before relying on them. Build from intuition, then prerequisites, project use, architecture, source, mechanism, failure/debugging, and extension. In v1, keep general teaching clearly marked `General:`; in v2, uncited paragraphs and bullets are classified and reviewed as GENERAL, including any unmarked project assertion. Keep general teaching separate from supported project facts. Do not promise that reading this chapter alone creates senior-level skill.

Keep structural labels, keys, IDs, markers, and canonical footnotes exactly; they are machine-readable.
Write learner-facing explanations and questions in the learner’s language; where claims are allowed, translate faithfully while preserving meaning and the exact citation marker.
Explain required English markers in the learner’s language where the format permits; retain fixed callouts and literal markers unchanged.

Treat every `Question:` as independently readable by a learner with near-zero experience: on its own, it must make clear what it asks without relying on definitions in other sections, metadata, or the answer. It must be answerable from the chapter's teaching and supported project facts, with no unstated assumptions. Use plain language or briefly define each necessary unfamiliar term in that same single line before asking about it. Keep definitions limited to what that question needs; do not turn the question into a glossary, answer, or hint. Any project-specific premise must be supported by the question's listed `claims`/`evidence` references; do not assume runtime behavior or a handoff that the packet does not establish.

Before emitting any Markdown, perform an internal, ordered first-use concept/term audit across the whole planned chapter, including Learning checks, and following the required section order. For every term a true beginner may not know, check that a plain-language explanation appears before or at its first use and explains the concept precisely rather than substituting a near-synonym. Include structural-analysis and graph terms, code symbols and method names, control-flow terms, and static-versus-runtime distinctions when used. Before first use of an unfamiliar code identifier, explain only the generic concept needed to read it (for example, what a method, callback, or parameter is) in a v1 `General:` line or an uncited v2 paragraph/bullet, which is reviewed as GENERAL. State the actual identifier's kind or project-specific role only when supported: use an eligible `Claim:` line in v1 or a suffix-marked paragraph/bullet in v2. If support is absent, leave its kind or role unknown rather than infer it from spelling. Keep project-specific behavior in eligible `Claim:` lines in v1 or suffix-marked v2 lines with their exact canonical footnotes, and never promote E1/E6/UNKNOWN material to a fact. This audit is internal: add no glossary, extra section, or other chapter output.

Before drafting, make this compact plan internally: map one evidence-bounded feature flow from the learner's goal through only the project steps supported by eligible claims, leaving unsupported handoffs unknown; then give each required section one learner question to answer. In v1, express supported project steps in eligible `Claim:` lines and general teaching in `General:` lines; in v2, use suffix-marked paragraphs/bullets for supported project facts and treat every uncited paragraph/bullet as GENERAL, so do not use one to smuggle an unsupported fact. Put only the minimum concepts needed to start in `Prerequisite`; introduce later concepts just before first use in v1 `General:` lines or uncited v2 paragraphs/bullets instead of collecting definitions in a glossary. In substantive sections whose v1 grammar permits both `General:` and `Claim:` lines, connect each relevant supported step to the flow: `General:` explains why that kind of step matters or how it works in general, while `Claim:` states only the supported project fact. In v2, uncited lines provide general explanation and suffix-marked lines state supported project facts. Do not let a heading definition or isolated claim stand in for that explanation. In `Project use`, v1 remains Claim-only-if-supported or the exact gap callout; in v2, mark each supported project-use fact with its suffix citation or use the exact gap callout. Place any needed general teaching in `Prerequisite` or another earlier section, never in `Project use`. In `Extension`, when safe general teaching is possible, give a clearly hypothetical change and a way to verify it in a v1 `General:` line or uncited v2 paragraph/bullet, even if no project-specific extension claim is eligible; describe a verification approach, not a result. Use the evidence-gap callout in `Extension` only when no safe, useful general teaching is possible, and follow the stricter section-specific rules below elsewhere. Keep this plan internal and emit only the required Markdown grammar.

## Required Markdown grammar

The structure and line grammar below describe the default v1 format. Use v2 only under the explicit opt-in described at the end of this prompt.

Return only UTF-8 Markdown with LF line endings, no BOM, and one final LF. Use exactly this structure and spelling:

```text
# Chapter

## Intuition
<one or more General: lines>

## Prerequisite
<General: lines or the exact evidence-gap callout>

## Project use
<Claim: line(s) only when supported; otherwise the exact callout>

## Architecture
<General: lines or the exact evidence-gap callout>

## Source
<General: lines or the exact evidence-gap callout>

## Mechanism
<General: lines or the exact evidence-gap callout>

## Failure and debugging
<General: lines or the exact evidence-gap callout>

## Extension
<General: lines or the exact evidence-gap callout>

## Learning checks

:::exercise id=EX-CHAPTER-<chapter hash>-recall-purpose type=recall prerequisites=<sorted keys|none> claims=<sorted IDs|none> evidence=<sorted IDs|none>
Question: <one nonempty single-line question>
:::

:::exercise id=EX-CHAPTER-<chapter hash>-trace-next type=trace_predict prerequisites=<sorted keys|none> claims=<sorted IDs|none> evidence=<sorted IDs|none>
Question: <one nonempty single-line question>
:::

[^CLAIM-<used claim hash>]: <copy the exact canonical JSON definition for this eligible claim from the prepared writer packet>
```

For each section, every nonblank line in the first eight sections must be exactly one of:

- `Claim: <one project-specific statement ending in .?!> [^CLAIM-<64 lowercase hex>]`
- `General: <one clearly general teaching statement ending in .?!>`
- `> Evidence gap — NOT ESTABLISHED IN THIS SLICE`

Use one claim marker at the end of each `Claim:` line; the same eligible Claim ID may be reused on multiple lines, including across sections. After the learning checks, emit exactly one sorted canonical footnote definition for each distinct used Claim ID, copying it exactly from the Writer packet; do not construct or edit its JSON. The packet contains definitions only for eligible supported claims. Add no extra definition or prose there. Exercise IDs use the chapter ID plus a stable objective key; use unique objective keys. Include at least one recall and one trace/predict block. Exercise blocks contain questions only—no answer, hint, rubric, solution, or request for a learner response. Every question is one line; cited claim/evidence IDs and prerequisite keys must come from the packet.

The verifier checks syntax, identity, reference sets, and byte identity for an optional marked excerpt. It does not verify that prose is true, that a citation entails the sentence, that every natural-language claim was marked, or that the chapter is pedagogically effective. The result remains `DRAFT`/`PARTIAL`; independent evidence review, privacy review, and a Beginner Critic are still required.

Operators may run `python skills/project-deepdive/scripts/chapter_workflow.py preflight --markdown PATH` before the slower formal verification. A lexical `PASS` checks only UTF-8/BOM/CR/final-LF/tab and footnote-tail line shape; it does not validate full chapter syntax, authenticate facts or source, or verify canonical claims, footnotes, or excerpt identity. Continue with formal verification.

## Opt-in Markdown v2

The required grammar above remains Markdown v1 and is the default. Use v2 only when the task explicitly requests it. Put the exact comment `<!-- project-deepdive-chapter-format: v2 -->` on the physical line immediately after the title line.

In the first eight sections, write natural multi-sentence paragraphs or top-level dash bullets. Keep each paragraph or bullet on one physical line, and end it with a period, exclamation mark, question mark, or the corresponding Chinese full stop, exclamation mark, or question mark. Do not add visible Claim: or General: prefixes. A single supported marker of the exact form `[^CLAIM-<64 lowercase hex>]` may follow the final punctuation at the end of a line; without that marker, the whole line is reviewed as GENERAL, including any unmarked project assertion. With the marker, the whole line is reviewed as CLAIM. Repeated supported claim IDs are allowed, with one sorted canonical footnote definition per distinct ID.

The v2 Source section must contain at least one excerpt. For each excerpt, choose 1–20 consecutive source lines wholly inside an authenticated E1 locator, and display its locator immediately before the excerpt as:

Source location: repo-relative/path.py:first-last

Follow the caption immediately with a marker whose fields appear in this exact order: `<!-- SOURCE-EXCERPT evidence=EVID-<id> path=<canonical percent-encoded path> lines=<first>-<last> sha256=<64 lowercase hex> -->`. Then put a line containing three backticks followed by text, the selected source line bytes, a line containing three backticks, and the exact closing comment `<!-- /SOURCE-EXCERPT -->`. The caption path is the decoded repository-relative path; the marker path is its canonical percent-encoded form. The caption and marker must agree exactly on path and range. That range is a 1–20-line subrange contained in the authenticated E1 locator (`line_start <= first <= last <= line_end`), not necessarily the locator's full range. The excerpt digest is SHA-256 over the selected original snapshot bytes. If the range ends at source EOF and its final line has no terminal LF, the LF immediately before the closing fence is Markdown framing only: exclude that one LF from the selected bytes and digest. Otherwise preserve the selected source line endings. Do not intentionally copy credentials or other sensitive material. Provenance validation establishes provenance and boundedness, not that source content is secret-free; independent privacy review remains required. If no suitable safe E1 range is available for a pilot, use v1.

In v2, headings, the fixed evidence-gap callout, caption and excerpt metadata, exercise metadata, and canonical footnotes remain structural lines. Every other nonblank line in the first eight sections must follow the paragraph or bullet grammar and becomes one review occurrence. Keep the existing beginner-first teaching, connected feature flow, question-only exercises, and exact heading order. Do not add diagrams, raw HTML, or arbitrary fenced blocks; only the exact format comment and bounded SOURCE-EXCERPT block are allowed markup.

# Phase 6 reader source materialization

Use this adapter when a reader lesson needs exact source excerpts tied to an authenticated curriculum run plan. It fills source slots in a complete lesson template; it does not write files or publish a lesson.

## Local pure-render authoring helper

For a local draft built from an already frozen source cache, the thin authoring helper can compose a lesson and its answer section without constructing an authenticated run plan:

~~~powershell
python -X utf8 skills/project-deepdive/scripts/reader_manuscript_authoring.py --lesson-template path/to/lesson-template.md --answers-template path/to/answers-template.md --sources path/to/sources.json --cache-root path/to/frozen-cache --output-dir path/to/local-draft --answer-mode append
~~~

The source manifest must pass the existing v1 shape validator. Cache paths are resolved under the supplied cache root; the command does not fall back to the working tree. It writes the complete composed chapter as lesson.md, the exact rendered answer section as answers.md, a body-free helper provenance file, and a copy of sources.json. Existing identical outputs are reused; different bytes are left untouched and cause a conflict. A successful render reports LOCAL_RENDERED, NOT_AUTHENTICATED, and NOT_REVIEWED: hashes and reversible source blocks establish byte consistency only, not source authority or teaching quality. Continue through the existing authenticated source and independent teaching review gates before treating the draft as accepted.

## Author flow

1. Write the full causal explanation first: learner question, needed concepts, project behavior, source responsibilities, mechanism, failure behavior, and checks. Choose source ranges that support that explanation in this chapter. A source path and line range by themselves are navigation, not teaching.
2. Give every SourceBlockPlan a unique slot ID and a frozen project-relative path, language, and 1-based inclusive line range. Put the exact whole-line marker `@@source:<slot_id>@@` where that excerpt belongs in the UTF-8 lesson template.
3. Call materialize_reader_theme_sources(auth_context, template_bytes, plans, frozen_sources=...). The live-authenticated run plan supplies the repository revision and project-index digest. frozen_sources is optional and maps source paths to cached immutable bytes.
4. Save the returned bytes and review metadata in the author's own staging workflow. The adapter does not write, publish, call a model, or decide whether the explanation teaches the behavior.

Example:

~~~python
from reader_source_blocks import SourceBlockPlan
from reader_theme_sources import materialize_reader_theme_sources

plans = [
    SourceBlockPlan(
        slot_id="upload-controller",
        path="src/main/java/example/UploadController.java",
        start_line=24,
        end_line=38,
        language="java",
    ),
]
lesson_bytes, audit = materialize_reader_theme_sources(
    auth_context,
    lesson_template.encode("utf-8"),
    plans,
    frozen_sources=optional_source_cache,
)
~~~

The adapter checks the project-index bytes against the authenticated plan, then validates every cache hit against that index's text-file size and SHA-256. A mismatched cache fails closed. On a cache miss it reads the pinned Git object or bounded workspace file and applies the same index size/hash check. Repeated slots for one path share one source read. Source paths, line ranges, placeholder coverage, excerpt hashes, and rendering remain the responsibility of the existing source-block renderer.

## Reading the result

The rendered code preserves source bytes. Any inline teaching comments recorded as overlays are additions for the reader, not original source text; consult the returned source-block metadata to distinguish them and recover the exact excerpt. Explain any simplified or hand-written example explicitly in the surrounding lesson, including which production steps it omits.

The metadata binds the rendered source bytes and revision to the authenticated plan and project-index hashes and reports `RENDERED` with `NOT_REVIEWED`. This byte-level source binding is not a claim that the facts were semantically verified, the learner understood the material, the answers are correct, or a formal four-file freeze was accepted. The helper imposes no narration-depth limit and does not replace the Principal's semantic teaching review.

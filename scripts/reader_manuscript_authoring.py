"""Compose a locally rendered reader manuscript; never authenticates or reviews it."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

import reader_handbook_lifecycle as lifecycle
import reader_handbook_review as handbook_review
import reader_source_blocks


class ReaderManuscriptAuthoringError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReaderManuscriptComposition:
    chapter_bytes: bytes
    answers_bytes: bytes
    provenance: dict[str, Any]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _markdown(raw: bytes, kind: str) -> None:
    try:
        lifecycle._validate_markdown(raw, kind=kind)
    except lifecycle.ReaderHandbookLifecycleError as exc:
        raise ReaderManuscriptAuthoringError(exc.code) from exc


def _verify_blocks(raw: bytes, proofs: Any, cache: Mapping[str, bytes]) -> list[dict[str, Any]]:
    if not isinstance(proofs, list):
        raise ReaderManuscriptAuthoringError("SOURCE_PROVENANCE_INVALID")
    verified = []
    for proof in proofs:
        if not isinstance(proof, Mapping):
            raise ReaderManuscriptAuthoringError("SOURCE_PROVENANCE_INVALID")
        caption, opening, closing = (proof.get(key) for key in (
            "caption", "opening_fence", "closing_fence",
        ))
        overlays = proof.get("external_overlay_lines")
        path = proof.get("source_path")
        if (
            not all(isinstance(item, str) for item in (caption, opening, closing, path))
            or not isinstance(overlays, list)
            or not all(isinstance(item, str) for item in overlays)
            or path not in cache
        ):
            raise ReaderManuscriptAuthoringError("SOURCE_PROVENANCE_INVALID")
        prefix = (caption + "\n" + "".join(overlays) + opening + "\n").encode("utf-8")
        closing_line = (closing + "\n").encode("utf-8")
        rendered_hash = proof.get("rendered_markdown_sha256")
        excerpt_hash = proof.get("excerpt_sha256")
        if (
            not isinstance(rendered_hash, str)
            or not isinstance(excerpt_hash, str)
            or proof.get("source_sha256_match") is not True
            or proof.get("excerpt_sha256_match") is not True
            or _sha(cache[path]) != proof.get("source_sha256")
        ):
            raise ReaderManuscriptAuthoringError("SOURCE_DIGEST_MISMATCH")

        candidates = []
        start = raw.find(prefix)
        while start >= 0:
            if start == 0 or raw[start - 1 : start] == b"\n":
                close = raw.find(closing_line, start + len(prefix))
                while close >= 0:
                    if close == 0 or raw[close - 1 : close] == b"\n":
                        block = raw[start : close + len(closing_line)]
                        if _sha(block) == rendered_hash:
                            candidates.append(block)
                            break
                    close = raw.find(closing_line, close + 1)
            start = raw.find(prefix, start + 1)
        if len(candidates) != 1 or raw.count(candidates[0]) != 1:
            raise ReaderManuscriptAuthoringError("SOURCE_BLOCK_OCCURRENCE_INVALID")
        try:
            excerpt = reader_source_blocks.strip_source_overlays(candidates[0], proof)
        except reader_source_blocks.SourceBlockError as exc:
            raise ReaderManuscriptAuthoringError("SOURCE_PROVENANCE_INVALID") from exc
        if _sha(excerpt) != excerpt_hash:
            raise ReaderManuscriptAuthoringError("SOURCE_DIGEST_MISMATCH")
        verified.append(dict(proof))
    return verified


def compose_reader_manuscript(
    lesson_template: bytes,
    answers_template: bytes,
    source_manifest: Mapping[str, Any],
    source_reader: Callable[[str], bytes],
    *,
    answer_mode: str = "append",
    required_symbols: Mapping[
        str, Sequence[reader_source_blocks.RequiredSourceSymbol]
    ] | None = None,
) -> ReaderManuscriptComposition:
    """Render exact UTF-8 bytes; the manifest revision remains caller-provided."""
    if answer_mode not in {"append", "embedded"}:
        raise ReaderManuscriptAuthoringError("ANSWER_MODE_INVALID")
    if not isinstance(lesson_template, bytes) or not isinstance(answers_template, bytes):
        raise ReaderManuscriptAuthoringError("TEMPLATE_INVALID")
    if not callable(source_reader) or not isinstance(source_manifest, Mapping):
        raise ReaderManuscriptAuthoringError("INPUT_INVALID")
    revision = source_manifest.get("source_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ReaderManuscriptAuthoringError("SOURCES_INVALID")
    try:
        manifest = handbook_review.validate_reader_theme_source_manifest(
            source_manifest, source_revision=revision,
        )
    except handbook_review.ReaderHandbookReviewError as exc:
        raise ReaderManuscriptAuthoringError(exc.code) from exc

    plans: dict[str, list[reader_source_blocks.SourceBlockPlan]] = {
        "lesson.md": [], "answers.md": [],
    }
    if required_symbols is None:
        symbols_by_manuscript = {}
    elif not isinstance(required_symbols, Mapping) or any(
        manuscript not in plans for manuscript in required_symbols
    ):
        raise ReaderManuscriptAuthoringError("INPUT_INVALID")
    else:
        symbols_by_manuscript = required_symbols
    for block in manifest["source_blocks"]:
        plans[block["manuscript"]].append(reader_source_blocks.SourceBlockPlan(
            slot_id=block["slot_id"],
            path=block["path"],
            start_line=block["start_line"],
            end_line=block["end_line"],
            language=block["language"],
            annotations=block["annotations"],
            expected_source_sha256=block["expected_source_sha256"],
            expected_excerpt_sha256=block["expected_excerpt_sha256"],
            revision=revision,
        ))

    cache: dict[str, bytes] = {}

    def read_once(path: str) -> bytes:
        if path not in cache:
            try:
                cache[path] = source_reader(path)
            except Exception as exc:
                raise ReaderManuscriptAuthoringError("SOURCE_READ_FAILED") from exc
            if not isinstance(cache[path], bytes):
                raise ReaderManuscriptAuthoringError("SOURCE_READ_FAILED")
        return cache[path]

    def render(
        template: bytes,
        source_plans: list[reader_source_blocks.SourceBlockPlan],
        manuscript: str,
    ):
        try:
            return reader_source_blocks.materialize_reader_sources(
                template,
                source_plans,
                read_once,
                required_symbols=symbols_by_manuscript.get(manuscript, ()),
            )
        except reader_source_blocks.SourceBlockError as exc:
            raise ReaderManuscriptAuthoringError("SOURCE_RENDER_FAILED") from exc

    answers, answer_meta = render(answers_template, plans["answers.md"], "answers.md")
    _markdown(answers, "answers")
    if answer_mode == "embedded":
        if lesson_template.count(answers_template) != 1:
            raise ReaderManuscriptAuthoringError("ANSWERS_NOT_EMBEDDED")
        lesson_input = lesson_template.replace(answers_template, answers, 1)
    else:
        if lesson_template.count(answers_template):
            raise ReaderManuscriptAuthoringError("ANSWERS_DUPLICATE")
        lesson_input = lesson_template

    lesson, lesson_meta = render(lesson_input, plans["lesson.md"], "lesson.md")
    _markdown(lesson, "lesson")
    if answer_mode == "append":
        if lesson.count(answers):
            raise ReaderManuscriptAuthoringError("ANSWERS_DUPLICATE")
        chapter = lesson + b"\n\n" + answers
    else:
        chapter = lesson
    if chapter.count(answers) != 1 or not chapter.endswith(answers):
        raise ReaderManuscriptAuthoringError("ANSWERS_POSITION_INVALID")
    _markdown(chapter, "lesson")

    proofs = [
        {"manuscript": "lesson.md", **proof}
        for proof in _verify_blocks(chapter, lesson_meta.get("source_blocks"), cache)
    ]
    proofs.extend(
        {"manuscript": "answers.md", **proof}
        for proof in _verify_blocks(answers, answer_meta.get("source_blocks"), cache)
    )
    provenance = {
        "status": "LOCAL_RENDERED",
        "source_authentication": "NOT_AUTHENTICATED",
        "semantic_review": "NOT_REVIEWED",
        "answer_mode": answer_mode,
        "source_revision": revision,
        "chapter_sha256": _sha(chapter),
        "answers_sha256": _sha(answers),
        "source_blocks": proofs,
    }
    return ReaderManuscriptComposition(chapter, answers, provenance)


def _strict_json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _cache_reader(cache_root: Path) -> Callable[[str], bytes]:
    try:
        root = cache_root.resolve(strict=True)
    except OSError as exc:
        raise ReaderManuscriptAuthoringError("CACHE_ROOT_INVALID") from exc
    if not root.is_dir():
        raise ReaderManuscriptAuthoringError("CACHE_ROOT_INVALID")

    def read(path: str) -> bytes:
        try:
            resolved = root.joinpath(*path.split("/")).resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                raise OSError
            return resolved.read_bytes()
        except (OSError, ValueError) as exc:
            raise ReaderManuscriptAuthoringError("CACHE_PATH_ESCAPE") from exc

    return read


def _write_outputs(directory: Path, outputs: Mapping[str, bytes]) -> None:
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ReaderManuscriptAuthoringError("OUTPUT_INVALID")
    for name, raw in outputs.items():
        path = directory / name
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != raw)):
            raise ReaderManuscriptAuthoringError("OUTPUT_CONFLICT")
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in outputs.items():
        path = directory / name
        if path.exists():
            continue
        try:
            with path.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
                raise ReaderManuscriptAuthoringError("OUTPUT_CONFLICT")
        except OSError as exc:
            raise ReaderManuscriptAuthoringError("OUTPUT_FAILED") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Locally render a reader manuscript from a frozen cache.")
    parser.add_argument("--lesson-template", required=True, type=Path)
    parser.add_argument("--answers-template", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--answer-mode", choices=("append", "embedded"), default="append")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        lesson = args.lesson_template.read_bytes()
        answers = args.answers_template.read_bytes()
        sources = args.sources.read_bytes()
        manifest = _strict_json(sources)
        composition = compose_reader_manuscript(
            lesson, answers, manifest, _cache_reader(args.cache_root),
            answer_mode=args.answer_mode,
        )
        provenance = json.dumps(
            composition.provenance, ensure_ascii=False, sort_keys=True,
            indent=2, allow_nan=False,
        ).encode("utf-8") + b"\n"
        _write_outputs(args.output_dir, {
            "lesson.md": composition.chapter_bytes,
            "answers.md": composition.answers_bytes,
            "source-provenance.json": provenance,
            "sources.json": sources,
        })
        print(
            "status=LOCAL_RENDERED source_authentication=NOT_AUTHENTICATED "
            "semantic_review=NOT_REVIEWED "
            f"chapter_sha256={composition.provenance['chapter_sha256']}"
        )
        return 0
    except ReaderManuscriptAuthoringError as exc:
        print(f"error={exc.code}")
    except (OSError, UnicodeError, TypeError, ValueError):
        print("error=INPUT_INVALID")
    return 2


if __name__ == "__main__":
    sys.exit(main())

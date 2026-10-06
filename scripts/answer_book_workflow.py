#!/usr/bin/env python3
"""Prepare and build one Phase 6D1 answer book without invoking a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, dumps_artifact
from chapter_review_workflow import Phase6ReviewError, authenticate_chapter_review_package
from phase6_answer_book import (
    Phase6AnswerBookError,
    build_answer_bank,
    build_writer_material,
    render_answer_book,
    sha256,
)
from phase6_chapter import (
    ChapterInputPaths,
    Phase6ChapterError,
    _absolute,
    _check_external_output,
    _inside,
    _publish_new_file,
    _read_artifact,
    _read_review_file,
    _unlink_if_owned,
)


def _ensure_disjoint(output: Path, *inputs: Path) -> None:
    for input_path in inputs:
        source = _absolute(input_path)
        if output == source or _inside(output, source) or _inside(source, output):
            raise Phase6AnswerBookError("OUTPUT_INVALID")


def _output_path(
    out_dir: str | Path,
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    review_dir: Path,
    candidate_path: Path | None = None,
) -> Path:
    try:
        output = _check_external_output(out_dir, inputs, must_exist=False)
    except Phase6ChapterError:
        raise
    disjoint = [Path(chapter_dir), Path(review_dir)]
    if candidate_path is not None:
        disjoint.append(candidate_path)
    _ensure_disjoint(output, *disjoint)
    return output


def _rollback_bundle(output: Path, directory_owner: tuple[int, int] | None, created: list[tuple[Path, tuple[int, int]]]) -> None:
    for path, owner in reversed(created):
        _unlink_if_owned(path, owner)
    if directory_owner is not None:
        try:
            info = output.lstat()
            if (info.st_dev, info.st_ino) == directory_owner:
                output.rmdir()
        except OSError:
            pass


def _publish_bundle(output: Path, files: Mapping[str, bytes]) -> None:
    directory_owner: tuple[int, int] | None = None
    created: list[tuple[Path, tuple[int, int]]] = []
    try:
        output.mkdir()
        info = output.lstat()
        directory_owner = (info.st_dev, info.st_ino)
        for name, payload in files.items():
            path = output / name
            created.append((path, _publish_new_file(path, payload)))
    except FileExistsError as exc:
        _rollback_bundle(output, directory_owner, created)
        raise Phase6ChapterError("OUTPUT_EXISTS") from exc
    except Phase6ChapterError:
        _rollback_bundle(output, directory_owner, created)
        raise
    except OSError as exc:
        _rollback_bundle(output, directory_owner, created)
        raise Phase6ChapterError("OUTPUT_FAILED") from exc
    except BaseException:
        _rollback_bundle(output, directory_owner, created)
        raise


def prepare_answer_book(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: str | Path,
    review_dir: str | Path,
    writer_alias: str,
    out_dir: str | Path,
) -> dict[str, Any]:
    """Authenticate one reviewed chapter, then publish the writer packet and unfinished template."""
    chapter = _absolute(chapter_dir)
    review = _absolute(review_dir)
    output = _output_path(out_dir, inputs, chapter_dir=chapter, review_dir=review)
    package = authenticate_chapter_review_package(inputs, chapter_dir=chapter, review_dir=review)
    packet, template = build_writer_material(package, writer_alias)
    template_raw = dumps_artifact(template).encode("utf-8")
    packet_sha = sha256(packet)
    package.recheck()
    _output_path(out_dir, inputs, chapter_dir=chapter, review_dir=review)
    _publish_bundle(output, {
        "answer-writer-packet.md": packet,
        "answer-candidates-template.json": template_raw,
    })
    return {
        "command": "prepare",
        "writer_packet_sha256": packet_sha,
        "candidate_template_sha256": sha256(template_raw),
    }


def build_answer_book(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: str | Path,
    review_dir: str | Path,
    answer_candidates: str | Path,
    out_dir: str | Path,
) -> dict[str, Any]:
    """Authenticate inputs, validate exact Writer bytes, and publish the 1.1 bank and render."""
    chapter = _absolute(chapter_dir)
    review = _absolute(review_dir)
    candidate_path = _absolute(answer_candidates)
    output = _output_path(
        out_dir, inputs, chapter_dir=chapter, review_dir=review, candidate_path=candidate_path,
    )
    candidate_read = _read_artifact(candidate_path)
    package = authenticate_chapter_review_package(inputs, chapter_dir=chapter, review_dir=review)
    bank = build_answer_bank(package, candidate_read.artifact, candidate_read.raw)
    answer_book = render_answer_book(bank).encode("utf-8")
    bank_raw = dumps_artifact(bank).encode("utf-8")

    package.recheck()
    try:
        if _read_review_file(candidate_path) != candidate_read.raw:
            raise Phase6AnswerBookError("INPUT_CHANGED")
    except Phase6ChapterError:
        raise Phase6AnswerBookError("INPUT_CHANGED")
    _output_path(
        out_dir, inputs, chapter_dir=chapter, review_dir=review, candidate_path=candidate_path,
    )
    _publish_bundle(output, {"exercise-bank.json": bank_raw, "ANSWER-BOOK.md": answer_book})
    return {
        "command": "build",
        "schema_version": bank["schema_version"],
        "question_count": len(bank["records"]),
        "answer_book_status": bank["answer_book_status"],
        "answer_book_sha256": sha256(answer_book),
    }


def _paths_from_args(args: argparse.Namespace) -> ChapterInputPaths:
    return ChapterInputPaths(
        phase4c_package=Path(args.phase4c_package),
        claim_candidates_path=Path(args.claim_candidates),
        claim_evidence_path=Path(args.claim_evidence),
        claim_evidence_graph_path=Path(args.claim_evidence_graph),
        prerequisite_candidates_path=Path(args.prerequisite_candidates),
        prerequisite_graph_path=Path(args.prerequisite_graph),
        curriculum_candidates_path=Path(args.curriculum_candidates),
        curriculum_path=Path(args.curriculum),
        run_dir=Path(args.run_dir),
        root=Path(args.root),
    )


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--phase4c-package", required=True)
    parser.add_argument("--claim-candidates", required=True)
    parser.add_argument("--claim-evidence", required=True)
    parser.add_argument("--claim-evidence-graph", required=True)
    parser.add_argument("--prerequisite-candidates", required=True)
    parser.add_argument("--prerequisite-graph", required=True)
    parser.add_argument("--curriculum-candidates", required=True)
    parser.add_argument("--curriculum", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--chapter-dir", required=True)
    prepare.add_argument("--review-dir", required=True)
    prepare.add_argument("--writer-alias", required=True)
    prepare.add_argument("--out-dir", required=True)
    _add_inputs(prepare)
    build = subparsers.add_parser("build")
    build.add_argument("--chapter-dir", required=True)
    build.add_argument("--review-dir", required=True)
    build.add_argument("--answer-candidates", required=True)
    build.add_argument("--out-dir", required=True)
    _add_inputs(build)
    args = parser.parse_args(argv)
    try:
        inputs = _paths_from_args(args)
        if args.command == "prepare":
            result = prepare_answer_book(
                inputs,
                chapter_dir=Path(args.chapter_dir),
                review_dir=Path(args.review_dir),
                writer_alias=args.writer_alias,
                out_dir=Path(args.out_dir),
            )
        else:
            result = build_answer_book(
                inputs,
                chapter_dir=Path(args.chapter_dir),
                review_dir=Path(args.review_dir),
                answer_candidates=Path(args.answer_candidates),
                out_dir=Path(args.out_dir),
            )
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (Phase6AnswerBookError, Phase6ChapterError, Phase6ReviewError) as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        print(json.dumps({"error": code}, separators=(",", ":")))
        return 2
    except (ArtifactValidationError, OSError, ValueError, TypeError):
        print('{"error":"INPUT_INVALID"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

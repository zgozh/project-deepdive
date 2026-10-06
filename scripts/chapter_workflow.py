#!/usr/bin/env python3
"""Explicit Phase 6A prepare/verify CLI; never invokes a Writer or model."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase6_chapter import (
    ChapterInputPaths,
    Phase6ChapterError,
    markdown_format_preflight,
    prepare_chapter_facts,
    verify_chapter_draft,
)


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase4c-package", required=True, type=Path)
    parser.add_argument("--claim-candidates", required=True, type=Path)
    parser.add_argument("--claim-evidence", required=True, type=Path)
    parser.add_argument("--claim-evidence-graph", required=True, type=Path)
    parser.add_argument("--prerequisite-candidates", required=True, type=Path)
    parser.add_argument("--prerequisite-graph", required=True, type=Path)
    parser.add_argument("--curriculum-candidates", required=True, type=Path)
    parser.add_argument("--curriculum", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and mechanically verify one Phase 6A Markdown draft.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="check Markdown byte and footnote-tail format only")
    preflight.add_argument("--markdown", required=True, type=Path)
    prepare = subparsers.add_parser("prepare", help="authenticate inputs and write facts plus Writer packet")
    _add_inputs(prepare)
    verify = subparsers.add_parser("verify", help="check a Writer-created chapter without overwriting it")
    _add_inputs(verify)
    verify.add_argument("--facts", required=True, type=Path)
    verify.add_argument("--markdown", required=True, type=Path)
    return parser


def _inputs(args: argparse.Namespace) -> ChapterInputPaths:
    return ChapterInputPaths(
        phase4c_package=args.phase4c_package,
        claim_candidates_path=args.claim_candidates,
        claim_evidence_path=args.claim_evidence,
        claim_evidence_graph_path=args.claim_evidence_graph,
        prerequisite_candidates_path=args.prerequisite_candidates,
        prerequisite_graph_path=args.prerequisite_graph,
        curriculum_candidates_path=args.curriculum_candidates,
        curriculum_path=args.curriculum,
        run_dir=args.run_dir,
        root=args.root,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "preflight":
        try:
            raw = args.markdown.read_bytes()
        except OSError:
            print("format_preflight=FAIL authentication=NOT_RUN errors=INPUT_INVALID")
            return 2
        errors = markdown_format_preflight(raw)
        if errors:
            print(f"format_preflight=FAIL authentication=NOT_RUN errors={','.join(errors)}")
            return 1
        print("format_preflight=PASS authentication=NOT_RUN errors=NONE")
        return 0

    try:
        if args.command == "prepare":
            facts, _packet = prepare_chapter_facts(
                _inputs(args), unit_id=args.unit_id, out_dir=args.out_dir,
            )
            print(
                f"status=PREPARED chapter_id={facts['selected_unit']['chapter_id']} "
                f"claims={len(facts['claims'])} evidence={len(facts['evidence'])}"
            )
            return 0

        _bank, status = verify_chapter_draft(
            _inputs(args),
            unit_id=args.unit_id,
            facts_path=args.facts,
            markdown_path=args.markdown,
            out_dir=args.out_dir,
        )
        print(
            f"chapter_status={status['chapter_status']} "
            f"structural_status={status['structural_status']} "
            f"overall_status={status['overall_status']} errors={len(status['errors'])}"
        )
        return 1 if status["structural_status"] == "FAIL" else 0
    except Phase6ChapterError as exc:
        print(f"error={exc.code}")
        return 2
    except Exception:
        print("error=INPUT_INVALID")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

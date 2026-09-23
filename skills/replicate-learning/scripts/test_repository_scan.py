#!/usr/bin/env python3
"""Tests for the deterministic Git-backed repository inventory."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_contract import dumps_artifact, validate_artifact  # noqa: E402
from file_classification import CLASSIFICATION_POLICY_VERSION  # noqa: E402
from repository_scan import (  # noqa: E402
    RepositoryScanError,
    ScanOptions,
    analysis_prefix_between,
    discover_git_context,
    enumerate_indexed_files,
    load_coverage_overrides,
    scan_repository,
    split_nul,
)

SYMLINKS_SUPPORTED = hasattr(os, "symlink") and (os.name != "nt")
GITLINK_OBJECT_ID = "a" * 40
REPOSITORY_FIXTURE_ROOT = SKILL_ROOT / "tests" / "fixtures" / "repository_scanner"
FIXTURE_FAMILIES = ("python", "java", "frontend")


# ---------------------------------------------------------------------------
# Temporary Git repository helpers (used by every Phase 2 test module)
# ---------------------------------------------------------------------------

def run_git_bytes(repo, *args, input_bytes=None):
    """Run one Git command with an explicit argument array; never a shell."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        input=input_bytes,
        capture_output=True,
        check=False,
    )


def commit_all(repo, message="fixture"):
    run_git_bytes(repo, "add", "--all")
    result = run_git_bytes(repo, "commit", "--quiet", "--no-verify", "-m", message)
    if result.returncode != 0:
        raise AssertionError(f"git commit failed: {result.stderr.decode('utf-8', 'replace')}")
    return result


def init_git_repo(root: Path, files: dict[str, bytes]) -> str:
    """Create an isolated Git repository below ``root`` and return its full HEAD id."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for relative, data in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for args in (
        ("init", "--quiet"),
        ("config", "user.name", "Project DeepDive Test"),
        ("config", "user.email", "deepdive@example.invalid"),
        ("config", "core.autocrlf", "false"),
        ("config", "commit.gpgsign", "false"),
    ):
        result = run_git_bytes(root, *args)
        if result.returncode != 0:
            raise AssertionError(f"git {args[0]} failed: {result.stderr.decode('utf-8', 'replace')}")
    commit_all(root)
    return run_git_bytes(root, "rev-parse", "HEAD").stdout.decode("ascii").strip()


def create_symlink_or_skip(testcase, link: Path, target: Path | str, target_is_directory=False):
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        testcase.skipTest(f"creating symlinks is not permitted on this platform: {exc}")


def create_junction_or_skip(testcase, link: Path, target: Path):
    if os.name != "nt":
        testcase.skipTest("Windows junction/reparse-point case")

    def quote_powershell(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    command = (
        f"New-Item -ItemType Junction -Path {quote_powershell(link)} "
        f"-Target {quote_powershell(target)} -ErrorAction Stop | Out-Null"
    )
    failure = "PowerShell is unavailable"
    for executable in ("powershell.exe", "powershell", "pwsh"):
        try:
            result = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            failure = str(exc)
            continue
        if result.returncode == 0:
            return
        failure = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
    testcase.skipTest(f"cannot create a Windows junction on this host: {failure}")


def tracked_count(repo) -> int:
    """Count tracked paths with NUL delimiters so odd path bytes stay one path."""
    raw = run_git_bytes(repo, "ls-files", "-z").stdout
    return len([path for path in raw.split(b"\0") if path])


def build_fixture_repository(family: str, destination: Path) -> Path:
    """Copy one committed fixture template and initialize it as an isolated Git repository."""
    shutil.copytree(REPOSITORY_FIXTURE_ROOT / family, destination)
    init_git_repo(destination, {})
    return destination


class NulSafetyTests(unittest.TestCase):
    def test_nul_split_keeps_spaces_unicode_and_newlines_in_one_path(self):
        raw = b"plain.txt\0dir with space/a b.txt\0\xc3\xbcnicode/\xe6\x96\x87.txt\0weird/line\nbreak.txt\0"

        self.assertEqual(
            (
                b"plain.txt",
                b"dir with space/a b.txt",
                b"\xc3\xbcnicode/\xe6\x96\x87.txt",
                b"weird/line\nbreak.txt",
            ),
            split_nul(raw),
        )


class GitInventoryTests(unittest.TestCase):
    def test_root_repository_and_nested_analysis_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo, {
                "a/one.txt": b"one\n",
                "a/sub/two.txt": b"two\n",
                "outside.txt": b"out\n",
            })

            context = discover_git_context(repo / "a")
            files = enumerate_indexed_files(context, "worktree")

            self.assertEqual(repo.resolve(), Path(context.repository_root).resolve())
            self.assertEqual("a", context.analysis_prefix)
            self.assertEqual(revision, context.revision)
            self.assertEqual(("one.txt", "sub/two.txt"), tuple(item.path for item in files))

    def test_untracked_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"tracked.txt": b"tracked\n"})
            (repo / "untracked.txt").write_bytes(b"untracked\n")

            files = enumerate_indexed_files(discover_git_context(repo), "worktree")

            self.assertEqual(("tracked.txt",), tuple(item.path for item in files))

    def test_paths_with_spaces_and_unicode_survive_enumeration(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {
                "dir with space/a b.txt": b"space\n",
                "unicode/\u00fcnicode-\u6587\u4ef6.txt": b"unicode\n",
            })

            files = enumerate_indexed_files(discover_git_context(repo), "worktree")

            self.assertEqual(("dir with space/a b.txt", "unicode/\u00fcnicode-\u6587\u4ef6.txt"),
                             tuple(item.path for item in files))
            self.assertEqual(2, tracked_count(repo))

    @unittest.skipIf(os.name == "nt", "Windows forbids newline characters in path names")
    def test_newline_in_a_tracked_path_is_one_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"weird/line\nbreak.txt": b"newline\n"})

            files = enumerate_indexed_files(discover_git_context(repo), "worktree")
            raw = run_git_bytes(repo, "ls-files", "-z").stdout

            self.assertEqual(("weird/line\nbreak.txt",), tuple(item.path for item in files))
            self.assertEqual(1, len([path for path in raw.split(b"\0") if path]))

    def test_artifact_paths_are_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {
                "zeta.txt": b"z\n",
                "alpha/beta.txt": b"b\n",
                "Alpha.txt": b"a\n",
                "beta.txt": b"b\n",
            })

            files = enumerate_indexed_files(discover_git_context(repo), "worktree")
            paths = [item.path for item in files]

            self.assertEqual(sorted(paths), paths)

    def test_revision_is_the_full_commit_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            revision = init_git_repo(repo, {"one.txt": b"1\n"})

            context = discover_git_context(repo)

            self.assertEqual(40, len(context.revision))
            self.assertEqual(revision, context.revision)

    def test_dirty_tracks_only_changes_below_the_analysis_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"inside/one.txt": b"1\n", "outside.txt": b"2\n"})

            clean = discover_git_context(repo / "inside")
            self.assertFalse(clean.dirty)

            (repo / "outside.txt").write_bytes(b"changed\n")
            self.assertFalse(discover_git_context(repo / "inside").dirty)
            self.assertTrue(discover_git_context(repo).dirty)

            (repo / "inside" / "one.txt").write_bytes(b"changed\n")
            self.assertTrue(discover_git_context(repo / "inside").dirty)

    def test_untracked_files_do_not_make_the_project_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"tracked.txt": b"1\n"})
            (repo / "untracked.txt").write_bytes(b"2\n")

            self.assertFalse(discover_git_context(repo).dirty)

    def test_non_git_root_fails_with_an_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "not-a-repo"
            plain.mkdir()
            (plain / "file.txt").write_bytes(b"x\n")

            with self.assertRaisesRegex(RepositoryScanError, "Git"):
                discover_git_context(plain)

    def test_repository_without_head_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "empty"
            repo.mkdir()
            run_git_bytes(repo, "init", "--quiet")

            with self.assertRaisesRegex(RepositoryScanError, "HEAD"):
                discover_git_context(repo)

    def test_analysis_root_must_be_inside_the_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            with self.assertRaisesRegex(RepositoryScanError, "analysis root"):
                analysis_prefix_between(repo, Path(tmp) / "elsewhere")
            self.assertEqual("", analysis_prefix_between(repo, repo))
            self.assertEqual("a/b", analysis_prefix_between(repo, repo / "a" / "b"))

    def test_unresolved_merge_stages_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"c.txt": b"base\n"})
            base_branch = run_git_bytes(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()

            run_git_bytes(repo, "checkout", "--quiet", "-b", "side")
            (repo / "c.txt").write_bytes(b"side\n")
            commit_all(repo, "side")
            run_git_bytes(repo, "checkout", "--quiet", base_branch)
            (repo / "c.txt").write_bytes(b"main\n")
            commit_all(repo, "main")
            merge = run_git_bytes(repo, "merge", "--no-edit", "side")
            self.assertNotEqual(0, merge.returncode, "fixture must actually conflict")

            context = discover_git_context(repo)
            with self.assertRaisesRegex(RepositoryScanError, "stage"):
                enumerate_indexed_files(context, "worktree")

    @unittest.skipIf(os.name == "nt", "Windows cannot create non-UTF-8 file names")
    def test_invalid_utf8_tracked_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            raw_name = os.path.join(os.fsencode(repo), b"bad-\xff-name.txt")
            with open(raw_name, "wb") as handle:
                handle.write(b"x\n")
            for args in (
                ("init", "--quiet"),
                ("config", "user.name", "Project DeepDive Test"),
                ("config", "user.email", "deepdive@example.invalid"),
                ("config", "core.autocrlf", "false"),
            ):
                run_git_bytes(repo, *args)
            commit_all(repo)

            context = discover_git_context(repo)
            with self.assertRaisesRegex(RepositoryScanError, "UTF-8"):
                enumerate_indexed_files(context, "worktree")

    def test_unknown_snapshot_kind_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"one.txt": b"1\n"})

            with self.assertRaisesRegex(RepositoryScanError, "snapshot"):
                enumerate_indexed_files(discover_git_context(repo), "magic")


class SnapshotContentTests(unittest.TestCase):
    def test_worktree_snapshot_hashes_current_bytes_and_reports_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"one.txt": b"original\n"})
            (repo / "one.txt").write_bytes(b"edited\n")

            context = discover_git_context(repo)
            files = enumerate_indexed_files(context, "worktree")

            self.assertTrue(context.dirty)
            self.assertEqual(7, files[0].byte_count)
            self.assertEqual(hashlib.sha256(b"edited\n").hexdigest(), files[0].sha256)

    def test_git_tree_snapshot_keeps_committed_bytes_after_a_local_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"one.txt": b"original\n"})
            (repo / "one.txt").write_bytes(b"edited\n")

            context = discover_git_context(repo)
            files = enumerate_indexed_files(context, "git-tree")

            self.assertTrue(context.dirty, "dirty is still reported for tracked local edits")
            self.assertEqual(9, files[0].byte_count)
            self.assertEqual(hashlib.sha256(b"original\n").hexdigest(), files[0].sha256)
            self.assertNotEqual(
                run_git_bytes(repo, "hash-object", "one.txt").stdout.decode().strip(),
                files[0].vcs_object_id,
            )

    def test_worktree_metadata_records_the_index_object_id_not_the_edited_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"one.txt": b"original\n"})
            index_object_id = run_git_bytes(repo, "hash-object", "one.txt").stdout.decode().strip()
            (repo / "one.txt").write_bytes(b"edited\n")

            files = enumerate_indexed_files(discover_git_context(repo), "worktree")

            self.assertEqual(index_object_id, files[0].vcs_object_id)
            self.assertNotEqual(hashlib.sha256(b"edited\n").hexdigest(), index_object_id)

    def test_deleted_tracked_file_fails_in_worktree_mode_and_suggests_git_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"one.txt": b"1\n"})
            (repo / "one.txt").unlink()

            with self.assertRaisesRegex(RepositoryScanError, "git-tree"):
                enumerate_indexed_files(discover_git_context(repo), "worktree")

    def test_fixed_content_yields_expected_size_hash_and_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            payload = "print('hi')\n".encode("utf-8")
            init_git_repo(repo, {"src/app.py": payload})

            item = enumerate_indexed_files(discover_git_context(repo), "worktree")[0]

            self.assertEqual("src/app.py", item.path)
            self.assertEqual(len(payload), item.byte_count)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), item.sha256)
            self.assertEqual(("text", "text/x-python", "py"), (item.content_kind, item.media_type, item.extension))

    @unittest.skipUnless(SYMLINKS_SUPPORTED, "creating symlinks is not permitted on this platform")
    def test_symlink_payload_is_hashed_instead_of_the_target_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "real.txt").write_bytes(b"real content\n")
            os.symlink("real.txt", repo / "link.txt")
            for args in (
                ("init", "--quiet"),
                ("config", "user.name", "Project DeepDive Test"),
                ("config", "user.email", "deepdive@example.invalid"),
                ("config", "core.autocrlf", "false"),
            ):
                run_git_bytes(repo, *args)
            commit_all(repo)

            files = {item.path: item for item in enumerate_indexed_files(discover_git_context(repo), "worktree")}

            self.assertEqual("symlink", files["link.txt"].content_kind)
            self.assertEqual(len(b"real.txt"), files["link.txt"].byte_count)
            self.assertEqual(hashlib.sha256(b"real.txt").hexdigest(), files["link.txt"].sha256)

            from coverage_audit import audit_coverage

            artifacts = scan_repository(ScanOptions(
                root=repo,
                snapshot_kind="worktree",
                generated_at="2026-09-22T00:00:00Z",
            ))
            audited = audit_coverage(artifacts.project_index, artifacts.coverage, repo)
            self.assertEqual("PASS", audited.status, audited.violations)

    def test_regular_worktree_paths_reject_symlink_or_junction_escapes(self):
        from coverage_audit import audit_coverage

        if os.name == "nt":
            escape_kinds = ("junction-ancestor",)
        else:
            escape_kinds = ("final-symlink", "ancestor-symlink")

        for escape_kind in escape_kinds:
            with self.subTest(escape_kind=escape_kind), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                repo = base / "repo"
                init_git_repo(repo, {"src/regular.py": b"inside\n"})
                artifacts = scan_repository(ScanOptions(
                    root=repo,
                    snapshot_kind="worktree",
                    generated_at="2026-09-22T00:00:00Z",
                ))
                outside = base / "outside"
                outside.mkdir()
                (outside / "regular.py").write_bytes(b"external payload\n")

                if escape_kind == "final-symlink":
                    tracked = repo / "src" / "regular.py"
                    tracked.unlink()
                    create_symlink_or_skip(self, tracked, outside / "regular.py")
                else:
                    tracked_parent = repo / "src"
                    shutil.rmtree(tracked_parent)
                    if escape_kind == "junction-ancestor":
                        create_junction_or_skip(self, tracked_parent, outside)
                        self.addCleanup(
                            lambda path=tracked_parent: os.rmdir(path)
                            if os.path.lexists(path) else None
                        )
                    else:
                        create_symlink_or_skip(
                            self, tracked_parent, outside, target_is_directory=True,
                        )

                with self.assertRaisesRegex(RepositoryScanError, "symlink|junction|reparse|outside"):
                    scan_repository(ScanOptions(
                        root=repo,
                        snapshot_kind="worktree",
                        generated_at="2026-09-22T00:00:00Z",
                    ))

                result = audit_coverage(artifacts.project_index, artifacts.coverage, repo)
                self.assertEqual("FAIL", result.status)
                self.assertEqual({"SNAPSHOT_PATH_UNSAFE"}, {item.code for item in result.violations})

    def test_gitlink_payload_hashes_the_object_id_and_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"one.txt": b"1\n"})
            result = run_git_bytes(
                repo, "update-index", "--add", "--cacheinfo", f"160000,{GITLINK_OBJECT_ID},vendor/lib",
            )
            self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", "replace"))
            committed = run_git_bytes(repo, "commit", "--quiet", "--no-verify", "-m", "add gitlink")
            self.assertEqual(0, committed.returncode,
                             (committed.stdout + committed.stderr).decode("utf-8", "replace"))

            for snapshot_kind in ("worktree", "git-tree"):
                with self.subTest(snapshot_kind=snapshot_kind):
                    files = {item.path: item for item in enumerate_indexed_files(
                        discover_git_context(repo), snapshot_kind)}
                    gitlink = files["vendor/lib"]

                    self.assertEqual("gitlink", gitlink.content_kind)
                    self.assertEqual("application/x-gitlink", gitlink.media_type)
                    self.assertEqual(GITLINK_OBJECT_ID, gitlink.vcs_object_id)
                    self.assertEqual(len(GITLINK_OBJECT_ID), gitlink.byte_count)
                    self.assertEqual(hashlib.sha256(GITLINK_OBJECT_ID.encode("ascii")).hexdigest(),
                                     gitlink.sha256)
                    self.assertEqual("", gitlink.extension)

    def test_git_tree_snapshot_reads_objects_from_the_declared_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            first = init_git_repo(repo, {"one.txt": b"first\n"})
            (repo / "one.txt").write_bytes(b"second\n")
            commit_all(repo, "second")

            head = discover_git_context(repo)
            item = enumerate_indexed_files(head, "git-tree")[0]
            self.assertEqual(hashlib.sha256(b"second\n").hexdigest(), item.sha256)
            self.assertNotEqual(first, head.revision)


class CoverageOverrideTests(unittest.TestCase):
    """Unused, duplicate, escaping or malformed overrides must fail closed."""

    TRACKED = ("mystery.odd", "src/app.py")
    FIXED_TIME = "2026-09-22T00:00:00Z"

    def write(self, tmp, payload: bytes) -> Path:
        path = Path(tmp) / "overrides.json"
        path.write_bytes(payload)
        return path

    def document(self, entries, **overrides):
        body = {"schema_version": "1.0.0", "entries": entries}
        body.update(overrides)
        return json.dumps(body).encode("utf-8")

    def test_valid_exact_path_override_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, self.document({
                "mystery.odd": {
                    "surface": "tooling",
                    "classification": "CLASSIFIED",
                    "reason": "Repository-owned generator input documented by the project.",
                },
            }))

            overrides = load_coverage_overrides(path, self.TRACKED)

            self.assertEqual({"mystery.odd"}, set(overrides))
            self.assertEqual("tooling", overrides["mystery.odd"].surface)
            self.assertEqual("CLASSIFIED", overrides["mystery.odd"].classification)

    def test_duplicate_json_keys_are_rejected(self):
        payload = (
            b'{"schema_version": "1.0.0", "entries": {'
            b'"mystery.odd": {"surface": "tooling", "classification": "CLASSIFIED", "reason": "a"},'
            b'"mystery.odd": {"surface": "tooling", "classification": "CLASSIFIED", "reason": "b"}}}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RepositoryScanError, "duplicate"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_invalid_utf8_is_rejected(self):
        payload = b'{"schema_version": "1.0.0", "entries": {"\xff\xfe": {}}}'
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RepositoryScanError, "UTF-8"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_unsupported_schema_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.document({}, schema_version="2.0.0")
            with self.assertRaisesRegex(RepositoryScanError, "schema_version"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_unknown_top_level_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.document({}, extra=True)
            with self.assertRaisesRegex(RepositoryScanError, "extra"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_entries_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.document([])
            with self.assertRaisesRegex(RepositoryScanError, "entries"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_absolute_and_escaping_paths_are_rejected(self):
        for candidate in ("/etc/passwd", "../escape.txt", "src/../../escape.txt", "src\\app.py"):
            with self.subTest(path=candidate):
                with tempfile.TemporaryDirectory() as tmp:
                    payload = self.document({
                        candidate: {
                            "surface": "tooling",
                            "classification": "CLASSIFIED",
                            "reason": "Trying to escape the analysis root.",
                        },
                    })
                    with self.assertRaisesRegex(RepositoryScanError, "path"):
                        load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_untracked_override_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.document({
                "not/tracked.txt": {
                    "surface": "tooling",
                    "classification": "CLASSIFIED",
                    "reason": "This path is not in the tracked set.",
                },
            })
            with self.assertRaisesRegex(RepositoryScanError, "tracked"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_empty_reason_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.document({
                "mystery.odd": {
                    "surface": "tooling",
                    "classification": "CLASSIFIED",
                    "reason": "   ",
                },
            })
            with self.assertRaisesRegex(RepositoryScanError, "reason"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_forbidden_classifications_are_rejected(self):
        for classification in ("COVERED", "UNKNOWN"):
            with self.subTest(classification=classification):
                with tempfile.TemporaryDirectory() as tmp:
                    payload = self.document({
                        "mystery.odd": {
                            "surface": "tooling",
                            "classification": classification,
                            "reason": "Trying to bypass the audit.",
                        },
                    })
                    with self.assertRaisesRegex(RepositoryScanError, classification):
                        load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_entry_missing_a_required_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.document({"mystery.odd": {"surface": "tooling", "classification": "CLASSIFIED"}})
            with self.assertRaisesRegex(RepositoryScanError, "reason"):
                load_coverage_overrides(self.write(tmp, payload), self.TRACKED)

    def test_missing_file_and_invalid_json_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RepositoryScanError, "override"):
                load_coverage_overrides(Path(tmp) / "absent.json", self.TRACKED)
            with self.assertRaisesRegex(RepositoryScanError, "JSON"):
                load_coverage_overrides(self.write(tmp, b"{not json"), self.TRACKED)


class ArtifactAssemblyTests(unittest.TestCase):
    FIXED_TIME = "2026-09-22T00:00:00Z"

    def test_artifacts_use_the_v1_1_scanner_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "sample-repo"
            revision = init_git_repo(repo, {
                "src/app.py": b"print('hi')\n",
                "mystery.odd": b"who knows\n",
            })

            result = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                generated_at=self.FIXED_TIME))
            project_index = result.project_index
            coverage = result.coverage

            self.assertEqual("project-index", project_index["artifact_kind"])
            self.assertEqual("1.1.0", project_index["schema_version"])
            self.assertEqual(".", project_index["project"]["root"])
            self.assertEqual("sample-repo", project_index["project"]["name"])
            self.assertEqual("git", project_index["project"]["vcs"])
            self.assertEqual("worktree", project_index["project"]["snapshot_kind"])
            self.assertFalse(project_index["project"]["dirty"])
            self.assertEqual(revision, project_index["repository_revision"])
            self.assertEqual(self.FIXED_TIME, project_index["generated_at"])
            self.assertEqual(2, project_index["file_count"])
            self.assertEqual(len(project_index["files"]), project_index["file_count"])

            self.assertEqual("coverage", coverage["artifact_kind"])
            self.assertEqual("1.1.0", coverage["schema_version"])
            self.assertEqual(CLASSIFICATION_POLICY_VERSION, coverage["classification_policy_version"])
            self.assertEqual(revision, coverage["repository_revision"])
            self.assertEqual(self.FIXED_TIME, coverage["generated_at"])
            self.assertEqual(len(project_index["files"]), coverage["tracked_file_count"])
            self.assertEqual(
                sum(entry["classification"] == "UNKNOWN" for entry in coverage["entries"]),
                coverage["unknown_count"],
            )
            self.assertEqual(
                [entry["path"] for entry in project_index["files"]],
                [entry["path"] for entry in coverage["entries"]],
            )
            self.assertIs(project_index, validate_artifact(project_index))
            self.assertIs(coverage, validate_artifact(coverage))

    def test_every_file_entry_carries_the_v1_1_typing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"src/app.py": b"print('hi')\n", "docs/readme.md": b"# hi\n"})

            result = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                generated_at=self.FIXED_TIME))
            by_path = {entry["path"]: entry for entry in result.project_index["files"]}

            self.assertEqual(
                {"path", "bytes", "sha256", "tracked", "content_kind", "media_type", "extension",
                 "vcs_object_id"},
                set(by_path["src/app.py"]),
            )
            self.assertEqual("text/x-python", by_path["src/app.py"]["media_type"])
            self.assertEqual("py", by_path["src/app.py"]["extension"])
            self.assertEqual(40, len(by_path["src/app.py"]["vcs_object_id"]))
            self.assertTrue(all(entry["tracked"] is True for entry in result.project_index["files"]))

    def test_deliberate_unknown_is_reported_and_then_resolved_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"src/app.py": b"print('hi')\n", "mystery.odd": b"who knows\n"})
            overrides = Path(tmp) / "overrides.json"
            overrides.write_text(json.dumps({
                "schema_version": "1.0.0",
                "entries": {
                    "mystery.odd": {
                        "surface": "tooling",
                        "classification": "CLASSIFIED",
                        "reason": "Repository-owned generator input documented by the project.",
                    },
                },
            }), encoding="utf-8")

            unresolved = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                    generated_at=self.FIXED_TIME))
            resolved = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                  generated_at=self.FIXED_TIME,
                                                  overrides_path=overrides))
            unresolved_entries = {entry["path"]: entry for entry in unresolved.coverage["entries"]}
            resolved_entries = {entry["path"]: entry for entry in resolved.coverage["entries"]}

            self.assertEqual(1, unresolved.coverage["unknown_count"])
            self.assertEqual("UNKNOWN", unresolved_entries["mystery.odd"]["classification"])
            self.assertEqual("fallback:unknown", unresolved_entries["mystery.odd"]["rule_id"])

            self.assertEqual(0, resolved.coverage["unknown_count"])
            self.assertEqual("CLASSIFIED", resolved_entries["mystery.odd"]["classification"])
            self.assertEqual("override:exact-path", resolved_entries["mystery.odd"]["rule_id"])
            self.assertEqual(
                "Repository-owned generator input documented by the project.",
                resolved_entries["mystery.odd"]["reason"],
            )
            self.assertEqual("extension:known", resolved_entries["src/app.py"]["rule_id"])
            self.assertEqual(
                unresolved_entries["src/app.py"],
                resolved_entries["src/app.py"],
                "the override must not change any other entry",
            )

    def test_repeated_fixed_time_scan_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"src/app.py": b"print('hi')\n", "mystery.odd": b"who knows\n"})

            first = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                               generated_at=self.FIXED_TIME))
            second = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                                generated_at=self.FIXED_TIME))

            self.assertEqual(dumps_artifact(first.project_index), dumps_artifact(second.project_index))
            self.assertEqual(dumps_artifact(first.coverage), dumps_artifact(second.coverage))

    def test_nested_analysis_root_reports_dot_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"inner/src/app.py": b"print('hi')\n", "outer.txt": b"x\n"})

            result = scan_repository(ScanOptions(root=repo / "inner", snapshot_kind="git-tree",
                                                generated_at=self.FIXED_TIME))

            self.assertEqual((".", "inner"), (result.project_index["project"]["root"],
                                              result.project_index["project"]["name"]))
            self.assertEqual(("src/app.py",), tuple(entry["path"] for entry in result.project_index["files"]))

    def test_invalid_generated_at_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init_git_repo(repo, {"one.txt": b"1\n"})

            with self.assertRaisesRegex(RepositoryScanError, "generated-at"):
                scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                            generated_at="2026-09-22 00:00:00"))


class FixtureFamilyTests(unittest.TestCase):
    """Python-style, Java-style and frontend-containing fixture repositories."""

    EXPECTED_SURFACES = {
        "backend", "frontend", "database", "migration", "test", "configuration",
        "infrastructure", "build", "script", "ci", "documentation", "asset", "lockfile",
        "generated", "vendor",
    }
    PER_FAMILY_SURFACES = {
        "python": {"build", "test", "script", "ci", "infrastructure", "documentation"},
        "java": {"build", "backend", "test", "configuration", "migration", "database",
                 "tooling", "generated"},
        "frontend": {"build", "lockfile", "frontend", "api", "test", "tooling", "asset",
                     "generated", "vendor"},
    }

    def build(self, family):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return build_fixture_repository(family, Path(temporary.name) / family)

    def all_surfaces(self, family):
        repo = self.build(family)
        result = scan_repository(ScanOptions(root=repo, snapshot_kind="worktree",
                                             generated_at=ArtifactAssemblyTests.FIXED_TIME))
        surfaces = set()
        for entry in result.coverage["entries"]:
            surfaces.add(entry["surface"])
            surfaces.update(entry["secondary_surfaces"])
        return repo, result, surfaces

    def test_git_tracked_count_equals_both_artifact_sets(self):
        for family in FIXTURE_FAMILIES:
            for snapshot_kind in ("worktree", "git-tree"):
                with self.subTest(family=family, snapshot=snapshot_kind):
                    repo = self.build(family)
                    git_count = tracked_count(repo)

                    result = scan_repository(ScanOptions(root=repo, snapshot_kind=snapshot_kind,
                                                        generated_at=ArtifactAssemblyTests.FIXED_TIME))
                    index_paths = [entry["path"] for entry in result.project_index["files"]]
                    coverage_paths = [entry["path"] for entry in result.coverage["entries"]]

                    self.assertGreater(git_count, 0)
                    self.assertEqual(git_count, result.project_index["file_count"])
                    self.assertEqual(git_count, result.coverage["tracked_file_count"])
                    self.assertEqual(git_count, len(set(index_paths)))
                    self.assertEqual(git_count, len(set(coverage_paths)))
                    self.assertEqual(set(index_paths), set(coverage_paths))

    def test_families_collectively_expose_the_required_surfaces(self):
        collected = set()
        for family in FIXTURE_FAMILIES:
            _repo, _result, surfaces = self.all_surfaces(family)
            self.assertLessEqual(self.PER_FAMILY_SURFACES[family], surfaces, family)
            collected |= surfaces

        self.assertLessEqual(self.EXPECTED_SURFACES, collected)
        self.assertNotIn(
            "fixture", collected,
            "the copied fixture templates contain no fixture path; `fixture` remains the host role "
            "of skills/replicate-learning/tests/fixtures/ while the target files exercise the rest",
        )

    def test_only_the_frontend_fixture_has_a_deliberate_unknown(self):
        for family in FIXTURE_FAMILIES:
            with self.subTest(family=family):
                _repo, result, _surfaces = self.all_surfaces(family)
                unknown = sorted(entry["path"] for entry in result.coverage["entries"]
                                 if entry["classification"] == "UNKNOWN")

                self.assertEqual(["mystery.odd"] if family == "frontend" else [], unknown)

    def test_generated_vendor_and_lockfile_entries_carry_reasons(self):
        for family in FIXTURE_FAMILIES:
            with self.subTest(family=family):
                _repo, result, _surfaces = self.all_surfaces(family)
                for entry in result.coverage["entries"]:
                    if entry["classification"] in ("GENERATED", "VENDOR", "IGNORED_WITH_REASON"):
                        self.assertTrue(entry["reason"].strip(), entry["path"])

    def test_phase_two_never_emits_covered(self):
        for family in FIXTURE_FAMILIES:
            with self.subTest(family=family):
                _repo, result, _surfaces = self.all_surfaces(family)
                self.assertNotIn("COVERED", {entry["classification"] for entry in result.coverage["entries"]})


if __name__ == "__main__":
    unittest.main()

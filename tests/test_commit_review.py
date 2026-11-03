"""Behavior tests use only disposable repositories; no external service is contacted."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "commit-reviewer" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import commit_review as cli
from git_evidence import Git, inspect, issues, verify


SELF = {"GIT_AUTHOR_NAME": "Developer", "GIT_AUTHOR_EMAIL": "dev@example.com",
        "GIT_COMMITTER_NAME": "Developer", "GIT_COMMITTER_EMAIL": "dev@example.com"}
OTHER = {"GIT_AUTHOR_NAME": "Other", "GIT_AUTHOR_EMAIL": "other@example.com",
         "GIT_COMMITTER_NAME": "Other", "GIT_COMMITTER_EMAIL": "other@example.com"}


def git(repo, *args, identity=None):
    env = {**os.environ, **SELF, **(identity or {}), "GIT_TERMINAL_PROMPT": "0"}
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    result = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, shell=False, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8").strip()


def configure(repo):
    git(repo, "config", "user.name", "Developer")
    git(repo, "config", "user.email", "dev@example.com")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "commit.gpgsign", "false")


def commit_file(repo, name="feature.txt", text="feature\n", message="feat: add feature\n\nRefs: #123", identity=None):
    (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", message, identity=identity)
    return git(repo, "rev-parse", "HEAD")


class Repositories(unittest.TestCase):
    def setUp(self):
        self.runtime = Path(__file__).resolve().parents[1] / ".test-runs"
        self.runtime.mkdir(exist_ok=True)
        self.root = self.runtime / ("run-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.cleanup)
        self.seed = self.root / "seed"
        self.seed.mkdir()
        git(self.seed, "init", "--initial-branch=main")
        configure(self.seed)
        (self.seed / "shared.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        (self.seed / "image.bin").write_bytes(b"\x00\x01")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "feat: baseline\n\nRefs: #1", identity=OTHER)
        self.base = git(self.seed, "rev-parse", "HEAD")
        self.remote = self.root / "remote.git"
        git(self.root, "clone", "--bare", str(self.seed), str(self.remote))
        git(self.seed, "remote", "add", "origin", str(self.remote))
        self.work = self.root / "工作 Repo"
        git(self.root, "clone", str(self.remote), str(self.work))
        configure(self.work)
        git(self.work, "switch", "-c", "feature/test")
        # Keep tests independent of any environment-level author override.
        self.environ = patch.dict(os.environ, SELF)
        self.environ.start()

    def tearDown(self):
        self.environ.stop()

    def cleanup(self):
        def writable_then_retry(function, path, exc):
            os.chmod(path, 0o700)
            for attempt in range(7):
                try:
                    function(path)
                    return
                except PermissionError:
                    if attempt == 6:
                        raise
                    time.sleep(0.05 * (2 ** attempt))
        # Git object files can be read-only on Windows.
        resolved = self.root.resolve()
        if resolved.parent != self.runtime.resolve() or not resolved.name.startswith("run-"):
            raise AssertionError("Refusing cleanup outside the test runtime root")
        shutil.rmtree(resolved, onerror=writable_then_retry)

    def facts(self, mode="mr", **kwargs):
        return inspect(self.work, mode, **kwargs)

    def check_status(self, facts, key):
        return next(item["status"] for item in facts["checks"] if item["id"] == key)

    def advance_main(self):
        sha = commit_file(self.seed, "upstream.txt", "upstream\n", identity=OTHER)
        git(self.seed, "push", "origin", "main")
        return sha

    def save(self, facts, diff, name="evidence"):
        folder = self.root / name
        folder.mkdir()
        cli.write_json(folder / "snapshot.json", facts)
        (folder / "changes.patch").write_bytes(diff)
        analysis = dict(snapshot_id=facts["snapshot_id"], summary="新增 feature.txt 功能內容。",
                        diff_reviewed=True, commit_message="feat: 新增功能\n\nRefs: #123",
                        confirmations=[])
        (folder / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
        return folder, analysis

    def test_linear_current_feature_and_report_full_hash(self):
        head = commit_file(self.work)
        facts, diff = self.facts()
        self.assertFalse(facts["gaps"])
        self.assertFalse(facts["manual_items"])
        self.assertTrue(all(c["status"] == "pass" for c in facts["checks"]))
        self.assertEqual([head], [c["sha"] for c in facts["commits"]])
        self.assertEqual(self.base, facts["target_sha"])
        folder, _ = self.save(facts, diff)
        result = cli.publish(folder / "snapshot.json")
        self.assertTrue(result["ready"])
        report = Path(result["report"]).read_text(encoding="utf-8")
        self.assertIn(head, report)
        self.assertIn(self.base, report)
        self.assertIn("#123", report)
        self.assertEqual(self.work / "review-reports", Path(result["report"]).parent)

    def test_behind_latest_target_fails(self):
        commit_file(self.work)
        tip = self.advance_main()
        facts, _ = self.facts()
        self.assertEqual(tip, facts["target_sha"])
        self.assertEqual("fail", self.check_status(facts, "latest_target"))

    def test_merge_target_is_current_but_not_linear(self):
        commit_file(self.work)
        self.advance_main()
        git(self.work, "fetch", "origin")
        git(self.work, "merge", "--no-ff", "origin/main", "-m", "Merge main\n\nRefs: #123")
        facts, _ = self.facts()
        self.assertEqual("pass", self.check_status(facts, "latest_target"))
        self.assertEqual("fail", self.check_status(facts, "linear_history"))

    def test_on_main_and_no_new_commits_fail(self):
        git(self.work, "switch", "main")
        facts, _ = self.facts()
        self.assertEqual("fail", self.check_status(facts, "feature_branch"))
        self.assertEqual("fail", self.check_status(facts, "has_commits"))

    def test_detached_head_fails(self):
        commit_file(self.work)
        git(self.work, "switch", "--detach")
        facts, _ = self.facts()
        self.assertEqual("fail", self.check_status(facts, "attached_branch"))

    def test_one_missing_issue_in_multiple_commits_fails(self):
        missing = commit_file(self.work, message="feat: missing reference")
        commit_file(self.work, "second.txt")
        facts, _ = self.facts()
        self.assertEqual("fail", self.check_status(facts, "issue_references"))
        self.assertIn(missing, next(c["detail"] for c in facts["checks"] if c["id"] == "issue_references"))

    def test_other_author_is_not_hidden_by_self_committer(self):
        author = dict(SELF, GIT_AUTHOR_NAME="Other", GIT_AUTHOR_EMAIL="other@example.com")
        head = commit_file(self.work, identity=author)
        facts, _ = self.facts()
        self.assertEqual([head], [m["sha"] for m in facts["manual_items"] if m["kind"] == "commit"])
        self.assertEqual("dev@example.com", facts["commits"][0]["committer_email"])

    def test_mailmap_and_explicit_aliases_identify_self(self):
        commit_file(self.work, ".mailmap", "Developer <dev@example.com> Alias <alias@example.com>\n")
        alias = dict(SELF, GIT_AUTHOR_NAME="Alias", GIT_AUTHOR_EMAIL="alias@example.com")
        commit_file(self.work, identity=alias)
        facts, _ = self.facts()
        self.assertFalse(facts["manual_items"])
        alias2 = dict(SELF, GIT_AUTHOR_EMAIL="second@example.com")
        commit_file(self.work, "second.txt", identity=alias2)
        facts, _ = self.facts(self_emails=["second@example.com"])
        self.assertFalse(facts["manual_items"])

    def test_modified_and_deleted_lines_use_left_side_author(self):
        commit_file(self.work, "shared.txt", "ALPHA\ngamma\n")
        facts, _ = self.facts()
        rows = [m for m in facts["manual_items"] if m["kind"] == "lines"]
        self.assertTrue(rows)
        self.assertTrue(all(m["origin_sha"] == self.base for m in rows))
        affected = {line for m in rows for line in range(m["old_start"], m["old_end"] + 1)}
        self.assertEqual({1, 2}, affected)

    def test_deleted_file_is_attributed(self):
        git(self.work, "rm", "shared.txt")
        git(self.work, "commit", "-m", "refactor: remove shared\n\nRefs: #123")
        facts, _ = self.facts()
        rows = [m for m in facts["manual_items"] if m["kind"] == "lines"]
        self.assertEqual([(1, 3)], [(m["old_start"], m["old_end"]) for m in rows])

    def test_pure_rename_does_not_claim_other_lines_were_edited(self):
        git(self.work, "mv", "shared.txt", "改名 file.txt")
        git(self.work, "commit", "-m", "refactor: rename\n\nRefs: #123")
        facts, _ = self.facts()
        self.assertFalse(facts["manual_items"])
        self.assertTrue(facts["files"][0]["status"].startswith("R"))

    def test_binary_modification_is_explicit_gap(self):
        (self.work / "image.bin").write_bytes(b"\x00\x02")
        git(self.work, "add", "image.bin")
        git(self.work, "commit", "-m", "fix: image\n\nRefs: #123")
        facts, _ = self.facts()
        self.assertTrue(any("二進位" in item for item in facts["gaps"]))

    def test_staged_scope_excludes_unstaged_content_and_is_unchanged(self):
        (self.work / "feature.txt").write_text("staged\n", encoding="utf-8")
        git(self.work, "add", "feature.txt")
        (self.work / "feature.txt").write_text("unstaged\n", encoding="utf-8")
        before_status = git(self.work, "status", "--porcelain")
        before_index = Git(self.work).index_digest()
        facts, diff = self.facts("commit", requested_issues=["#123"])
        self.assertIn(b"+staged", diff)
        self.assertNotIn(b"+unstaged", diff)
        self.assertEqual(before_index, Git(self.work).index_digest())
        self.assertEqual(before_status, git(self.work, "status", "--porcelain"))
        self.assertEqual([], facts["commits"])
        folder, _ = self.save(facts, diff)
        result = cli.publish(folder / "snapshot.json")
        self.assertTrue(result["ready"])
        self.assertIn("尚未提交", Path(result["report"]).read_text(encoding="utf-8"))
        self.assertEqual(before_index, Git(self.work).index_digest())

    def test_no_staged_changes_fail(self):
        facts, _ = self.facts("commit")
        self.assertEqual("fail", self.check_status(facts, "has_changes"))

    def test_default_evidence_directory_inherits_usable_permissions(self):
        commit_file(self.work)
        args = argparse.Namespace(repo=str(self.work), mode="commit", remote=None,
                                  target=None, self_email=[], issue=[], output_dir=None)
        with patch.object(cli.tempfile, "gettempdir", return_value=str(self.root)):
            result = cli.capture(args)
        self.assertTrue(Path(result["snapshot"]).is_file())
        self.assertEqual(result["snapshot_id"], cli.load_snapshot(result["snapshot"])["snapshot_id"])

    def test_project_rule_failure_or_unknown_cannot_claim_ready(self):
        commit_file(self.work)
        facts, diff = self.facts()
        folder, analysis = self.save(facts, diff)
        for status in ("fail", "unknown"):
            analysis["project_findings"] = [dict(rule="AGENTS.md 的提交規範", status=status,
                                                  detail="實際提交訊息與專案規則不符或尚待核對")]
            (folder / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
            result = cli.publish(folder / "snapshot.json")
            self.assertFalse(result["ready"])
            self.assertEqual(status, result["status"])

    def test_initial_staged_commit_has_no_fabricated_hash(self):
        initial = self.root / "initial"
        initial.mkdir()
        git(initial, "init", "--initial-branch=main")
        configure(initial)
        (initial / "new.txt").write_text("new\n", encoding="utf-8")
        git(initial, "add", "new.txt")
        facts, diff = inspect(initial, "commit")
        self.assertIsNone(facts["head_sha"])
        self.assertFalse(facts["gaps"])
        folder, _ = self.save(facts, diff)
        result = cli.publish(folder / "snapshot.json")
        self.assertTrue(result["ready"])
        self.assertEqual([], facts["commits"])

    def test_draft_missing_or_wrong_requested_issue_fails(self):
        (self.work / "new.txt").write_text("new", encoding="utf-8")
        git(self.work, "add", "new.txt")
        facts, diff = self.facts("commit", requested_issues=["#123"])
        folder, analysis = self.save(facts, diff)
        for message in ("feat: new", "feat: new\n\nRefs: #999"):
            analysis["commit_message"] = message
            (folder / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
            self.assertEqual("fail", cli.publish(folder / "snapshot.json")["status"])

    def test_manual_confirmation_required_and_bound_to_snapshot(self):
        commit_file(self.work, "shared.txt", "changed\nbeta\ngamma\n")
        facts, diff = self.facts()
        folder, analysis = self.save(facts, diff)
        self.assertEqual("pending", cli.publish(folder / "snapshot.json")["status"])
        analysis["confirmations"] = [dict(item_id=m["id"], confirmed_by="Developer",
                                           reason="已由開發者確認為 #123 必要異動")
                                      for m in facts["manual_items"]]
        (folder / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
        self.assertTrue(cli.publish(folder / "snapshot.json")["ready"])
        commit_file(self.work, "second.txt")
        new_facts, new_diff = self.facts()
        new_folder, _ = self.save(new_facts, new_diff, "new-evidence")
        (new_folder / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
        with self.assertRaises(ValueError):
            cli.publish(new_folder / "snapshot.json")

    def test_no_semantic_summary_never_claims_ready(self):
        commit_file(self.work)
        facts, diff = self.facts()
        folder, analysis = self.save(facts, diff)
        analysis["diff_reviewed"] = False
        (folder / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
        self.assertEqual("pending", cli.publish(folder / "snapshot.json")["status"])

    def test_changed_head_branch_or_index_is_stale(self):
        commit_file(self.work)
        facts, _ = self.facts()
        git(self.work, "switch", "-c", "other-feature")
        self.assertFalse(verify(facts)["current"])
        git(self.work, "switch", "feature/test")
        commit_file(self.work, "second.txt")
        self.assertFalse(verify(facts)["current"])
        commit_facts, _ = self.facts("commit")
        (self.work / "third.txt").write_text("third", encoding="utf-8")
        git(self.work, "add", "third.txt")
        self.assertFalse(verify(commit_facts)["current"])

    def test_remote_changes_after_capture_make_report_unknown(self):
        head = commit_file(self.work)
        facts, diff = self.facts()
        folder, _ = self.save(facts, diff)
        self.advance_main()
        self.assertFalse(verify(facts)["current"])
        result = cli.publish(folder / "snapshot.json")
        self.assertEqual("unknown", result["status"])
        self.assertIn(head, Path(result["report"]).read_text(encoding="utf-8"))

    def test_failed_fetch_does_not_reuse_stale_tracking_ref(self):
        commit_file(self.work)
        git(self.work, "remote", "set-url", "origin", str(self.root / "missing.git"))
        facts, _ = self.facts(target="main")
        self.assertIsNone(facts["target_sha"])
        self.assertTrue(facts["gaps"])
        self.assertFalse(verify(facts)["current"])

    def test_ambiguous_remote_needs_explicit_selection(self):
        commit_file(self.work)
        git(self.work, "remote", "add", "upstream", str(self.remote))
        facts, _ = self.facts()
        self.assertTrue(any("--remote" in g for g in facts["gaps"]))
        facts, _ = self.facts(remote="origin")
        self.assertFalse(facts["gaps"])

    def test_fetch_is_scoped_and_preserves_head_index_and_fetch_head(self):
        commit_file(self.work)
        git(self.seed, "switch", "-c", "side")
        commit_file(self.seed, "side.txt")
        git(self.seed, "push", "origin", "side")
        git(self.seed, "tag", "not-requested")
        git(self.seed, "push", "origin", "refs/tags/not-requested")
        before_head = git(self.work, "rev-parse", "HEAD")
        before_index = Git(self.work).index_digest()
        fetch_head = self.work / ".git" / "FETCH_HEAD"
        before_fetch = fetch_head.read_bytes() if fetch_head.exists() else None
        self.facts()
        self.assertEqual(before_head, git(self.work, "rev-parse", "HEAD"))
        self.assertEqual(before_index, Git(self.work).index_digest())
        self.assertEqual(before_fetch, fetch_head.read_bytes() if fetch_head.exists() else None)
        self.assertEqual("", git(self.work, "tag", "--list"))
        self.assertNotIn("origin/side", git(self.work, "branch", "-r"))

    def test_shallow_history_is_a_gap(self):
        shallow = self.root / "shallow"
        git(self.root, "clone", "--depth=1", self.remote.as_uri(), str(shallow))
        configure(shallow)
        git(shallow, "switch", "-c", "feature/shallow")
        commit_file(shallow)
        facts, _ = inspect(shallow, "mr")
        self.assertTrue(any("shallow" in g for g in facts["gaps"]))

    def test_tampered_snapshot_or_patch_is_rejected(self):
        commit_file(self.work)
        facts, diff = self.facts()
        folder, _ = self.save(facts, diff)
        (folder / "changes.patch").write_bytes(diff + b"tampered")
        with self.assertRaises(ValueError):
            cli.load_snapshot(folder / "snapshot.json")
        (folder / "changes.patch").write_bytes(diff)
        facts["head_sha"] = "0" * 40
        (folder / "snapshot.json").write_text(json.dumps(facts), encoding="utf-8")
        with self.assertRaises(ValueError):
            cli.load_snapshot(folder / "snapshot.json")

    def test_cli_both_modes_and_report_exit_codes(self):
        for mode in ("commit", "mr"):
            if mode == "commit":
                (self.work / "feature.txt").write_text("feature\n", encoding="utf-8")
                git(self.work, "add", "feature.txt")
            else:
                git(self.work, "commit", "-m", "feat: add feature\n\nRefs: #123")
            process = subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPTS / "commit_review.py"),
                                      "inspect", "--repo", str(self.work), "--mode", mode,
                                      "--output-dir", str(self.root / f"cli-{mode}")],
                                     capture_output=True, timeout=120)
            self.assertEqual(0, process.returncode, process.stderr)
            captured = json.loads(process.stdout)
            analysis = cli.read_json(captured["analysis"])
            analysis.update(summary="新增 feature.txt。", diff_reviewed=True,
                            commit_message="feat: 新增功能\n\nRefs: #123")
            Path(captured["analysis"]).write_text(json.dumps(analysis), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exit_code = cli.main(["report", "--snapshot", captured["snapshot"]])
            self.assertEqual(0, exit_code)
            self.assertTrue(json.loads(out.getvalue())["ready"])


class IssueReferences(unittest.TestCase):
    def test_supported_and_false_reference_forms(self):
        self.assertEqual(["#123", "group/sub/project#42"],
                         issues("feat: feature #123\n\nRefs: group/sub/project#42, #123"))
        self.assertEqual([], issues("!123 TASK-123 #0 #123abc foo#123 \\#123"))


if __name__ == "__main__":
    unittest.main()

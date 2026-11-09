"""End-to-end tests create throwaway repositories; never change the user's repository."""
import csv
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import merge_review as review


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        local = Path(__file__).parent / "data" / "tests"
        local.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=local)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.git("init", "-b", "feature-current")
        self.git("config", "user.name", "Test Author")
        self.git("config", "user.email", "author@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        hooks = self.root / "test-only-hooks"
        hooks.mkdir()
        self.git("config", "core.hooksPath", str(hooks))
        self.base_content = b"first\n" + b"middle\n" * 12 + b"last\n"
        self.write("src/a.txt", self.base_content)
        self.write("src/b.txt", b"base b\n")
        self.write("asset.bin", b"\0base")
        self.write("delete.txt", b"delete base\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-b", "release-incoming")
        self.write("src/a.txt", self.base_content.replace(b"last", b"incoming last"))
        self.write("src/b.txt", b"incoming b\n")
        self.write("asset.bin", b"\0incoming")
        self.write("delete.txt", b"incoming keeps modified\n")
        self.git("add", ".")
        self.git("commit", "-m", "incoming change")
        self.incoming = self.git("rev-parse", "HEAD")
        self.git("checkout", "feature-current")
        self.write("src/a.txt", self.base_content.replace(b"first", b"current first"))
        self.write("src/b.txt", b"current b\n")
        self.write("asset.bin", b"\0current")
        self.git("rm", "delete.txt")
        self.git("add", ".")
        self.git("commit", "-m", "current change")
        self.current = self.git("rev-parse", "HEAD")
        self.git("merge", "release-incoming", ok=False)
        # a.txt would merge cleanly; expose it as an unmerged path to exercise replay.
        oid = lambda ref: self.git("rev-parse", ref + ":src/a.txt")
        index_info = (f"0 {'0'*40}\tsrc/a.txt\n" +
                      f"100644 {oid(self.base)} 1\tsrc/a.txt\n" +
                      f"100644 {oid(self.current)} 2\tsrc/a.txt\n" +
                      f"100644 {oid(self.incoming)} 3\tsrc/a.txt\n")
        self.run_git("update-index", "--index-info", input=index_info.encode())
        self.write("src/a.txt", b"temporary conflict worktree\n")

    def tearDown(self):
        self.temp.cleanup()

    def run_git(self, *args, input=None):
        return subprocess.run(["git", "-C", str(self.repo), *args], input=input, capture_output=True)

    def git(self, *args, ok=True):
        result = self.run_git(*args)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout.decode().strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def capture(self):
        return review.capture(self.repo, self.root / "sessions")

    def plan(self, session, choices):
        snapshot = review.read(session / "snapshot.json")
        path = self.root / "choices.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["snapshot", "path", "choice", "reviewer", "comment"])
            writer.writeheader()
            for name, choice in choices.items():
                writer.writerow({"snapshot": snapshot["id"], "path": name, "choice": choice})
        return review.plan_session(session, path)

    def test_capture_dynamic_branches_and_isolated_sessions(self):
        session = self.capture()
        another = self.capture()
        snap = review.read(session / "snapshot.json")
        self.assertEqual(snap["ours_label"], "feature-current")
        self.assertEqual(snap["theirs_label"], "release-incoming")
        self.assertNotEqual(session, another)
        self.assertEqual(len(snap["files"]), 4)
        self.assertTrue((session / 'review.md').is_file())
        self.assertIn('Test Author', (session / 'review.md').read_text(encoding='utf-8'))
        self.assertEqual(next(f for f in snap["files"] if f["path"] == "src/b.txt")["ours"]["author"], "Test Author")

    def test_apply_choices_clean_replay_manual_finish_and_backup(self):
        self.write("unrelated.txt", b"leave staged change intact")
        self.git("add", "unrelated.txt")
        before = self.git("rev-parse", ":unrelated.txt")
        session = self.capture()
        self.plan(session, {"src/b.txt": "theirs", "delete.txt": "ours"})
        result = review.apply_session(session)
        self.assertEqual(result["status"], "manual_required")
        self.assertEqual((self.repo / "src/b.txt").read_bytes(), b"incoming b\n")
        self.assertEqual((self.repo / "src/a.txt").read_bytes(), self.base_content.replace(b"first", b"current first").replace(b"last", b"incoming last"))
        self.assertFalse((self.repo / "delete.txt").exists())
        self.assertEqual((self.repo / "asset.bin").read_bytes(), b"\0current")
        self.assertIn("asset.bin", self.git("diff", "--name-only", "--diff-filter=U"))
        self.assertEqual(self.git("rev-parse", ":unrelated.txt"), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.current)
        self.assertTrue((session / "backup/index-stages.json").is_file())
        with self.assertRaises(review.ReviewError):
            review.finish_session(session, confirmed=True)
        self.write("asset.bin", b"\0current + incoming")
        report = review.finish_session(session, confirmed=True)
        self.assertEqual(report["resolved"], 4)
        self.assertEqual(report["problems"], [])
        self.assertEqual(self.git("diff", "--name-only", "--diff-filter=U"), "")
        with self.assertRaises(review.ReviewError):
            review.apply_session(session)

    def test_worktree_drift_blocks_before_any_write(self):
        session = self.capture()
        self.plan(session, {"src/b.txt": "theirs"})
        self.write("src/b.txt", b"new user's edit")
        original = (self.repo / "asset.bin").read_bytes()
        with self.assertRaises(review.ReviewError):
            review.apply_session(session)
        self.assertEqual((self.repo / "src/b.txt").read_bytes(), b"new user's edit")
        self.assertEqual((self.repo / "asset.bin").read_bytes(), original)
        self.assertFalse((session / "apply-state.json").exists())

    def test_pending_defaults_ours_when_incoming_has_no_path_history(self):
        self.assertEqual(review.default_choice({"theirs": None}), "ours")
        self.assertEqual(review.default_choice({"theirs": {"sha": "x"}}), "both")
        session = self.capture()
        plan = review.plan_session(session)
        self.assertTrue(all(f["choice"] == "both" for f in plan["files"]))  # 合入分支都有历史，仍默认 both

    def test_csv_wrong_snapshot_duplicate_and_unknown_path(self):
        session = self.capture()
        for bad in ("../outside.txt", "unknown.txt"):
            with self.assertRaises(review.ReviewError):
                self.plan(session, {bad: "ours"})
        path = self.root / "wrong.csv"
        path.write_text("snapshot,path,choice\nwrong,src/b.txt,ours\n", encoding="utf-8")
        with self.assertRaises(review.ReviewError):
            review.plan_session(session, path)
        snap = review.read(session / 'snapshot.json')
        path.write_text(f"snapshot,path,choice\n{snap['id']},src/b.txt,ours\n{snap['id']},src/b.txt,theirs\n", encoding='utf-8')
        with self.assertRaises(review.ReviewError):
            review.plan_session(session, path)

    def test_overlapping_text_stays_manual_not_silently_ours(self):
        session = self.capture()
        self.plan(session, {"asset.bin": "ours", "delete.txt": "theirs", "src/a.txt": "ours"})
        result = review.apply_session(session)
        row = next(f for f in result["files"] if f["path"] == "src/b.txt")
        self.assertEqual(row["status"], "manual")
        self.assertEqual((self.repo / "src/b.txt").read_bytes(), b"current b\n")
        self.write("src/b.txt", b"<<<<<<< ours\nconflict\n=======\n>>>>>>> theirs\n")
        with self.assertRaises(review.ReviewError):
            review.finish_session(session, confirmed=True)
        self.write("src/b.txt", b"current b\n")
        report = review.finish_session(session, confirmed=True, accept_baseline=["src/b.txt"])
        self.assertEqual(report["problems"], [])

    def test_tampered_source_object_is_rejected(self):
        session = self.capture()
        self.plan(session, {p: "ours" for p in ["src/a.txt", "src/b.txt", "asset.bin", "delete.txt"]})
        snap = review.read(session / "snapshot.json")
        file = next(f for f in snap["files"] if f["path"] == "asset.bin")
        (session / "blobs" / file["sources"]["ours"]["oid"]).write_bytes(b"tampered")
        with self.assertRaises(review.ReviewError):
            review.apply_session(session)
        self.assertFalse((session / 'apply-state.json').exists())

    def test_replay_base_is_explicit_and_head_drift_blocks(self):
        session = review.capture(self.repo, self.root / 'sessions', replay_base=self.base)
        self.assertEqual(review.read(session / 'snapshot.json')['replay_base'], self.base)
        self.plan(session, {'src/b.txt':'ours'})
        self.git('update-ref', 'HEAD', self.incoming)
        with self.assertRaises(review.ReviewError):
            review.apply_session(session)

    def test_verify_detects_changed_resolution(self):
        session = self.capture()
        self.plan(session, {p:'ours' for p in ['src/a.txt','src/b.txt','asset.bin','delete.txt']})
        review.apply_session(session)
        self.write('src/b.txt',b'changed after apply')
        self.assertTrue(review.verify_session(session)['problems'])

    def test_no_merge_is_rejected(self):
        self.git('merge','--abort')
        with self.assertRaises(review.ReviewError):
            self.capture()

    def test_restore_original_worktree_and_conflict_index(self):
        session = self.capture()
        snapshot = review.read(session / 'snapshot.json')
        before_index = review.Git(self.repo).stages()
        before_worktree = {f['path']: review.worktree_state(review.Git(self.repo), f['path']) for f in snapshot['files']}
        self.plan(session, {p:'ours' for p in before_worktree})
        review.apply_session(session)
        review.restore_session(session, confirmed=True)
        self.assertEqual(review.Git(self.repo).stages(), before_index)
        for p, expected in before_worktree.items():
            self.assertEqual(review.worktree_state(review.Git(self.repo), p), expected)

    def test_restore_does_not_overwrite_new_changes(self):
        session = self.capture()
        self.plan(session, {p:'ours' for p in ['src/a.txt','src/b.txt','asset.bin','delete.txt']})
        review.apply_session(session)
        self.write('src/b.txt',b'new manual work')
        with self.assertRaises(review.ReviewError):
            review.restore_session(session, confirmed=True)
        self.assertEqual((self.repo/'src/b.txt').read_bytes(),b'new manual work')


if __name__ == '__main__':
    unittest.main()

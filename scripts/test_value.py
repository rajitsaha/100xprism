#!/usr/bin/env python3
"""Tests for the value × cost pipeline (_value + token-dashboard value join).

Run: python3 scripts/test_value.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _value  # noqa: E402

# token-dashboard.py has a hyphen → load it by path.
_spec = importlib.util.spec_from_file_location(
    "token_dashboard", os.path.join(HERE, "token-dashboard.py"))
td = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(td)


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "-C", repo, *args],
        capture_output=True, text=True, check=False)


def commit(repo, subject):
    with open(os.path.join(repo, "f.txt"), "a") as f:
        f.write(subject + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class LabelTest(unittest.TestCase):
    def test_path_label_matches_dashboard(self):
        """The label the CLI registers must equal the label the dashboard derives
        from the same repo's transcript dir — otherwise cost never joins value."""
        repo = os.path.join(_value.HOME, "personal-github", "100xprism")
        transcript = os.path.join(
            _value.PROJECTS_DIR,
            repo.replace("/", "-"), "session.jsonl")
        self.assertEqual(_value.project_label_for_path(repo),
                         td.project_label(transcript))


class ResolveDirTest(unittest.TestCase):
    def test_resolves_hyphenated_segment(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as root:
            # real path has a hyphen IN a segment: <root>/personal-github/100xprism
            target = os.path.join(root, "personal-github", "100xprism")
            os.makedirs(target)
            mangled = target.replace("/", "-")   # lossy: every / and the hyphen are '-'
            self.assertEqual(_value.resolve_real_dir(mangled), target)

    def test_returns_none_when_absent(self):
        self.assertIsNone(_value.resolve_real_dir("-no-such-path-xyz-123"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(_value.resolve_real_dir(""))
        self.assertIsNone(_value.resolve_real_dir("-"))


class GitValueTest(unittest.TestCase):
    def test_windowed_commits_prs_files(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a (#1)")
            commit(repo, "fix: b")
            commit(repo, "docs: c (#2)")
            self.assertNotEqual(_value.git_head(repo), "")
            v = _value.git_value(repo, None, None)
            self.assertEqual(v["commits"], 3)
            self.assertEqual(v["prs"], 2)               # (#1) and (#2)
            self.assertGreaterEqual(v["files"], 1)
            self.assertEqual(len(v["subjects"]), 3)

    def test_not_a_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_value.git_value(d, None, None))


class DirValueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (_value.STORE_DIR, _value.STORE_PATH)
        _value.STORE_DIR = self.tmp.name
        _value.STORE_PATH = os.path.join(self.tmp.name, "value.json")

    def tearDown(self):
        _value.STORE_DIR, _value.STORE_PATH = self._orig
        self.tmp.cleanup()

    def test_git_dir_value_kind_git(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            v = _value.dir_value(repo, "~/x", "claude-code", None, None)
            self.assertEqual(v["kind"], "git")
            self.assertEqual(v["commits"], 1)

    def test_non_repo_uses_fs_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("x")
            v = _value.dir_value(d, "~/d", "claude-code", None, None)
            self.assertEqual(v["kind"], "fs")
            self.assertGreaterEqual(v["fs_files"], 1)

    def test_cache_preserves_summary_until_head_changes(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            _value.cached_dir_value(repo, "~/x", "claude-code", None, None)
            # inject a summary as the background pass would
            store = _value.load_store()
            store["dirs"][os.path.abspath(repo)]["value"]["summary"] = "did a thing"
            _value.save_store(store)
            again = _value.cached_dir_value(repo, "~/x", "claude-code", None, None)
            self.assertEqual(again["summary"], "did a thing")   # head unchanged → kept
            commit(repo, "fix: b")
            after = _value.cached_dir_value(repo, "~/x", "claude-code", None, None)
            self.assertIsNone(after["summary"])                 # head changed → recomputed


class AdapterTest(unittest.TestCase):
    def test_claude_iter_dir_days(self):
        import importlib
        ad = importlib.import_module("adapters.claude_code")
        summaries = [{
            "projdir": "-Users-rajit-x", "project": "~/x",
            "by_day": {"2026-06-01": {"input":1,"output":2,"cache_read":3,"cache_write":4}},
        }]
        rows = list(ad.iter_dir_days(summaries))
        self.assertEqual(rows[0].dir, "-Users-rajit-x")
        self.assertEqual(rows[0].day, "2026-06-01")
        self.assertEqual(rows[0].output, 2)
        self.assertEqual(rows[0].tool, "claude-code")

    def test_codex_stub_empty_without_sessions(self):
        import importlib
        cx = importlib.import_module("adapters.codex")
        # No ~/.codex/sessions on CI → yields nothing, never raises.
        self.assertEqual(list(cx.iter_usage()), [])


class DirectoriesShapeTest(unittest.TestCase):
    def test_build_directories_from_summaries(self):
        # exercise the pure assembler with a real git repo as one dir
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            label = _value.project_label_for_path(repo)
            mangled = os.path.abspath(repo).replace("/", "-")
            by_project_day_cost = {label: {"2026-06-01": 12.0}}
            tokens_by_label = {label: {"input":1,"output":2,"cache_read":3,"cache_write":4}}
            window_by_label = {label: ("2026-06-01", "2026-06-01")}
            dirs = td.assemble_directories(
                {mangled: label}, tokens_by_label, by_project_day_cost,
                window_by_label, tool_by_label={label: "claude-code"})
            row = dirs[0]
            self.assertEqual(row["label"], label)
            self.assertEqual(row["cost"], 12.0)
            self.assertEqual(row["value"]["kind"], "git")
            self.assertEqual(row["dir"], os.path.abspath(repo))


class DiscoverTest(unittest.TestCase):
    def _make_tree(self, root):
        """Helper: create a temp tree with specific layout."""
        proj = os.path.join(root, "proj")
        os.makedirs(proj)
        write(os.path.join(proj, "CLAUDE.md"), "# proj")
        sub = os.path.join(proj, "sub")
        os.makedirs(sub)
        write(os.path.join(sub, "normal.txt"), "no marker here")
        hidden = os.path.join(root, ".hidden")
        os.makedirs(hidden)
        write(os.path.join(hidden, "CLAUDE.md"), "# hidden")
        return proj

    def test_discovers_marker_dir(self):
        with tempfile.TemporaryDirectory() as root:
            proj = self._make_tree(root)
            found = _value.discover_project_dirs(root)
            self.assertIn(proj, found, "proj/ with CLAUDE.md should be discovered")
            hidden = os.path.join(root, ".hidden")
            self.assertNotIn(hidden, found, ".hidden/ should be pruned")

    def test_depth_cap(self):
        with tempfile.TemporaryDirectory() as root:
            # Create a marker at depth 3 (a/b/c/CLAUDE.md)
            deep = os.path.join(root, "a", "b", "c")
            os.makedirs(deep)
            write(os.path.join(deep, "CLAUDE.md"), "# deep")
            # With max_depth=2, depth-3 dir should NOT be found
            found = _value.discover_project_dirs(root, max_depth=2)
            self.assertNotIn(deep, found, "dir at depth 3 with max_depth=2 should be pruned")
            # With max_depth=3, it should be found
            found2 = _value.discover_project_dirs(root, max_depth=3)
            self.assertIn(deep, found2, "dir at depth 3 with max_depth=3 should be found")

    def test_cached_discover_uses_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig_dir = _value.STORE_DIR
            orig_path = _value.STORE_PATH
            try:
                _value.STORE_DIR = tmp
                _value.STORE_PATH = os.path.join(tmp, "value.json")
                # Set up a real project tree to discover
                proj_root = os.path.join(tmp, "projects")
                os.makedirs(proj_root)
                proj = os.path.join(proj_root, "myrepo")
                os.makedirs(proj)
                write(os.path.join(proj, "CLAUDE.md"), "# myrepo")
                # First call: does the walk
                result1 = _value.cached_discover(root=proj_root)
                self.assertIn(proj, result1)
                # Second call: should use cache (store has discovered_at)
                result2 = _value.cached_discover(root=proj_root)
                self.assertEqual(result1, result2)
                store = _value.load_store()
                self.assertIn("discovered_at", store)
                self.assertIsInstance(store.get("discovered"), dict)
            finally:
                _value.STORE_DIR = orig_dir
                _value.STORE_PATH = orig_path


class SummariesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (_value.STORE_DIR, _value.STORE_PATH)
        _value.STORE_DIR = self.tmp.name
        _value.STORE_PATH = os.path.join(self.tmp.name, "value.json")
        import importlib
        self.sm = importlib.import_module("_summaries")
        _value.save_store({"version": 2, "dirs": {"/x": {
            "label": "~/x", "tool": "claude-code", "head": "abc",
            "window": {"start": None, "end": None},
            "value": {"kind": "git", "commits": 1, "subjects": ["feat: a"],
                      "fs_files": 0, "summary": None}, "scanned": "t"}}})

    def tearDown(self):
        _value.STORE_DIR, _value.STORE_PATH = self._orig
        self.tmp.cleanup()

    def test_backfill_writes_summary(self):
        n = self.sm.backfill(runner=lambda prompt: "shipped feature a")
        self.assertEqual(n, 1)
        v = _value.load_store()["dirs"]["/x"]["value"]
        self.assertEqual(v["summary"], "shipped feature a")

    def test_backfill_graceful_when_cli_absent(self):
        n = self.sm.backfill(runner=lambda prompt: None)  # CLI absent / failed
        self.assertEqual(n, 0)
        self.assertIsNone(_value.load_store()["dirs"]["/x"]["value"]["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

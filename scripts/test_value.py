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
import _value as _shipped  # noqa: E402 — temporary alias; Task 9/10 rewrites this file

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
        repo = os.path.join(_shipped.HOME, "personal-github", "100xprism")
        transcript = os.path.join(
            _shipped.PROJECTS_DIR,
            repo.replace("/", "-"), "session.jsonl")
        self.assertEqual(_shipped.project_label_for_path(repo),
                         td.project_label(transcript))


class ResolveDirTest(unittest.TestCase):
    def test_resolves_hyphenated_segment(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as root:
            # real path has a hyphen IN a segment: <root>/personal-github/100xprism
            target = os.path.join(root, "personal-github", "100xprism")
            os.makedirs(target)
            mangled = target.replace("/", "-")   # lossy: every / and the hyphen are '-'
            self.assertEqual(_shipped.resolve_real_dir(mangled), target)

    def test_returns_none_when_absent(self):
        self.assertIsNone(_shipped.resolve_real_dir("-no-such-path-xyz-123"))

    def test_empty_input_returns_none(self):
        self.assertIsNone(_shipped.resolve_real_dir(""))
        self.assertIsNone(_shipped.resolve_real_dir("-"))


class GitValueTest(unittest.TestCase):
    def test_windowed_commits_prs_files(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a (#1)")
            commit(repo, "fix: b")
            commit(repo, "docs: c (#2)")
            self.assertNotEqual(_shipped.git_head(repo), "")
            v = _shipped.git_value(repo, None, None)
            self.assertEqual(v["commits"], 3)
            self.assertEqual(v["prs"], 2)               # (#1) and (#2)
            self.assertGreaterEqual(v["files"], 1)
            self.assertEqual(len(v["subjects"]), 3)

    def test_not_a_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_shipped.git_value(d, None, None))


class DirValueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (_shipped.STORE_DIR, _shipped.STORE_PATH)
        _shipped.STORE_DIR = self.tmp.name
        _shipped.STORE_PATH = os.path.join(self.tmp.name, "value.json")

    def tearDown(self):
        _shipped.STORE_DIR, _shipped.STORE_PATH = self._orig
        self.tmp.cleanup()

    def test_git_dir_value_kind_git(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            v = _shipped.dir_value(repo, "~/x", "claude-code", None, None)
            self.assertEqual(v["kind"], "git")
            self.assertEqual(v["commits"], 1)

    def test_non_repo_uses_fs_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("x")
            v = _shipped.dir_value(d, "~/d", "claude-code", None, None)
            self.assertEqual(v["kind"], "fs")
            self.assertGreaterEqual(v["fs_files"], 1)

    def test_cache_preserves_summary_until_head_changes(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            _shipped.cached_dir_value(repo, "~/x", "claude-code", None, None)
            # inject a summary as the background pass would
            store = _shipped.load_store()
            store["dirs"][os.path.abspath(repo)]["value"]["summary"] = "did a thing"
            _shipped.save_store(store)
            again = _shipped.cached_dir_value(repo, "~/x", "claude-code", None, None)
            self.assertEqual(again["summary"], "did a thing")   # head unchanged → kept
            commit(repo, "fix: b")
            after = _shipped.cached_dir_value(repo, "~/x", "claude-code", None, None)
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
            label = _shipped.project_label_for_path(repo)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

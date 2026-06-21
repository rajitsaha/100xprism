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


if __name__ == "__main__":
    unittest.main(verbosity=2)

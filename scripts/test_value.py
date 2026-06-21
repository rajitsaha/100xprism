#!/usr/bin/env python3
"""Tests for the value × cost pipeline (_shipped + token-dashboard value join).

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
import _shipped  # noqa: E402

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


class ChangelogTest(unittest.TestCase):
    def test_parse_releases_sections_bullets(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "CHANGELOG.md")
            write(p,
                  "# Changelog\n\n"
                  "## [1.1.0] — 2026-01-10\n### Added\n- one\n- two\n### Fixed\n- bug\n\n"
                  "## [1.0.0] — 2026-01-05\n### Added\n- start\n")
            rels = _shipped.parse_changelog(p, 5)
            self.assertEqual([r["version"] for r in rels], ["1.1.0", "1.0.0"])
            self.assertEqual(rels[0]["date"], "2026-01-10")
            self.assertEqual(rels[0]["sections"]["Added"], ["one", "two"])
            self.assertEqual(rels[0]["sections"]["Fixed"], ["bug"])


class GitBoundaryTest(unittest.TestCase):
    def test_tag_lag_does_not_double_count(self):
        """CHANGELOG ahead of tags: a released version's commits must NOT appear
        as unreleased — the chore(release) boundary owns the cutoff."""
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            commit(repo, "chore(release): 1.0.0")
            git(repo, "tag", "v1.0.0")          # tagged release
            commit(repo, "feat: b")
            commit(repo, "chore(release): 1.1.0")  # released in CHANGELOG, NOT tagged
            commit(repo, "docs: c")
            g = _shipped.git_summary(repo)
            self.assertEqual(g["since"], "last release")
            self.assertEqual(g["count"], 1)          # only "docs: c"
            self.assertEqual(g["buckets"], {"docs": ["docs: c"]})
            self.assertNotIn("feat", g["buckets"])    # 1.1.0's feat is NOT unreleased

    def test_tag_fallback_when_no_release_commit(self):
        with tempfile.TemporaryDirectory() as repo:
            git(repo, "init", "-q", "-b", "main")
            commit(repo, "feat: a")
            git(repo, "tag", "v0.1.0")
            commit(repo, "feat: b")
            g = _shipped.git_summary(repo)
            self.assertEqual(g["since"], "v0.1.0")
            self.assertEqual(g["count"], 1)
            self.assertEqual(list(g["buckets"]), ["feat"])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (_shipped.STORE_DIR, _shipped.STORE_PATH, _shipped.PROJECTS_DIR)
        _shipped.STORE_DIR = self.tmp.name
        _shipped.STORE_PATH = os.path.join(self.tmp.name, "value.json")
        _shipped.PROJECTS_DIR = os.path.join(self.tmp.name, "projects")  # empty → no autodiscover

    def tearDown(self):
        _shipped.STORE_DIR, _shipped.STORE_PATH, _shipped.PROJECTS_DIR = self._orig
        self.tmp.cleanup()

    def test_roundtrip_and_registry_beats_auto(self):
        reg = {"path": "/x/foo", "name": "foo", "label": "~/foo",
               "source": "registry", "releases": [], "unreleased": None}
        _shipped.save_repo(reg)
        self.assertEqual(_shipped.load_store()["repos"]["/x/foo"]["source"], "registry")
        # auto must NOT clobber a registry entry
        _shipped.save_repo({**reg, "source": "auto"})
        self.assertEqual(_shipped.load_store()["repos"]["/x/foo"]["source"], "registry")

    def test_value_panel_windowed_cost(self):
        store = {"version": 1, "repos": {"/x/foo": {
            "name": "foo", "label": "~/foo", "source": "registry",
            "releases": [
                {"version": "1.1", "date": "2026-01-10", "items": 2,
                 "sections": {"Added": ["a", "b"]}},
                {"version": "1.0", "date": "2026-01-05", "items": 1,
                 "sections": {"Added": ["x"]}},
            ],
            "unreleased": {"since": "last release", "count": 3, "buckets": {"feat": 3}},
        }}}
        _shipped.save_store(store)
        data = {"by_project_day_cost": {"~/foo": {
            "2026-01-04": 10.0, "2026-01-06": 20.0, "2026-01-08": 5.0,
            "2026-01-11": 7.0, "2026-01-20": 3.0}}}
        panel = td.value_panel(data)
        repo = panel["repos"][0]
        self.assertTrue(repo["has_cost"])
        costs = {r["version"]: r["cost"] for r in repo["releases"]}
        self.assertEqual(costs["1.1"], 25.0)   # (01-05, 01-10]: 20 + 5
        self.assertEqual(costs["1.0"], 10.0)    # (-inf, 01-05]: 10
        self.assertEqual(repo["unreleased"]["cost"], 10.0)  # (01-10, ...]: 7 + 3
        self.assertEqual(repo["releases"][0]["top"], ["a", "b"])


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

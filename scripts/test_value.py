#!/usr/bin/env python3
"""Tests for the value × cost pipeline (_value + token-dashboard value join).

Run: python3 scripts/test_value.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _value  # noqa: E402
import pricing  # noqa: E402

# Never touch the developer's real ~/.100xprism/value.json: every test starts from
# a throwaway store (tests that swap in their own still restore to this one).
_STORE_TMP = tempfile.TemporaryDirectory()
_value.STORE_DIR = _STORE_TMP.name
_value.STORE_PATH = os.path.join(_STORE_TMP.name, "value.json")

# token-dashboard.py has a hyphen → load it by path.
_spec = importlib.util.spec_from_file_location(
    "token_dashboard", os.path.join(HERE, "token-dashboard.py"))
td = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(td)


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t.t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgSign=false",
         "-C", repo, *args],
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
                         _value.project_label(transcript, {repo.replace("/", "-"): repo}))

    def test_path_label_preserves_dots_and_hyphens(self):
        repo = os.path.join(_value.HOME, ".claude-mem", "100x-prism")
        self.assertEqual(_value.project_label_for_path(repo),
                         "~/.claude-mem/100x-prism")

    def test_transcript_label_uses_resolved_path(self):
        repo = os.path.join(_value.HOME, ".claude-mem", "100x-prism")
        mangled = _value.mangle_path(repo)
        transcript = os.path.join(_value.PROJECTS_DIR, mangled, "session.jsonl")
        self.assertEqual(_value.project_label(transcript, {mangled: repo}),
                         "~/.claude-mem/100x-prism")

    def test_unresolved_transcript_label_does_not_invent_path_separators(self):
        mangled = _value.mangle_path(os.path.join(
            _value.HOME, "idle-timeout", "personal", "work", "reserch"))
        transcript = os.path.join(_value.PROJECTS_DIR, mangled, "session.jsonl")
        label = _value.project_label(transcript, {})
        self.assertEqual(label, "~/idle-timeout-personal-work-reserch (unresolved)")
        self.assertNotIn("/Users/example/person", label)


class ResolveDirTest(unittest.TestCase):
    def test_resolves_hyphenated_segment(self):
        import tempfile
        import os
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
            self.assertEqual(sum(day["commits"] for day in v["by_day"].values()), 3)
            self.assertEqual(sum(day["prs"] for day in v["by_day"].values()), 2)

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
    def test_claude_parse_emits_windowed_skill_dimensions(self):
        import importlib
        ad = importlib.import_module("adapters.claude_code")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({
                    "timestamp": "2026-07-09T10:00:00Z",
                    "sessionId": "skill-session",
                    "attributionSkill": "loop",
                    "message": {"role": "assistant", "model": "claude-sonnet-4-5-20250929",
                                 "content": "edited a file",
                                 "usage": {"input_tokens": 10, "output_tokens": 5,
                                           "cache_read_input_tokens": 2,
                                           "cache_creation_input_tokens": 1}},
                }) + "\n")
            parsed = ad.parse_file(path)
        self.assertIn("2026-07-09", parsed["comp_by_day"])
        self.assertIn("loop", parsed["by_skill_model"])
        self.assertIn("claude-sonnet-4-5-20250929", parsed["by_skill_model"]["loop"])
        self.assertEqual(parsed["skill_invocations_by_day"]["2026-07-09"]["loop"], 1)

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

    def test_codex_scan_empty_without_sessions(self):
        import importlib
        cx = importlib.import_module("adapters.codex")
        # No ~/.codex/sessions → scan() returns [] gracefully, never raises.
        orig = cx.SOURCE_DIR
        cx.SOURCE_DIR = "/nonexistent/path/xyz"
        try:
            self.assertEqual(cx.scan(verbose=False), [])
        finally:
            cx.SOURCE_DIR = orig


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

    def test_zero_cost_renders_none(self):
        """Fix 1: daycost values that sum to 0.0 must yield cost=None (renders —, not $0)."""
        label = "~/zero-cost-proj"
        # Put label in discovered so it enters all_labels; real=None avoids cached_dir_value
        dirs = td.assemble_directories(
            {}, {}, {label: {"2026-06-01": 0.0}},
            {label: (None, None)}, tool_by_label={label: "claude-code"},
            discovered={"ignored_real": label}, realdir_by_label={})
        self.assertEqual(len(dirs), 1)
        self.assertIsNone(dirs[0]["cost"])

    def test_discovery_only_tool_is_none(self):
        """Fix 2: labels that appear only in discovered (no token cost) must have tool=None."""
        label = "~/discovery-only-proj"
        dirs = td.assemble_directories(
            {}, {}, {},
            {}, tool_by_label={},
            discovered={"ignored": label}, realdir_by_label={})
        self.assertEqual(len(dirs), 1)
        self.assertIsNone(dirs[0]["tool"])
        self.assertIsNone(dirs[0]["cost"])


class DeliveryEconomicsTest(unittest.TestCase):
    def test_separates_total_attributed_and_unmeasured_value(self):
        rows = [
            {"cost": 25.0, "value": {"kind": "git", "commits": 5, "prs": 2,
                                      "releases": ["v1"], "files": 8,
                                      "insertions": 100, "deletions": 20}},
            {"cost": 75.0, "value": {"kind": "none"}},
        ]
        result = td._delivery_economics(100.0, rows)
        self.assertEqual(result["spend"], {
            "total": 100.0, "attributed": 25.0, "unattributed": 75.0,
            "coverage_pct": 25.0,
        })
        self.assertEqual(result["delivery_unit_cost"]["per_pr"], 12.5)
        self.assertEqual(result["delivery_unit_cost"]["per_commit"], 5.0)
        self.assertEqual(result["business_value"]["status"], "not_measured")

    def test_page_uses_delivery_language_and_pr_details(self):
        self.assertIn("Token cost → observable delivery → business value", td.PAGE)
        self.assertIn("delivery cost / PR", td.PAGE)
        self.assertIn("PR details", td.PAGE)
        self.assertIn("selectedComposition", td.PAGE)
        self.assertIn("selectedDelivery", td.PAGE)
        self.assertIn("directory</th><th>model", td.PAGE)
        self.assertNotIn("unit economics</th>", td.PAGE)

    def test_dashboard_inline_javascript_is_valid(self):
        script = td.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        result = subprocess.run(
            ["node", "--check"], input=script, capture_output=True, text=True,
            check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_undated_day_keys_do_not_break_time_windows(self):
        script = td.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        pick = lambda prefix: next(l for l in script.splitlines() if l.startswith(prefix))
        start = script.index("function dayInWindow(")
        helper = script[start:script.index("\n}\n", start) + 3]
        probe = "\n".join([pick("const WINDOWS="), pick("function windowDef("), helper,
            "const days=['2026-10-01','2026-10-10','unknown'];",
            "console.log(JSON.stringify([dayInWindow('2026-10-10','30d',days),"
            "dayInWindow('unknown','30d',days),dayInWindow('unknown','all',days)]));"])
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [True, False, True])

    def test_diagnostics_show_every_day_in_selected_history(self):
        script = td.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        pick = lambda prefix: next(l for l in script.splitlines() if l.startswith(prefix))
        def helper(name):
            start = script.index("function " + name + "(")
            return script[start:script.index("\n}\n", start) + 3]
        probe = "\n".join([pick("const WINDOWS="), pick("function windowDef("),
            helper("dayInWindow"), helper("diagnosticDays"),
            "const rows=Array.from({length:70},(_,i)=>"
            "[new Date(Date.UTC(2026,5,1+i)).toISOString().slice(0,10),{}]);",
            "const d={by_day:[...rows,['unknown',{}]]};",
            "console.log(JSON.stringify(['30d','90d','all'].map(w=>"
            "diagnosticDays(d,w).map(r=>r[0]))));"])
        result = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        recent, quarter, lifetime = json.loads(result.stdout)
        self.assertEqual(len(recent), 30)
        self.assertEqual(len(quarter), 70)
        self.assertEqual(len(lifetime), 70)
        self.assertEqual(lifetime[-1], "2026-06-01")
        self.assertEqual(lifetime, sorted(lifetime, reverse=True))

    def test_source_history_distinguishes_available_tool_ranges(self):
        script = td.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        start = script.index("function sourceHistory(")
        helper = script[start:script.index("\n}\n", start) + 3]
        data = {"tool_model_day_tokens": {
            "claude-code": {"sonnet": {"2026-09-11": 10, "unknown": 5}},
            "codex": {"sol": {"2026-06-27": 1, "2026-10-11": 2},
                      "luna": {"2026-06-27": 4}},
        }}
        result = subprocess.run(["node", "-e", helper + "\nconsole.log(JSON.stringify("
            "sourceHistory(" + json.dumps(data) + ")));"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {"tool": "claude-code", "start": "2026-09-11", "end": "2026-09-11", "days": 1},
            {"tool": "codex", "start": "2026-06-27", "end": "2026-10-11", "days": 2},
        ])


class ScanHomeTest(unittest.TestCase):
    def test_mangle_path_non_alnum_to_dash(self):
        """mangle_path replaces every non-alphanumeric char with '-'."""
        self.assertEqual(_value.mangle_path("/Users/example/100x-dev"),
                         "-Users-example-100x-dev")
        self.assertEqual(_value.mangle_path("/Users/example/.claude-mem/observer"),
                         "-Users-example--claude-mem-observer")

    def test_scan_home_indexes_hidden_and_hyphenated(self):
        """scan_home index contains mangled keys for hidden + hyphenated dirs."""
        with tempfile.TemporaryDirectory() as root:
            # hyphenated dir
            hyph = os.path.join(root, "100x-dev")
            os.makedirs(hyph)
            # hidden dir (not in _DISCOVER_SKIP)
            hidden = os.path.join(root, ".my-proj")
            os.makedirs(hidden)
            write(os.path.join(hidden, "CLAUDE.md"), "# hidden-proj")
            _, index = _value.scan_home(root)
            # Both dirs must be in the index
            self.assertIn(_value.mangle_path(hyph), index)
            self.assertEqual(index[_value.mangle_path(hyph)], hyph)
            self.assertIn(_value.mangle_path(hidden), index)
            self.assertEqual(index[_value.mangle_path(hidden)], hidden)

    def test_scan_home_markers_still_finds_claude_md(self):
        """scan_home markers finds dirs with CLAUDE.md (no regression on discovery)."""
        with tempfile.TemporaryDirectory() as root:
            proj = os.path.join(root, "myproj")
            os.makedirs(proj)
            write(os.path.join(proj, "CLAUDE.md"), "# myproj")
            markers, _ = _value.scan_home(root)
            self.assertIn(proj, markers)

    def test_resolution_via_index_beats_heuristic(self):
        """assemble_directories resolves a mangled name via dir_index even when
        resolve_real_dir would fail (hidden dir the heuristic can't un-mangle)."""
        with tempfile.TemporaryDirectory() as root:
            hidden_proj = os.path.join(root, ".claude-mem", "my-proj")
            os.makedirs(hidden_proj)
            label = _value.project_label_for_path(hidden_proj)
            mangled = _value.mangle_path(hidden_proj)
            dir_index = {mangled: hidden_proj}
            rows = td.assemble_directories(
                {mangled: label},
                {label: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}},
                {},
                {label: (None, None)},
                {label: "claude-code"},
                dir_index=dir_index,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["dir"], hidden_proj)


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
        _value.save_store({"version": _value.STORE_VERSION, "dirs": {"/x": {
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


class EnsureDaemonTest(unittest.TestCase):
    def test_already_running_no_spawn(self):
        """When port is already in use, ensure_daemon must NOT spawn a new process."""
        import unittest.mock as mock
        # Monkeypatch _port_in_use to simulate "already running"
        orig = td._port_in_use
        td._port_in_use = lambda port: True
        spawned = []
        orig_popen = __import__('subprocess').Popen
        try:
            import subprocess
            subprocess.Popen = lambda *a, **kw: spawned.append((a, kw)) or mock.MagicMock()
            # Clear opt-out env var if set
            env_backup = os.environ.pop("PRISM_NO_DASHBOARD", None)
            result = td.ensure_daemon(8787)
        finally:
            td._port_in_use = orig
            subprocess.Popen = orig_popen
            if env_backup is not None:
                os.environ["PRISM_NO_DASHBOARD"] = env_backup
        self.assertEqual(len(spawned), 0, "Must not spawn when port is already in use")
        self.assertIn("live", result)
        self.assertIn("http://127.0.0.1:8787", result)

    def test_opt_out_returns_none(self):
        """PRISM_NO_DASHBOARD=1 → ensure_daemon returns None and never spawns."""
        import subprocess
        orig_popen = subprocess.Popen
        spawned = []
        try:
            subprocess.Popen = lambda *a, **kw: spawned.append((a, kw)) or None
            os.environ["PRISM_NO_DASHBOARD"] = "1"
            result = td.ensure_daemon(8787)
        finally:
            subprocess.Popen = orig_popen
            del os.environ["PRISM_NO_DASHBOARD"]
        self.assertIsNone(result)
        self.assertEqual(len(spawned), 0)

    def test_free_port_spawns_daemon(self):
        import subprocess
        original = (td._port_in_use, td._stop_previous_dashboard, td._token_summary,
                    td.HOME, subprocess.Popen)
        spawned = []
        with tempfile.TemporaryDirectory() as root:
            try:
                td._port_in_use = lambda port: False
                td._stop_previous_dashboard = lambda port: []
                td._token_summary = lambda: None
                td.HOME = root
                subprocess.Popen = lambda *a, **kw: spawned.append((a, kw))
                result = td.ensure_daemon(8787)
            finally:
                (td._port_in_use, td._stop_previous_dashboard, td._token_summary,
                 td.HOME, subprocess.Popen) = original
        self.assertEqual(len(spawned), 1)
        self.assertIn("starting", result)

    def test_spawn_failure_is_reported(self):
        import contextlib
        import io
        import subprocess
        original = (td._port_in_use, td._stop_previous_dashboard, td._token_summary,
                    td.HOME, subprocess.Popen)
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            try:
                td._port_in_use = lambda port: False
                td._stop_previous_dashboard = lambda port: []
                td._token_summary = lambda: None
                td.HOME = root
                subprocess.Popen = lambda *a, **kw: (_ for _ in ()).throw(OSError("boom"))
                with contextlib.redirect_stderr(stderr):
                    result = td.ensure_daemon(8787)
            finally:
                (td._port_in_use, td._stop_previous_dashboard, td._token_summary,
                 td.HOME, subprocess.Popen) = original
        self.assertIsNone(result)
        self.assertIn("dashboard startup failed: boom", stderr.getvalue())

    def test_pid_write_failure_is_reported_without_crashing(self):
        import contextlib
        import io
        original_pid_file = td.PID_FILE
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            blocker = os.path.join(root, "not-a-directory")
            write(blocker, "file")
            td.PID_FILE = os.path.join(blocker, "dashboard.pid")
            try:
                with contextlib.redirect_stderr(stderr):
                    result = td._write_pid(8787)
            finally:
                td.PID_FILE = original_pid_file
        self.assertFalse(result)
        self.assertIn("dashboard pid file unavailable", stderr.getvalue())

    def test_background_refresh_failure_is_logged(self):
        import contextlib
        import io
        original_rebuild = td._rebuild
        stderr = io.StringIO()
        try:
            td._rebuild = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            with contextlib.redirect_stderr(stderr):
                td._safe_rebuild()
        finally:
            td._rebuild = original_rebuild
        self.assertIn("dashboard rebuild failed: boom", stderr.getvalue())

    def test_loading_payload_surfaces_live_scan_progress_and_partial_metrics(self):
        original_state = dict(td._build_state)
        try:
            td._build_state.update({
                "building": True,
                "stage": "scanning",
                "tool": "codex",
                "current": 300,
                "total": 646,
                "reparsed": 300,
                "cached": 0,
                "completed_sources": ["claude-code"],
                "preview": {
                    "records": 525,
                    "metered_sessions": 500,
                    "activity_sessions": 0,
                    "tokens": 123456,
                    "estimated_cost": 12.34,
                },
            })
            payload = td._loading_data()
        finally:
            td._build_state.clear()
            td._build_state.update(original_state)

        self.assertTrue(payload["loading"])
        self.assertEqual(payload["scan"], {
            "stage": "scanning",
            "tool": "codex",
            "current": 300,
            "total": 646,
            "reparsed": 300,
            "cached": 0,
            "completed_sources": ["claude-code"],
        })
        self.assertEqual(payload["preview"]["records"], 525)
        self.assertEqual(payload["preview"]["estimated_cost"], 12.34)

    def test_refresh_request_keeps_current_data_visible_while_rebuild_starts(self):
        original_rebuild = td._rebuild
        original_start = td._start_rebuild
        original_data = td.Handler.data
        original_state = dict(td._build_state)
        handler = object.__new__(td.Handler)
        handler.path = "/api/refresh"
        sent = []
        handler._send = lambda body, ctype="application/json", status=200: sent.append(
            (json.loads(body), ctype, status))
        starts = []

        def start(verbose=False):
            starts.append(verbose)
            td._build_state["building"] = True
            td._build_state["stage"] = "discovering"
            return True

        try:
            td.Handler.data = {"transcripts": 7, "generated": "earlier"}
            td._start_rebuild = start
            td._rebuild = lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("refresh request must not block on rebuild"))
            handler.do_GET()
        finally:
            td._rebuild = original_rebuild
            td._start_rebuild = original_start
            td.Handler.data = original_data
            td._build_state.clear()
            td._build_state.update(original_state)

        self.assertEqual(starts, [False])
        self.assertEqual(sent[0][2], 200)
        self.assertEqual(sent[0][0]["transcripts"], 7)
        self.assertTrue(sent[0][0]["refreshing"])
        self.assertEqual(sent[0][0]["scan"]["stage"], "discovering")

    def test_stop_previous_dashboard_terminates_owned_process(self):
        original_pids = td._owned_dashboard_pids
        original_kill = os.kill
        original_pid_file = td.PID_FILE
        signals = []
        try:
            td._owned_dashboard_pids = lambda port: [12345]
            td.PID_FILE = os.path.join(tempfile.gettempdir(), "missing-prism-pid")

            def fake_kill(pid, sig):
                if sig == 0:
                    raise ProcessLookupError()
                signals.append((pid, sig))

            os.kill = fake_kill
            self.assertEqual(td._stop_previous_dashboard(8787), [12345])
        finally:
            td._owned_dashboard_pids = original_pids
            td.PID_FILE = original_pid_file
            os.kill = original_kill
        self.assertEqual(signals, [(12345, __import__("signal").SIGTERM)])


class IncrementalClaudeScanTest(unittest.TestCase):
    def setUp(self):
        import adapters.claude_code as adapter
        self.adapter = adapter
        self.tmp = tempfile.TemporaryDirectory()
        self.original = (adapter.SOURCE_DIR, adapter.CACHE_FILE)
        adapter.SOURCE_DIR = os.path.join(self.tmp.name, "projects")
        adapter.CACHE_FILE = os.path.join(self.tmp.name, "cache.json")
        self.project = os.path.join(adapter.SOURCE_DIR, "-tmp-project")
        os.makedirs(self.project)

    def tearDown(self):
        self.adapter.SOURCE_DIR, self.adapter.CACHE_FILE = self.original
        self.tmp.cleanup()

    def test_recent_cache_skips_cold_transcript_stat_and_parse_but_refreshes_label(self):
        transcript = os.path.join(self.project, "old.jsonl")
        write(transcript, "{}\n")
        old = __import__("time").time() - self.adapter.HOT_FILE_SECONDS - 60
        os.utime(transcript, (old, old))
        st = os.stat(transcript)
        project_mtime = os.stat(self.project).st_mtime
        summary = {"mtime": st.st_mtime, "size": st.st_size,
                   "project": "~/project", "projdir": "-tmp-project",
                   "tool": "claude-code", "totals": {}}
        with open(self.adapter.CACHE_FILE, "w") as f:
            json.dump({"version": self.adapter.CACHE_VERSION,
                       "files": {transcript: summary},
                       "project_mtimes": {self.project: project_mtime},
                       "full_scan_at": __import__("time").time()}, f)
        original_parse = self.adapter.parse_file
        self.adapter.parse_file = lambda path: self.fail("cold cached file was parsed")
        try:
            rows = self.adapter.scan()
        finally:
            self.adapter.parse_file = original_parse
        self.assertEqual(rows[0]["mtime"], summary["mtime"])
        self.assertEqual(rows[0]["size"], summary["size"])
        self.assertEqual(rows[0]["project"], "Unresolved · tmp-project")
        self.assertEqual(rows[0]["projdir"], "-tmp-project")

    def test_malformed_cache_is_reported_and_ignored(self):
        import contextlib
        import io
        with open(self.adapter.CACHE_FILE, "w", encoding="utf-8") as f:
            f.write("{not valid json}")
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            cache = self.adapter.load_cache()

        self.assertEqual(cache, {})
        self.assertIn("ignoring unreadable cache", stderr.getvalue())


class TestMergedPRsAndReleases(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "commit", "--allow-empty", "-m", "init")

    def test_squash_pr_subject_counted(self):
        git(self.repo, "commit", "--allow-empty", "-m", "feat: add thing (#42)")
        v = _value.git_value(self.repo, None, None)
        self.assertEqual(v["prs"], 1)

    def test_real_merge_commit_counted(self):
        git(self.repo, "checkout", "-b", "feature", "-q")
        git(self.repo, "commit", "--allow-empty", "-m", "work")
        git(self.repo, "checkout", "-", "-q")
        git(self.repo, "merge", "--no-ff", "feature", "-m", "Merge pull request #7 from x/feature")
        v = _value.git_value(self.repo, None, None)
        self.assertEqual(v["prs"], 1)

    def test_squash_and_merge_dedup_by_number(self):
        git(self.repo, "commit", "--allow-empty", "-m", "feat: thing (#9)")
        git(self.repo, "checkout", "-b", "feature2", "-q")
        git(self.repo, "commit", "--allow-empty", "-m", "work2")
        git(self.repo, "checkout", "-", "-q")
        git(self.repo, "merge", "--no-ff", "feature2", "-m", "Merge pull request #9 duplicate-number-test")
        v = _value.git_value(self.repo, None, None)
        self.assertEqual(v["prs"], 1)  # same PR number from two commit shapes -> deduped

    def test_release_tags_in_window(self):
        git(self.repo, "tag", "v1.0.0")
        v = _value.git_value(self.repo, None, None)
        self.assertIn("v1.0.0", v["releases"])

    def test_empty_value_has_releases_key(self):
        self.assertEqual(_value._empty_value()["releases"], [])


class TestBuildIntegration(unittest.TestCase):
    """Exercises build() against a fake ~/.claude/projects tree (claude_code
    adapter only — codex.scan() safely returns [] when ~/.codex/sessions is
    absent in the test's patched HOME)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proj_dir = os.path.join(self.tmp, "claude", "projects", "-Users-x-proj")
        os.makedirs(self.proj_dir)
        session = {
            "type": "assistant",
            "attributionSkill": "loop",
            "message": {"role": "assistant", "content": "done",
                        "model": "claude-sonnet-4-5-20251001",
                        "usage": {"input_tokens": 1000, "output_tokens": 200,
                                  "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0}},
            "sessionId": "sess-abc", "timestamp": "2026-07-09T10:00:00Z",
            "isSidechain": False,
        }
        with open(os.path.join(self.proj_dir, "sess-abc.jsonl"), "w") as f:
            f.write(json.dumps(session) + "\n")
        self.cursor_dir = os.path.join(self.tmp, "cursor", "projects")
        cursor_transcript = os.path.join(
            self.cursor_dir, "Users-x-proj", "agent-transcripts", "cursor-abc",
            "cursor-abc.jsonl")
        os.makedirs(os.path.dirname(cursor_transcript))
        with open(cursor_transcript, "w") as f:
            f.write(json.dumps({"role": "user", "message": {"content": "task"}}) + "\n")
            f.write(json.dumps({"role": "assistant", "message": {"content": "done"}}) + "\n")

        import adapters.claude_code as claude_code
        import adapters.codex as codex
        import adapters.cursor as cursor
        import adapters.antigravity as antigravity
        self.claude_code = claude_code
        self.codex = codex
        self.cursor = cursor
        self.antigravity = antigravity
        self._orig_source = claude_code.SOURCE_DIR
        self._orig_cache = claude_code.CACHE_FILE
        self._orig_codex_source = codex.SOURCE_DIR
        self._orig_cursor_source = cursor.SOURCE_DIR
        self._orig_antigravity_source = antigravity.SOURCE_DIR
        claude_code.SOURCE_DIR = os.path.join(self.tmp, "claude", "projects")
        claude_code.CACHE_FILE = os.path.join(self.tmp, "cc-cache.json")
        codex.SOURCE_DIR = os.path.join(self.tmp, "nonexistent-codex")
        cursor.SOURCE_DIR = self.cursor_dir
        antigravity.SOURCE_DIR = os.path.join(self.tmp, "nonexistent-antigravity")

    def tearDown(self):
        self.claude_code.SOURCE_DIR = self._orig_source
        self.claude_code.CACHE_FILE = self._orig_cache
        self.codex.SOURCE_DIR = self._orig_codex_source
        self.cursor.SOURCE_DIR = self._orig_cursor_source
        self.antigravity.SOURCE_DIR = self._orig_antigravity_source

    def test_build_produces_by_session_and_by_skill_and_split(self):
        data = td.build(verbose=False)
        self.assertTrue(any(s["session_id"] == "sess-abc" for s in data["by_session"]))
        self.assertIn("main_subagent_split", data)
        self.assertGreaterEqual(data["main_subagent_split"]["main_cost"], 0)
        self.assertIn("fallback_pct", data)
        self.assertGreater(data["total_cost"], 0)
        self.assertIn("cost_by_purpose", data)
        self.assertAlmostEqual(sum(data["cost_by_purpose"].values()), data["total_cost"], places=2)
        self.assertIn("2026-07-09", data["by_day_model_cost"])
        self.assertEqual(data["data_quality"]["pricing_coverage_pct"], 100.0)
        self.assertEqual(data["pricing"]["as_of"], pricing.PRICING_AS_OF)
        self.assertIn("models", data["by_session"][0])
        skill = next(row for row in data["by_skill"] if row["skill"] == "loop")
        self.assertEqual(skill["tools"], ["claude-code"])
        self.assertEqual(skill["projects"], ["Unresolved · Users-x-proj"])
        self.assertEqual(skill["models"][0]["model"], "claude-sonnet-4-5-20251001")
        self.assertIn("loop", data["skill_by_day"]["2026-07-09"])
        self.assertEqual(data["activity"]["by_tool"]["cursor"]["sessions"], 1)
        self.assertEqual(data["activity"]["by_tool"]["cursor"]["messages"], 2)

    def test_build_skips_schema_incomplete_manifest_instead_of_crashing(self):
        """Important #4 regression: a structurally-valid-JSON-but-missing-
        required-keys manifest (e.g. no "outcome") used to raise an uncaught
        KeyError deep in _build_handoff_runs, crashing the ENTIRE dashboard
        build() over one bad manifest file. It must be skipped instead."""
        import run_manifest as rm
        orig_runs_dir = rm.RUNS_DIR
        runs_tmp = tempfile.mkdtemp()
        rm.RUNS_DIR = runs_tmp  # isolate from the real ~/.100xprism/handoff-runs
        try:
            malformed = {
                "v": 1, "run_id": "broken-run", "task": "t", "cwd": "/x",
                "branch": "b", "pr": None, "coder": "claude", "reviewer": "codex",
                "reviewer_fallback": False, "rounds": [],
                # "outcome" intentionally omitted — schema-incomplete
            }
            with open(os.path.join(runs_tmp, "broken-run.json"), "w") as f:
                json.dump(malformed, f)
            data = td.build(verbose=False)  # must not raise
            self.assertNotIn("broken-run", [r["run_id"] for r in data["handoff_runs"]])
        finally:
            rm.RUNS_DIR = orig_runs_dir

    def test_build_skips_manifest_with_null_outcome_instead_of_crashing(self):
        """Codex CLI review finding: a manifest with `"outcome": null` is
        valid JSON and has the "outcome" key (so no KeyError), but
        `manifest["outcome"].get(...)` in _build_handoff_runs raises
        AttributeError on None. The original `except (OSError, ValueError,
        KeyError)` didn't catch that, crashing the whole dashboard build over
        one bad manifest. Must be skipped instead."""
        import run_manifest as rm
        orig_runs_dir = rm.RUNS_DIR
        runs_tmp = tempfile.mkdtemp()
        rm.RUNS_DIR = runs_tmp  # isolate from the real ~/.100xprism/handoff-runs
        try:
            malformed = {
                "v": 1, "run_id": "null-outcome-run", "task": "t", "cwd": "/x",
                "branch": "b", "pr": None, "coder": "claude", "reviewer": "codex",
                "reviewer_fallback": False, "rounds": [],
                "outcome": None,  # present but null -> AttributeError on .get()
            }
            with open(os.path.join(runs_tmp, "null-outcome-run.json"), "w") as f:
                json.dump(malformed, f)
            data = td.build(verbose=False)  # must not raise
            self.assertNotIn("null-outcome-run", [r["run_id"] for r in data["handoff_runs"]])
        finally:
            rm.RUNS_DIR = orig_runs_dir


class GitHubInsightsTest(unittest.TestCase):
    def test_github_slug_parses_common_remote_urls(self):
        self.assertEqual(td._github_slug("https://github.com/acme/widget.git"), "acme/widget")
        self.assertEqual(td._github_slug("git@github.com:acme/widget.git"), "acme/widget")
        self.assertEqual(td._github_slug("https://github.com/acme/widget"), "acme/widget")
        self.assertIsNone(td._github_slug("https://gitlab.com/acme/widget.git"))

    def test_github_users_from_config_accepts_list_or_string(self):
        self.assertEqual(td._github_users_from_config({"users": ["octocat", "bad/name"]}), ["octocat"])
        self.assertEqual(td._github_users_from_config({"users": "octocat, hubot"}), ["octocat", "hubot"])
        self.assertEqual(td._github_users_from_config({"users": None}), [])

    def test_github_repos_from_config_accepts_urls_or_slugs(self):
        repos = td._github_repos_from_config({
            "repos": [
                "https://github.com/acme/example-service/",
                "git@github.com:acme/example-docs.git",
                "bad/repo/name",
            ]
        })
        self.assertEqual([r["slug"] for r in repos],
                         ["acme/example-service", "acme/example-docs"])

    def test_bounded_int_handles_bad_config_values(self):
        self.assertEqual(td._bounded_int("7", 12, 1, 50), 7)
        self.assertEqual(td._bounded_int("bad", 12, 1, 50), 12)
        self.assertEqual(td._bounded_int(999, 12, 1, 50), 50)
        self.assertEqual(td._bounded_int(0, 12, 1, 50), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""
pair-loop.py — CLI orchestration for the /pair-loop handoff skill.

The interactive agent running the skill IS the coder; this CLI never simulates
coding. It owns: run bookkeeping (manifest + HANDOFF.md), the per-round budget
check, invoking the reviewer subprocess, and PR-body assembly. See
https://github.com/rajitsaha/100xprism/blob/131564c/docs/superpowers/specs/2026-07-09-pair-loop-handoff-skill-design.md
(archived) for the full state machine.

Subcommands: start, budget-check, coder-done, review, finish.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_manifest  # noqa: E402
import handoff  # noqa: E402
import reviewer  # noqa: E402
import _config  # noqa: E402
import _budget  # noqa: E402
import adapters.claude_code as claude_code  # noqa: E402
import adapters.codex as codex  # noqa: E402
import adapters.pi as pi  # noqa: E402


def _git_dirty(cwd):
    r = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def _current_branch(cwd):
    r = subprocess.run(["git", "branch", "--show-current"], cwd=cwd,
                       capture_output=True, text=True)
    return r.stdout.strip() or "HEAD"


def _effective_per_run_usd(cfg):
    """Default $5 when coder is pi and the user has not set a cap."""
    per_run = (cfg.get("budget") or {}).get("per_run_usd")
    if per_run is None and (cfg.get("pair_loop") or {}).get("coder") == "pi":
        return 5
    return per_run


def _pi_independence_error(config):
    if config.get("coder") != "pi" or config.get("reviewer") != "pi":
        return None
    coder_provider = config.get("coder_provider")
    reviewer_provider = config.get("reviewer_provider")
    if not coder_provider or not reviewer_provider or coder_provider == reviewer_provider:
        return "Pi coder and reviewer require two explicitly configured different providers"
    coder_model = config.get("coder_model")
    reviewer_model = config.get("reviewer_model")
    if coder_model and reviewer_model and coder_model == reviewer_model:
        return "Pi coder and reviewer must not use the same model"
    return None


def cmd_start(args):
    cwd = os.path.abspath(args.cwd or ".")
    # The dirty-tree check must run before any manifest/HANDOFF.md side effects
    # are created — a rejected start must leave zero partial state behind.
    if _git_dirty(cwd):
        print("error: working tree is dirty — commit or stash before starting a pair-loop run",
              file=sys.stderr)
        sys.exit(1)
    full = _config.load_config()
    cfg = full["pair_loop"]
    independence_error = _pi_independence_error(cfg)
    if independence_error:
        print(f"error: {independence_error}", file=sys.stderr)
        sys.exit(1)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    branch = _current_branch(cwd)
    manifest = run_manifest.new_manifest(run_id, args.task, cwd, branch,
                                          cfg["coder"], cfg["reviewer"])
    manifest["coder_provider"] = cfg.get("coder_provider")
    manifest["reviewer_provider"] = cfg.get("reviewer_provider")
    manifest["coder_model"] = cfg.get("coder_model")
    manifest["reviewer_model"] = cfg.get("reviewer_model")
    run_manifest.save_manifest(manifest)
    handoff_path = os.path.join(cwd, handoff.HANDOFF_FILENAME)
    handoff.init_file(handoff_path, run_id, args.task, branch, cfg["coder"], cfg["reviewer"])
    print(json.dumps({"run_id": run_id, "manifest_path": run_manifest.manifest_path(run_id),
                      "handoff_path": handoff_path, "max_rounds": cfg["max_rounds"],
                      "coder": cfg["coder"], "reviewer": cfg["reviewer"],
                      "per_run_usd": _effective_per_run_usd(full)}))


def cmd_budget_check(args):
    manifest = run_manifest.load_manifest(args.run)
    full = _config.load_config()
    per_run = _effective_per_run_usd(full)
    summaries = claude_code.scan(verbose=False) + codex.scan(verbose=False) + pi.scan(verbose=False)
    cost = run_manifest.run_cost(manifest, summaries)
    _, level = _budget.status_for(cost["total"], per_run)
    print(json.dumps({"level": level, "spent": cost["total"], "limit": per_run}))
    sys.exit(2 if level == "alert" else 0)


CLAUDE_PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def _guess_current_claude_session(cwd):
    """Best-effort: the most-recently-modified transcript file for this cwd's
    mangled project dir, at the moment this is called."""
    import _value
    mangled = _value.mangle_path(os.path.abspath(cwd))
    proj_dir = os.path.join(CLAUDE_PROJECTS, mangled)
    files = glob.glob(os.path.join(proj_dir, "*.jsonl"))
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    return os.path.splitext(os.path.basename(newest))[0]


def _guess_current_codex_session(before_mtimes):
    """Best-effort: a Codex rollout file that appeared/changed after
    `before_mtimes` was snapshotted (see cmd_review)."""
    paths = glob.glob(os.path.join(codex.SOURCE_DIR, "**", "*.jsonl"), recursive=True)
    newest, newest_mtime = None, 0
    for p in paths:
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if mt > before_mtimes.get(p, 0) and mt > newest_mtime:
            newest, newest_mtime = p, mt
    if not newest:
        return None
    with open(newest, errors="ignore") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") == "session_meta":
                payload = o.get("payload") or {}
                return payload.get("session_id") or payload.get("id")
    return None


def _guess_current_pi_session(before_mtimes):
    """Newest Pi session jsonl that appeared/changed after `before_mtimes`."""
    paths = glob.glob(os.path.join(pi.SOURCE_DIR, "**", "*.jsonl"), recursive=True)
    newest, newest_mtime = None, 0
    for p in paths:
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if mt > before_mtimes.get(p, 0) and mt > newest_mtime:
            newest, newest_mtime = p, mt
    if not newest:
        return None
    with open(newest, errors="ignore") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not isinstance(o, dict):
                continue
            sid = o.get("sessionId") or o.get("session_id") or o.get("id")
            if sid:
                return sid
    return os.path.splitext(os.path.basename(newest))[0]


def _dominant_model(by_model):
    """Best-effort dominant model for a transcript's by_model breakdown: the
    model with the most input+output tokens, excluding the 'unknown'
    placeholder both adapters use when a line carries no model field."""
    candidates = {m: v for m, v in (by_model or {}).items() if m and m != "unknown"}
    if not candidates:
        return None
    return max(candidates, key=lambda m: candidates[m].get("input", 0) + candidates[m].get("output", 0))


def _resolve_session_model(tool, session_id, claude_summaries, codex_summaries,
                           pi_summaries=None):
    """Best-effort: the dominant model a given tool+session_id actually ran
    with, from already-scanned transcript summaries. None whenever the model
    can't be established.
    """
    if not session_id:
        return None
    if tool == "claude":
        summaries = claude_summaries
    elif tool == "codex":
        summaries = codex_summaries
    elif tool == "pi":
        summaries = pi_summaries or []
    else:
        return None
    for s in summaries:
        if s.get("session_id") == session_id:
            return _dominant_model(s.get("by_model"))
    return None


def cmd_coder_done(args):
    manifest = run_manifest.load_manifest(args.run)
    cwd = manifest["cwd"]
    if manifest["coder"] == "claude":
        session_id = _guess_current_claude_session(cwd)
    elif manifest["coder"] == "codex":
        # The coder's actual work happens between the previous CLI call and
        # this one, driven by the calling agent — not by this process — so
        # there's no "before" snapshot to take here the way cmd_review takes
        # `codex_before` right before invoking the reviewer subprocess.
        # Best-effort fallback: reuse _guess_current_codex_session with an
        # empty before-snapshot, which picks the single newest Codex session
        # file for this machine. Correct for the common single-active-session
        # case; could misattribute if multiple Codex sessions are running
        # concurrently in the same directory.
        session_id = _guess_current_codex_session({})
    elif manifest["coder"] == "pi":
        session_id = _guess_current_pi_session({})
    else:
        session_id = None
    round_ = run_manifest.add_round(manifest, "coder", manifest["coder"], session_id=session_id)
    run_manifest.close_round(manifest, round_, findings_addressed=args.findings_addressed)
    handoff_path = os.path.join(cwd, handoff.HANDOFF_FILENAME)
    handoff.append_coder_round(handoff_path, round_["n"], manifest["coder"], args.summary)
    print(json.dumps({"round": round_["n"]}))


MAX_UNTRACKED_FILE_BYTES = 200_000  # guard against dumping something absurd (data file, lockfile) into the prompt


def _build_untracked_sections(cwd):
    """`git diff <branch>` never includes untracked (new, not-yet-`git add`-ed)
    files — but the pair-loop workflow explicitly tells the coder not to
    commit until the run finishes, so brand-new files stay untracked for the
    whole loop. Without this, the reviewer could approve a round having never
    seen a new file's contents at all. Show each untracked file's full
    current content (there's no "before" to diff against). Skip binaries and
    huge files gracefully rather than erroring the whole review out."""
    result = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                            cwd=cwd, capture_output=True, text=True)
    sections = []
    for rel_path in result.stdout.splitlines():
        if not rel_path:
            continue
        abs_path = os.path.join(cwd, rel_path)
        try:
            if os.path.getsize(abs_path) > MAX_UNTRACKED_FILE_BYTES:
                sections.append(f"=== NEW (untracked) FILE: {rel_path} ===\n"
                                 f"[skipped — {os.path.getsize(abs_path)} bytes, over the "
                                 f"{MAX_UNTRACKED_FILE_BYTES}-byte review limit]\n")
                continue
            with open(abs_path, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        sections.append(f"=== NEW (untracked) FILE: {rel_path} ===\n{content}\n")
    return sections


def _build_review_prompt(cwd, branch, handoff_path):
    result = subprocess.run(["git", "diff", branch], cwd=cwd, capture_output=True,
                            text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git diff against '{branch}' failed: {result.stderr.strip()}")
    diff = result.stdout
    untracked_sections = _build_untracked_sections(cwd)
    untracked_text = "\n".join(untracked_sections)
    if os.path.exists(handoff_path):
        with open(handoff_path, encoding="utf-8") as f:
            handoff_text = f.read()
    else:
        handoff_text = ""
    return (
        "You are reviewing a code change as an independent reviewer in a "
        "coder<->reviewer handoff loop. Read the diff, any new untracked "
        "files, and the prior handoff rounds below, then respond with a "
        "numbered findings list in the form "
        "'N. [category] file:line — description' (omit file:line if not "
        "applicable), followed by a final line that is EXACTLY "
        "'VERDICT: APPROVED' or 'VERDICT: CHANGES_REQUESTED'.\n\n"
        f"=== DIFF vs {branch} ===\n{diff}\n\n"
        + (f"{untracked_text}\n\n" if untracked_text else "")
        + f"=== HANDOFF SO FAR ===\n{handoff_text}\n"
    )


def cmd_review(args):
    manifest = run_manifest.load_manifest(args.run)
    cwd = manifest["cwd"]
    prompt = _build_review_prompt(cwd, manifest["branch"],
                                  os.path.join(cwd, handoff.HANDOFF_FILENAME))

    run_command = None
    if args.reviewer_cmd:
        fixed_cmd = json.loads(args.reviewer_cmd)

        def run_command(cmd, prompt, cwd, timeout):
            r = subprocess.run(fixed_cmd, input=prompt, cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
            return r.stdout

    codex_before = {p: os.path.getmtime(p) for p in
                    glob.glob(os.path.join(codex.SOURCE_DIR, "**", "*.jsonl"), recursive=True)} \
        if os.path.isdir(codex.SOURCE_DIR) else {}

    fallback_models = _config.load_config()["pair_loop"].get("fallback_models", {})

    # --reviewer-cmd fully overrides dispatch: the supplied argv is run verbatim
    # and no vendor binary is invoked, so requiring one on PATH would break the
    # override on exactly the machines it exists for.
    require_cli = not args.reviewer_cmd

    reviewer_provider = manifest.get("reviewer_provider")
    reviewer_model = manifest.get("reviewer_model")
    independence_error = _pi_independence_error(manifest)
    if independence_error:
        print(f"error: {independence_error}", file=sys.stderr)
        sys.exit(1)

    result = reviewer.invoke(manifest["reviewer"], prompt, cwd, run_command=run_command,
                             fallback_models=fallback_models, require_cli=require_cli,
                             coder=manifest["coder"],
                             provider=reviewer_provider, model=reviewer_model)
    verdict = handoff.parse_verdict(result.output)
    if verdict is None:
        # one re-ask with a stricter prompt, then fall back to CHANGES_REQUESTED
        retry_prompt = prompt + "\n\nYour previous response had no parseable VERDICT line. Respond again, ending with exactly 'VERDICT: APPROVED' or 'VERDICT: CHANGES_REQUESTED'."
        result = reviewer.invoke(manifest["reviewer"], retry_prompt, cwd, run_command=run_command,
                                 fallback_models=fallback_models, require_cli=require_cli,
                             coder=manifest["coder"],
                             provider=reviewer_provider, model=reviewer_model)
        verdict = handoff.parse_verdict(result.output)
    findings = handoff.parse_findings(result.output)
    if verdict is None:
        verdict = "CHANGES_REQUESTED"
        findings = findings or [{"n": 1, "category": "process",
                                  "location": "", "text": "reviewer produced no parseable verdict"}]

    # Read the tool that actually ran from the result — do not re-derive it.
    # A hardcoded two-vendor guess here previously ran independently of
    # reviewer.select_fallback()'s real choice, so a Codex-to-Cursor fallback
    # was recorded as Claude everywhere: the manifest, HANDOFF.md, the round,
    # and therefore the PR summary. `result.tool` is select_fallback()'s
    # actual winner; there is nothing left to guess.
    actual_tool = result.tool
    if result.fallback_used:
        manifest["reviewer_fallback"] = True
        # Record the model (None when unpinned) so the summary can report what
        # actually ran. It is not evidence of independence — nothing compares
        # it to the coder's model; see #93.
        manifest["reviewer_fallback_model"] = result.model
        # Record which vendor actually ran. The summary must not infer it:
        # config permits coder == reviewer, so the fallback is not necessarily
        # the coder's vendor.
        manifest["reviewer_fallback_tool"] = actual_tool

    # Only guess a session for vendors we actually know how to find one for.
    # The old `else _guess_current_claude_session(...)` treated "not codex" as
    # "must be claude" — wrong the moment a third vendor (Cursor) could run,
    # attaching an unrelated Claude session (and its cost) to a Cursor review.
    # No session lookup exists for Cursor yet; None is honest, a wrong guess
    # is not.
    if actual_tool == "codex":
        session_id = _guess_current_codex_session(codex_before)
    elif actual_tool == "claude":
        session_id = _guess_current_claude_session(cwd)
    elif actual_tool == "pi":
        session_id = _guess_current_pi_session({})
    else:
        session_id = None

    # #93: verify independence instead of assuming it. A same-model review is
    # worth roughly nothing as a second opinion, whether that happened via an
    # unpinned same-vendor fallback or a plain coder==reviewer misconfig — an
    # APPROVED verdict from one must not pass silently. Only worth the scan
    # when the verdict is APPROVED; a CHANGES_REQUESTED round has nothing to
    # refuse. Both models must actually resolve (see _resolve_session_model) —
    # unknown is never treated as "safe", but it also can't be treated as a
    # conflict.
    same_model_conflict = None
    if verdict == "APPROVED":
        coder_rounds = [r for r in manifest["rounds"] if r["role"] == "coder"]
        coder_round = coder_rounds[-1] if coder_rounds else None
        if coder_round is not None:
            claude_summaries = claude_code.scan(verbose=False)
            codex_summaries = codex.scan(verbose=False)
            pi_summaries = pi.scan(verbose=False)
            coder_model = _resolve_session_model(coder_round.get("tool"), coder_round.get("session_id"),
                                                 claude_summaries, codex_summaries, pi_summaries)
            reviewer_model = _resolve_session_model(actual_tool, session_id,
                                                    claude_summaries, codex_summaries, pi_summaries)
            if coder_model and reviewer_model and coder_model == reviewer_model:
                same_model_conflict = coder_model
                manifest["reviewer_same_model_conflict"] = coder_model
                verdict = "CHANGES_REQUESTED"
                findings = findings + [{
                    "n": len(findings) + 1, "category": "process", "location": "",
                    "text": (f"Reviewer ran on the same model as the coder ({coder_model}) "
                             "— independence could not be established, so this approval "
                             "is refused (#93). Pick a fallback_models entry (or a "
                             "reviewer vendor/model) that differs from the coder's."),
                }]

    round_ = run_manifest.add_round(manifest, "reviewer", actual_tool, session_id=session_id)
    run_manifest.close_round(manifest, round_, findings=len(findings), verdict=verdict)
    handoff.append_reviewer_round(os.path.join(cwd, handoff.HANDOFF_FILENAME),
                                  round_["n"], actual_tool, findings, verdict)
    print(json.dumps({"verdict": verdict, "findings": findings,
                      "fallback_used": result.fallback_used,
                      "reviewer_tool": result.tool,
                      "reviewer_model": result.model,
                      "same_model_conflict": same_model_conflict}))


def render_review_summary(manifest):
    """Markdown summary of the loop, for posting as a PR comment.

    A reviewer on GitHub otherwise sees only the final diff, with no evidence
    that an adversarial pass happened at all. This records who reviewed (and on
    which model, when one was pinned — the normal path uses each CLI's own
    default, which is not recorded), how many rounds it took, and how many
    findings were raised
    and resolved.
    """
    reviewer_rounds = [r for r in manifest["rounds"] if r["role"] == "reviewer"]
    coder_rounds = [r for r in manifest["rounds"] if r["role"] == "coder"]
    findings_raised = sum(r.get("findings") or 0 for r in reviewer_rounds)
    findings_addressed = sum(r.get("findings_addressed") or 0 for r in coder_rounds)

    reviewer_label = manifest["reviewer"]
    fallback_model = None
    if manifest.get("reviewer_fallback"):
        # Name the model — it is the only thing distinguishing this from a
        # self-review, so burying it would misrepresent the result.
        actual = manifest.get("reviewer_fallback_tool") or (
            "claude" if manifest["reviewer"] == "codex" else "codex")
        fallback_model = manifest.get("reviewer_fallback_model")
        reviewer_label = f"{actual} (`{fallback_model}`)" if fallback_model else f"{actual} (model not pinned)"

    lines = [
        "## Pair-loop review",
        "",
        "| | |",
        "|---|---|",
        f"| Coder | {manifest['coder']} |",
        f"| Reviewer | {reviewer_label} |",
        f"| Rounds | {manifest['outcome']['rounds']} |",
        f"| Findings raised | {findings_raised} |",
        f"| Findings addressed | {findings_addressed} |",
        f"| Verdict | **{manifest['outcome']['verdict']}** |",
        "",
    ]

    if manifest.get("reviewer_fallback"):
        # State only what is known. Nothing here compares the reviewer's model
        # to the coder's, so claiming the models *differed* and claiming they
        # *matched* are equally unfounded — earlier versions asserted each in
        # turn, and both were wrong. Do not claim the fallback is the coder's
        # vendor either: config permits coder == reviewer, and with three
        # vendors the fallback may be a third one entirely. Name what ran.
        same_vendor = actual == manifest["coder"]
        vendor_note = ("the coder's own vendor" if same_vendor
                       else f"`{actual}`, a different vendor from the coder")
        if fallback_model:
            warning = (
                f"> ⚠️ **Configured reviewer unavailable.** The "
                f"`{manifest['reviewer']}` CLI was not on PATH, so the review ran on "
                f"{vendor_note}, pinned to `{fallback_model}`."
                + ("" if not same_vendor else
                   " If that is not the model you code with, this is a weaker-but-real "
                   "second opinion; if it is, the approval carries no independent signal.")
            )
        else:
            warning = (
                f"> 🛑 **Independence unverified.** The `{manifest['reviewer']}` CLI was "
                f"not on PATH, and no usable fallback model was configured for the "
                f"vendor that ran (unset, or set to something that isn't a model id), "
                f"so the review used {vendor_note} at that CLI's default model. "
                f"Nothing here checked what that model was. Set "
                f"`pair_loop.fallback_models` in `~/.100xprism/config.json` to pin one."
            )
        lines += [warning, ""]

    if manifest.get("reviewer_same_model_conflict"):
        # Unlike the fallback warning above, this one IS founded: cmd_review
        # resolved both the coder's and the reviewer's session transcripts to
        # the same model and refused the resulting approval (#93) rather than
        # letting it pass as a second opinion.
        lines += [
            f"> 🛑 **Same-model review refused (#93).** A reviewer round "
            f"resolved to the coder's own model "
            f"(`{manifest['reviewer_same_model_conflict']}`) via session "
            "transcripts, and its approval was overridden to "
            "`CHANGES_REQUESTED` rather than counted as an independent "
            "second opinion — see the round list below.",
            "",
        ]

    for r in reviewer_rounds:
        lines.append(
            f"- Round {r['n']} — {r.get('findings', 0)} finding(s), "
            f"{r.get('verdict', 'no verdict')}"
        )

    return "\n".join(lines) + "\n"


def post_pr_comment(pr, body, cwd, run=None):
    """Post `body` to PR `pr` via gh. Returns True on success.

    A failed post must not fail the run: the review itself already happened and
    is recorded in the manifest, so a network blip or a missing gh should warn,
    not discard the result.
    """
    run = run or (lambda cmd, **kw: subprocess.run(cmd, **kw))
    try:
        result = run(["gh", "pr", "comment", str(pr), "--body", body],
                     cwd=cwd, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"warning: could not post review summary to PR #{pr}: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"warning: could not post review summary to PR #{pr}: "
              f"{(result.stderr or '').strip()}", file=sys.stderr)
        return False
    return True


def cmd_finish(args):
    manifest = run_manifest.load_manifest(args.run)
    # `finish` is called twice in the real workflow: once right after
    # approval (no --pr yet — the PR doesn't exist), and again with --pr once
    # it's actually opened (see modules/pair-loop/SKILL.md Step 5). Only
    # overwrite manifest["pr"] when a value was actually passed — otherwise a
    # bare re-run (or the first, PR-less call) would clobber an already
    # recorded PR number back to None.
    if args.pr is not None:
        manifest["pr"] = args.pr
    run_manifest.close_run(manifest, args.verdict, merged=None)

    handoff_path = os.path.join(manifest["cwd"], handoff.HANDOFF_FILENAME)
    transcript = open(handoff_path, encoding="utf-8").read() if os.path.exists(handoff_path) else ""
    body = (
        f"## {manifest['task']}\n\n"
        f"Pair-loop run `{manifest['run_id']}` — coder: {manifest['coder']}, "
        f"reviewer: {manifest['reviewer']}"
        + (" (fallback used)" if manifest.get("reviewer_fallback") else "") + "\n\n"
        f"<details><summary>Full handoff transcript ({manifest['outcome']['rounds']} rounds)</summary>\n\n"
        f"```\n{transcript}\n```\n\n</details>\n"
    )
    body_path = os.path.join(os.path.dirname(run_manifest.manifest_path(manifest["run_id"])),
                             f"{manifest['run_id']}-pr-body.md")
    with open(body_path, "w", encoding="utf-8") as f:
        f.write(body)

    # Only once the PR exists (the second `finish` call — see the comment above)
    # is there somewhere to post the summary.
    summary_posted = False
    if manifest.get("pr") is not None and not args.no_comment:
        summary_posted = post_pr_comment(manifest["pr"], render_review_summary(manifest),
                                         manifest["cwd"])

    print(json.dumps({"pr_body_path": body_path, "summary_posted": summary_posted}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--task", required=True)
    p_start.add_argument("--cwd", default=None)
    p_start.set_defaults(func=cmd_start)

    p_budget = sub.add_parser("budget-check")
    p_budget.add_argument("--run", required=True)
    p_budget.set_defaults(func=cmd_budget_check)

    p_coder_done = sub.add_parser("coder-done")
    p_coder_done.add_argument("--run", required=True)
    p_coder_done.add_argument("--summary", required=True)
    p_coder_done.add_argument("--findings-addressed", type=int, default=0)
    p_coder_done.set_defaults(func=cmd_coder_done)

    p_review = sub.add_parser("review")
    p_review.add_argument("--run", required=True)
    p_review.add_argument("--reviewer-cmd", default=None,
                          help="JSON array override for testing (bypasses codex/claude auto-detect)")
    p_review.set_defaults(func=cmd_review)

    p_finish = sub.add_parser("finish")
    p_finish.add_argument("--run", required=True)
    p_finish.add_argument("--verdict", required=True, choices=["APPROVED", "CHANGES_REQUESTED"])
    p_finish.add_argument("--pr", type=int, default=None)
    p_finish.add_argument("--no-comment", action="store_true",
                          help="skip posting the review summary to the PR")
    p_finish.set_defaults(func=cmd_finish)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

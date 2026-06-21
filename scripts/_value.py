#!/usr/bin/env python3
"""
_value.py — tool-agnostic "what shipped" value layer + per-directory value cache.

Single source of truth for the VALUE side of token & value economics, used by the
token dashboard (web UI) and value-report.py (CLI). It derives value automatically
from git history and filesystem activity per real directory — no CHANGELOG, no manual
registration. The on-disk cache at ~/.100xprism/value.json is keyed by real directory.

Offline, no third-party dependencies.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")

STORE_DIR = os.path.join(HOME, ".100xprism")
STORE_PATH = os.path.join(STORE_DIR, "value.json")
STORE_VERSION = 2

# The dashboard turns a transcript dir like "-Users-rajit-foo-bar" into a label.
_HOME_DASH = HOME.replace("/", "-") + "-"  # e.g. "-Users-rajit-"


# ----------------------------------------------------------------- labels

def _label_from_dirname(dirname):
    return dirname.replace(_HOME_DASH, "~/").replace("-", "/")


def project_label(transcript_path):
    """Readable project name for a transcript path under ~/.claude/projects/."""
    if "/projects/" in transcript_path:
        dirname = transcript_path.split("/projects/")[1].split("/")[0]
    else:
        dirname = transcript_path
    return _label_from_dirname(dirname)


def project_label_for_path(repo_abs_path):
    """The dashboard label a repo at this filesystem path would get."""
    abs_path = os.path.abspath(os.path.expanduser(repo_abs_path))
    return _label_from_dirname(abs_path.replace("/", "-"))

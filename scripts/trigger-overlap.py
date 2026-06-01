#!/usr/bin/env python3
"""Deterministic trigger-overlap lint (issue #24).

Skills auto-trigger off their `description`. When two descriptions share too much
trigger vocabulary, the wrong skill fires. This catches that without an LLM: it builds a
trigger-token set per module (significant words + quoted trigger phrases), scores every
pair by an overlap coefficient, and flags pairs at/over a threshold.

Known-and-accepted overlaps (the CRO family, the lifecycle cluster, etc.) live in
scripts/trigger-overlap-allow.txt so they're acknowledged, not noise. The lint still
*reports* every flagged pair; `--strict` exits non-zero only on pairs that are NOT
allow-listed — i.e. new overlaps a change just introduced.

Usage:
  python3 scripts/trigger-overlap.py [--threshold 0.14] [--strict] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULES_DIR = REPO / "modules"
ALLOW_FILE = Path(__file__).resolve().parent / "trigger-overlap-allow.txt"

# Common English + cross-cutting skill words that carry no triggering signal.
STOPWORDS = {
    "the", "and", "for", "with", "use", "uses", "user", "users", "when", "wants", "want",
    "also", "this", "that", "your", "you", "from", "into", "about", "what", "which", "should",
    "mentions", "see", "whenever", "someone", "needs", "need", "help", "helps", "their", "them",
    "they", "have", "has", "are", "not", "but", "any", "all", "one", "two", "three", "four",
    "five", "more", "than", "just", "like", "isn", "won", "doesn", "don", "how", "why", "who",
    "where", "these", "those", "some", "each", "per", "via", "out", "set", "get", "make", "run",
    "running", "using", "based", "across", "every", "most", "much", "many", "such", "etc",
    "page", "pages", "work", "working", "thing", "things", "way", "ways", "good", "better",
    "best", "new", "now", "after", "before", "over", "under", "between",
}
WORD_RE = re.compile(r"[a-z][a-z0-9-]{2,}")
QUOTE_RE = re.compile(r"['\"]([^'\"]{3,40})['\"]")


def split_frontmatter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fm: dict[str, str] = {}
    current = None
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if line.startswith(" ") and current:
            fm[current] = (fm[current] + " " + line.strip()).strip()
        elif ":" in line:
            k, _, v = line.partition(":")
            current = k.strip()
            fm[current] = v.strip()
    return fm


def trigger_tokens(description: str) -> tuple[set, set]:
    """(word tokens, quoted trigger phrases) — both lowercased, stopwords removed."""
    desc = description.lower()
    phrases = {p.strip() for p in QUOTE_RE.findall(desc) if p.strip()}
    words = {w for w in WORD_RE.findall(desc) if w not in STOPWORDS}
    # fold quoted phrases' own words into the word set too
    for p in phrases:
        words |= {w for w in WORD_RE.findall(p) if w not in STOPWORDS}
    return words, phrases


def overlap_score(a: tuple[set, set], b: tuple[set, set]) -> float:
    """Overlap coefficient (|A∩B| / min(|A|,|B|)) blended with a quoted-phrase bonus.

    Overlap coefficient — not Jaccard — because trigger descriptions vary wildly in
    length; we care whether the *smaller* skill's triggers largely fall inside the
    other's, which is exactly when the wrong skill fires."""
    wa, pa = a
    wb, pb = b
    if not wa or not wb:
        return 0.0
    word = len(wa & wb) / min(len(wa), len(wb))
    phrase = (len(pa & pb) / min(len(pa), len(pb))) if (pa and pb) else 0.0
    return round(0.75 * word + 0.25 * phrase, 4)


def load_allow() -> set:
    pairs = set()
    if ALLOW_FILE.exists():
        for line in ALLOW_FILE.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = re.split(r"\s*(?:<->|,|\s)\s*", line)
            parts = [p for p in parts if p]
            if len(parts) == 2:
                pairs.add(tuple(sorted(parts)))
    return pairs


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="trigger-overlap lint")
    ap.add_argument("--threshold", type=float, default=0.14,
                    help="flag module pairs scoring at/above this (default 0.14)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on flagged pairs that are NOT allow-listed")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])

    tokens = {}
    for sf in sorted(MODULES_DIR.glob("*/SKILL.md")):
        fm = split_frontmatter(sf.read_text())
        desc = fm.get("description", "")
        if desc:
            tokens[sf.parent.name] = trigger_tokens(desc)

    allow = load_allow()
    flagged = []
    for a, b in combinations(sorted(tokens), 2):
        score = overlap_score(tokens[a], tokens[b])
        if score >= args.threshold:
            flagged.append({
                "pair": [a, b],
                "score": score,
                "allowed": tuple(sorted((a, b))) in allow,
            })
    flagged.sort(key=lambda f: f["score"], reverse=True)
    unallowed = [f for f in flagged if not f["allowed"]]

    if args.json:
        print(json.dumps({"threshold": args.threshold, "flagged": flagged}, indent=2))
    else:
        print(f"trigger-overlap: {len(flagged)} pair(s) ≥ {args.threshold} "
              f"({len(flagged) - len(unallowed)} allow-listed, {len(unallowed)} new)\n")
        for f in flagged:
            tag = "  (allow-listed)" if f["allowed"] else "  ⚠ NEW"
            print(f"  {f['score']:.3f}  {f['pair'][0]} ⇄ {f['pair'][1]}{tag}")
        if unallowed and not args.strict:
            print("\n  Add intended overlaps to scripts/trigger-overlap-allow.txt, "
                  "or differentiate the descriptions.")

    if args.strict and unallowed:
        sys.stderr.write(f"\ntrigger-overlap: {len(unallowed)} new overlapping pair(s) "
                         f"not in the allow-list\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

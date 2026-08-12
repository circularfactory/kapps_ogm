#!/usr/bin/env python3
"""Five hygiene checks that gate a public open-source release.

Vendored from `kapps_semantic_middleware` (its #110 chose vendoring over sharing: the repos
release on their own schedules), by way of the `kapps_triplestore_interface` copy, which is
where the two-host `PRIVATE_HOSTS` and the `.git`-pruning walk below were written.

**There are five checks here and six there, and the missing one is the rename.** That check
belongs to a repository whose import package changes on the way out. This one's does not:
`kapps_ogm` has been the import package since the beginning and `kapps-ogm` has been the
distribution since 0.1.1, so a rename check would have nothing to prove and would be a gate
that can only ever pass -- which is worse than no gate, because it reads as one.

The old *dependency* name is a fair question and the answer is the same. `graph_db_interface`
was swept out of this tree by kapps_semantic_middleware#133 and appears in **no code** here --
this sentence and the CHANGELOG's account of the sweep are prose about it, which is why the
claim is worth stating carefully rather than as "no file". A stale import of it would fail at
import time, and `prepare_release.py`'s probe already walks every module of the built wheel to
catch exactly that, against the artifact rather than against the tree.

Run from the repo root:

    python scripts/release_checks.py [TREE]                 # checks 2-5 over TREE (default ".")
    python scripts/release_checks.py [TREE] --origin URL    # additionally run check 1

Exit code 0 if all checks pass, 1 otherwise. Nothing is modified.

This file is copied verbatim into the public release repository and run there by
`.github/workflows/hygiene.yml`. It must never import from the rest of the project —
standard library only, self-contained.
"""

from __future__ import annotations

import argparse
import re
import subprocess

from pathlib import Path, PurePosixPath
from typing import NamedTuple

# -----------------------------------------------------------------------------
# The banned strings, assembled from fragments.
#
# This module ships into the public repo, where checks 4 and 5 scan every file in
# the tree -- this one included. Spelling either value out as a plain literal would
# make the checker fail on its own source. Assembling them is what lets both checks
# stay absolute: no exemption list, no self-skip, nothing to keep in step.
#
# DO NOT join these concatenations back into single strings, and do not quote the
# assembled values anywhere in this file -- comments and docstrings are scanned too.
# test_release_checks.py reads this file's own source and asserts neither value
# appears in it, so a tidy-up fails there rather than silently at release time.
# -----------------------------------------------------------------------------

PRIVATE_HOSTS: tuple[str, ...] = (
    "gitlab" + "." + "kit" + "." + "edu",
    "iam-mms" + "." + "kit" + "." + "edu",
)
"""The private hosts that must appear nowhere in a public tree.

Index 0 is the private Git host -- where this library is developed from #119 onward, and the
one the sibling repos already ban. Index 1 is the institute's GraphDB, and **it is the one that
has actually fired here**: the conjunctive-range entry in `CHANGELOG.md` quoted the endpoint it
was measured against, and all seven `scripts/demo_*.py` hardcode it as their `base_url`. That
is not a credential, and it is behind KIT authentication, but an internal service address in a
public changelog is a thing somebody notices later rather than never.

`@kit.edu` in an author's e-mail address is deliberately *not* banned. The bare domain is
public and the addresses are the credit this release is required to carry; only these two
named service hosts are private.
"""

DEAD_REFERENCE_DIRS: tuple[str, ...] = (
    "docs/" + "adr/",
    "docs/" + "prd/",
)
"""Record directories that never ship, so a pointer at one is dead on arrival.

Index 0 is the decision records, index 1 the requirements documents. **This repository has the
second of them**, holding the specifications handed to it, and `CHANGELOG.md` cited a document
inside it by full path three times, in entries that ship. No reader of the public tree can open
any of them.

Neither directory is named literally here, for the reason at the top of this file: this module
ships, check 5 scans it, and a plain literal in this docstring makes the checker report itself.
It did, on the first run of `test_the_shipped_checker_passes_its_own_checks`.
"""

BANNED_PATH_FRAGMENTS: tuple[str, ...] = (
    "docs/agents/",
    ".claude/",
    ".gitlab-ci.yml",
    "RELEASE.md",
    "tests_release/",
)
"""Dev-only path fragments that must never appear in a public release tree.

`.claude/` has a leading dot and trailing slash precisely so it cannot match a `CLAUDE.md` at
the root. `.gitlab-ci.yml` names the private forge in its filename alone, so check 4 -- which
reads contents -- would never see it.

`scripts/` is deliberately absent: `release_checks.py` is the one file out of that directory
that ships, and a fragment banning the directory would ban this file. The demo scripts that
share the directory with it are excluded by the allowlist and caught by check 4 besides.
"""


class Violation(NamedTuple):
    """A single hygiene violation."""

    check: str
    """Short check name: remotes, trailers, paths, secrets, or references."""

    path: str
    """Repo-relative POSIX path of the offending file; empty string for repo-level findings."""

    detail: str
    """One line a human can act on."""


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo``, returning stdout stripped."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def check_remotes(repo: Path, expected_origin: str) -> list[Violation]:
    """Check 1 — remotes.

    Pass only when exactly one remote is configured, named ``origin``, with a URL
    matching ``expected_origin``. Comparison strips trailing ``.git`` and ``/`` from
    both sides. A second remote, wrong origin URL, or missing origin are violations.
    """

    def _normalize(url: str) -> str:
        """Strip trailing .git and / for comparison."""
        url = url.removesuffix(".git")
        return url.rstrip("/")

    violations: list[Violation] = []
    expected_norm = _normalize(expected_origin)

    try:
        raw = _git(repo, "remote", "-v")
    except subprocess.CalledProcessError:
        return [Violation("remotes", "", "no remotes configured")]

    # `git remote -v` prints one line per direction:
    #
    #     origin  https://host/repo.git (fetch)
    #     origin  https://host/repo.git (push)
    #
    # and `git remote set-url --push` changes only the second. Reading just the first line per
    # remote would see the right address and pass while every push went elsewhere -- which is
    # the exact accident this check exists to catch. So every line is kept and checked.
    remotes: dict[str, list[tuple[str, str]]] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        direction = parts[2].strip("()") if len(parts) > 2 else "fetch"
        remotes.setdefault(name, []).append((url, direction))

    if "origin" not in remotes:
        violations.append(Violation("remotes", "", "no origin remote configured"))
    else:
        for url, direction in remotes["origin"]:
            if _normalize(url) != expected_norm:
                violations.append(
                    Violation(
                        "remotes",
                        "",
                        f"origin {direction} URL is {url}, expected {expected_origin}",
                    )
                )

    for name in sorted(remotes):
        if name != "origin":
            url = remotes[name][0][0]
            violations.append(
                Violation("remotes", "", f"extra remote '{name}' configured at {url}")
            )

    return violations


def check_trailers(repo: Path) -> list[Violation]:
    """Check 2 — trailers. No agent co-authorship in the public log.

    Scans every commit reachable from HEAD. A commit violates when its message
    contains ``Co-Authored-By:`` naming Claude, or the phrase ``Generated with``.
    Both matched case-insensitively. Human co-authors do NOT trigger this — only
    the agent one.
    """
    violations: list[Violation] = []

    try:
        raw = _git(
            repo,
            "log",
            "--format=%H%x1f%B%x1e",
        )
    except subprocess.CalledProcessError:
        return violations

    if not raw.strip():
        return violations

    for record in raw.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f", 1)
        if len(parts) != 2:
            continue
        sha, message = parts
        short_sha = sha[:7]

        if re.search(r"co-authored-by:.*claude", message, re.IGNORECASE):
            violations.append(
                Violation(
                    "trailers",
                    "",
                    f"{short_sha} has Co-Authored-By naming Claude",
                )
            )
        elif re.search(r"generated with", message, re.IGNORECASE):
            violations.append(
                Violation(
                    "trailers",
                    "",
                    f"{short_sha} has 'Generated with' marker",
                )
            )

    return violations


def _walk_files(tree: Path) -> list[tuple[Path, str]]:
    """Every file under ``tree`` as (absolute path, repo-relative POSIX path), sorted.

    ``.git/`` is pruned during the walk rather than filtered afterwards. It holds hooks,
    config and packed objects that are not part of the published tree, and a release clone's
    config legitimately names the origin the checks would otherwise read as content.

    The pruning has to happen while the walk is running -- sorting the walk first would
    materialise it, and assigning to ``dirs[:]`` after that changes nothing.
    """
    found: list[tuple[Path, str]] = []
    for root, dirs, files in tree.walk():
        dirs[:] = [d for d in dirs if d != ".git"]
        for fname in files:
            fpath = root / fname
            found.append((fpath, PurePosixPath(fpath.relative_to(tree)).as_posix()))
    return sorted(found, key=lambda pair: pair[1])


def check_paths(tree: Path) -> list[Violation]:
    """Check 3 — paths. Named dev-only files must not exist in the tree.

    A file violates when its repo-relative POSIX path contains any entry of
    ``BANNED_PATH_FRAGMENTS``. ``CLAUDE.md`` at the root is explicitly allowed.
    """
    violations: list[Violation] = []

    for _fpath, rel_str in _walk_files(tree):
        for frag in BANNED_PATH_FRAGMENTS:
            if frag in rel_str:
                violations.append(
                    Violation("paths", rel_str, f"dev-only path fragment '{frag}' found")
                )
                break

    return violations


def _read_text_safe(path: Path) -> str | None:
    """Read a file's text, returning None for a binary or unreadable one.

    Binary is detected as a NUL byte in the first 8 KB. A PNG that happens to contain the
    banned byte sequence is not a leak, and must not crash the scan either.
    """
    try:
        with path.open("rb") as handle:
            if b"\x00" in handle.read(8192):
                return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def check_secrets(tree: Path) -> list[Violation]:
    """Check 4 — secrets. No private host may appear anywhere.

    Walks ``tree``, skipping ``.git/`` and binary files. A file violates once per entry of
    ``PRIVATE_HOSTS`` it contains, so a file naming both is reported for both rather than
    fixed, re-run and reported again.
    """
    violations: list[Violation] = []

    for fpath, rel_str in _walk_files(tree):
        content = _read_text_safe(fpath)
        if content is None:
            continue

        for host in PRIVATE_HOSTS:
            if host in content:
                violations.append(
                    Violation("secrets", rel_str, f"contains private host '{host}'")
                )

    return violations


def check_references(tree: Path) -> list[Violation]:
    """Check 5 — references. No shipped file points at a decision-record directory.

    Walks ``tree``, skipping ``.git/`` and binary files. A file violates when its
    text contains either entry of ``DEAD_REFERENCE_DIRS``. Both shapes fail: a full
    path with a filename, and a bare directory named in prose. A bare citation like
    ``ADR 0012`` does NOT fire — it names no directory.
    """
    violations: list[Violation] = []

    for fpath, rel_str in _walk_files(tree):
        content = _read_text_safe(fpath)
        if content is None:
            continue

        for dead_dir in DEAD_REFERENCE_DIRS:
            if dead_dir in content:
                violations.append(
                    Violation("references", rel_str, f"references dead directory '{dead_dir}'")
                )
                break

    return violations


def run_tree_checks(tree: Path) -> list[Violation]:
    """Run checks 3, 4, 5 — the tree scans that apply in CI.

    Check 1 (remotes) and 2 (trailers) are about local clone state and history;
    they mean nothing in a CI checkout, so this runner excludes them.
    """
    violations: list[Violation] = []
    violations.extend(check_paths(tree))
    violations.extend(check_secrets(tree))
    violations.extend(check_references(tree))
    return violations


def run_ci_checks(repo: Path) -> list[Violation]:
    """Run checks 2-5 — the backstop the release repo's own CI runs.

    Check 1 (remotes) is the one check a CI checkout cannot answer: the runner's remote is
    whatever `actions/checkout` configured, and says nothing about where a release was pushed
    from. Check 2 is different, and is exactly why this runner exists rather than
    ``run_tree_checks``: a commit made **directly in the public repo** never passes the
    pre-push gate, and an agent trailer on it is the leak the gate cannot see. The workflow
    checks out with ``fetch-depth: 0`` so the whole history is there to scan.

    On a tree that is not a git repository, ``check_trailers`` finds no history and returns
    nothing, so this degrades to the tree scans rather than failing.
    """
    violations: list[Violation] = []
    violations.extend(check_trailers(repo))
    violations.extend(run_tree_checks(repo))
    return violations


def run_all(repo: Path, expected_origin: str) -> list[Violation]:
    """Run all five checks (1-5).

    Used for the pre-push gate in the private repo, where the full history and
    remote configuration are available.
    """
    violations: list[Violation] = []
    violations.extend(check_remotes(repo, expected_origin))
    violations.extend(run_ci_checks(repo))
    return violations


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Supports::

        python scripts/release_checks.py [TREE]                    # checks 2-5
        python scripts/release_checks.py [TREE] --origin URL       # checks 1-5

    Without ``--origin`` this is the release repo's CI backstop, which
    kapps_semantic_middleware#110 specifies as checks 2-5. Only check 1 needs the expected
    origin, so only check 1 waits for it.
    """
    parser = argparse.ArgumentParser(
        description="Hygiene checks for a public release tree.",
    )
    parser.add_argument("tree", nargs="?", default=".", help="Tree to check (default: .)")
    parser.add_argument(
        "--origin",
        dest="origin",
        default=None,
        help="Expected origin URL. Adds check 1; without it, checks 2-5 run.",
    )

    args = parser.parse_args(argv)

    tree = Path(args.tree).resolve()
    violations: list[Violation] = []

    if args.origin:
        violations = run_all(tree, args.origin)
    else:
        violations = run_ci_checks(tree)

    for v in violations:
        print(f"{v.check}  {v.path}  {v.detail}")

    if violations:
        print(f"\n{len(violations)} violation(s) found")
        return 1
    else:
        print("\nAll checks passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

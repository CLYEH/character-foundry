"""PR CI guard — a `[tool.mutmut]` change must re-baseline the kill rate (T-065).

The nightly mutation workflow compares `mutmut run`'s kill rate against
`.harness/mutation-baseline.json`. That baseline is only meaningful for the
exact mutation surface it was measured on — the `paths_to_mutate` / `tests_dir`
/ `mutate_only_covered_lines` / `also_copy` config in `api/pyproject.toml`
plus the pinned `mutmut` version. Change the surface without re-measuring and
the very next nightly opens a false `mutation-drift` issue.

This script enforces that contract at PR time: if the `[tool.mutmut]` config
(order-insensitive) or the `mutmut` dependency pin changed in this PR but
`.harness/mutation-baseline.json` was not also updated, the PR fails — unless a
commit message declares the change `baseline-irrelevant`.

It is the structural version of the rule that previously lived only in the
baseline JSON `notes` field and a `[tool.mutmut]` comment (T-065 Scope).

Exit codes:
    0   `[tool.mutmut]` + mutmut pin unchanged, OR they changed and the
        baseline JSON was updated in the same diff, OR a commit declared
        `baseline-irrelevant`.
    1   `[tool.mutmut]`/mutmut changed without re-baselining and no escape
        hatch — actionable hint on stderr.
    2   git plumbing failed (base ref not fetched — see `fetch-depth: 0`).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

# `<root>/api/scripts/check_baseline_resync.py` → parents[2] == repo root.
# Resolved from `__file__` so the script is cwd-independent: CI invokes it from
# the repo root, the pytest subprocess smoke from `api/`, both must agree.
REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_REL = "api/pyproject.toml"
BASELINE_REL = ".harness/mutation-baseline.json"
ESCAPE_HATCH = "baseline-irrelevant"


def _canonical(value: Any) -> Any:
    """Recursively normalize so set-equal config compares equal.

    `json.dumps(sort_keys=True)` only sorts dict keys, not list elements —
    using it directly would treat reordering `paths_to_mutate` as a content
    change, violating the "these are sets" intent. So we sort lists too,
    keying nested elements by their own canonical serialization to stay safe
    on heterogeneous lists.
    """
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return sorted(
            (_canonical(x) for x in value),
            key=lambda x: json.dumps(x, sort_keys=True),
        )
    return value


def _pkg_name(spec: str) -> str:
    """`'mutmut[fast]>=3.0'` → `'mutmut'` (leading name token, lowercased)."""
    match = re.match(r"\s*([A-Za-z0-9._-]+)", spec)
    return match.group(1).lower() if match else ""


def _fingerprint(pyproject_text: str) -> tuple[str, tuple[str, ...]]:
    """Return the baseline-sensitive fingerprint of a pyproject's contents.

    A pair of (canonical `[tool.mutmut]` JSON, sorted `mutmut` dep specs). Two
    fingerprints comparing equal means nothing the baseline depends on moved.
    A pyproject that can't be parsed (e.g. the file did not exist at the base
    ref) yields an empty fingerprint, which deliberately differs from any real
    config so the guard errs toward asking for a re-baseline.
    """
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError:
        return ("{}", ())
    mutmut_config = data.get("tool", {}).get("mutmut", {})
    canonical_config = json.dumps(_canonical(mutmut_config), sort_keys=True)
    dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    mutmut_specs = tuple(sorted(s for s in dev_deps if _pkg_name(s) == "mutmut"))
    return (canonical_config, mutmut_specs)


def _failure_message() -> str:
    return (
        "::error::[tool.mutmut] config (or the mutmut version pin) changed but "
        f"{BASELINE_REL} was not updated in this diff.\n"
        "A baseline measured on a different mutation surface is not a fair "
        "comparison — tomorrow's nightly will open a false `mutation-drift` issue.\n"
        "\n"
        "Resolve it one of two ways:\n"
        "  (a) Re-baseline. From api/ run:\n"
        "        mutmut run && mutmut export-cicd-stats\n"
        "      then read killed / (total - no_tests - skipped) from\n"
        "      mutants/mutmut-cicd-stats.json and write it into\n"
        f"      {BASELINE_REL} 's `baseline_kill_rate` (commit the change).\n"
        "  (b) If this change cannot affect the kill rate (a comment edit or a\n"
        "      pure reordering), put the string 'baseline-irrelevant' in a\n"
        "      commit message in this PR to acknowledge it."
    )


def check(
    base_pyproject: str,
    head_pyproject: str,
    *,
    baseline_changed: bool,
    commit_messages: str,
) -> tuple[bool, str]:
    """Decide whether the diff satisfies the re-baseline contract.

    Returns `(ok, message)`. `ok=True` → exit 0 with an informational message;
    `ok=False` → exit 1 with the actionable hint.
    """
    if _fingerprint(base_pyproject) == _fingerprint(head_pyproject):
        return (True, "baseline-resync OK - [tool.mutmut] config + mutmut pin unchanged.")

    if baseline_changed:
        return (
            True,
            f"baseline-resync OK - [tool.mutmut]/mutmut changed and {BASELINE_REL} "
            "was updated in the same diff.",
        )

    if ESCAPE_HATCH in commit_messages:
        return (
            True,
            "baseline-resync OK - [tool.mutmut]/mutmut changed but a commit "
            f"declared '{ESCAPE_HATCH}'.",
        )

    return (False, _failure_message())


def _git(args: list[str]) -> str:
    # `encoding="utf-8"` is load-bearing, not cosmetic: git emits UTF-8, but
    # `text=True` alone decodes with the locale codec (cp950 on Windows),
    # which dies on the non-ASCII bytes in pyproject.toml's comments. CI is
    # UTF-8 so it would pass there and only break on a dev's machine.
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout


def _git_show(ref_path: str) -> str:
    """`git show <ref>:<path>`; empty string if the path didn't exist there."""
    proc = subprocess.run(
        ["git", "show", ref_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _gather_from_git(base_ref: str) -> tuple[str, str, bool, str]:
    """Collect the inputs `check()` needs from git, three-dot semantics.

    `git diff A...HEAD` == diff from `merge-base(A, HEAD)`, so we resolve the
    merge base once and compare everything against it. That avoids a false
    positive when main moved ahead with its own `[tool.mutmut]` edits that this
    PR never touched.
    """
    merge_base = _git(["merge-base", base_ref, "HEAD"]).strip()
    base_text = _git_show(f"{merge_base}:{PYPROJECT_REL}")
    head_text = _git_show(f"HEAD:{PYPROJECT_REL}")
    changed = {
        line.strip()
        for line in _git(["diff", "--name-only", merge_base, "HEAD"]).splitlines()
        if line.strip()
    }
    commit_messages = _git(["log", "--format=%B", f"{merge_base}..HEAD"])
    return base_text, head_text, BASELINE_REL in changed, commit_messages


def main(
    argv: list[str] | None = None,
    *,
    base_pyproject: str | None = None,
    head_pyproject: str | None = None,
    baseline_changed: bool | None = None,
    commit_messages: str | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Base ref to diff HEAD against (default: origin/main).",
    )
    args = parser.parse_args(argv)

    # Injected inputs (tests) bypass git entirely.
    if base_pyproject is not None:
        ok, message = check(
            base_pyproject,
            head_pyproject or "",
            baseline_changed=bool(baseline_changed),
            commit_messages=commit_messages or "",
        )
    else:
        try:
            base_text, head_text, baseline_was_changed, messages = _gather_from_git(args.base_ref)
        except subprocess.CalledProcessError as exc:
            print(
                f"::error::git failed resolving '{args.base_ref}' against HEAD: "
                f"{exc.stderr.strip() if exc.stderr else exc}. "
                "The base branch must be fetched — set `fetch-depth: 0` on the "
                "checkout (or `git fetch origin <base>`) before running this guard.",
                file=sys.stderr,
            )
            return 2
        ok, message = check(
            base_text,
            head_text,
            baseline_changed=baseline_was_changed,
            commit_messages=messages,
        )

    if ok:
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

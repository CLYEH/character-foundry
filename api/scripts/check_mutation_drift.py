"""Mutation kill-rate drift checker (T-060, Harness A3).

Compares the kill rate from `mutants/mutmut-cicd-stats.json` (produced by
`mutmut export-cicd-stats`) against the committed baseline in
`.harness/mutation-baseline.json`. Writes a structured drift report to
stdout that the nightly workflow uses both for log output and as the body
of the auto-filed `mutation-drift` issue.

Exit codes:
    0   no baseline change yet (first run) OR kill rate stays within
        `baseline_kill_rate - drift_threshold_pp` of the recorded baseline.
    1   regression — kill rate dropped by more than the configured
        threshold; nightly workflow turns this into a `mutation-drift`
        issue.
    2   missing/malformed input — `mutmut export-cicd-stats` was never run
        in this workflow, the JSON is unreadable, or no mutants were
        evaluated (every mutant ended up in `no_tests` / `skipped`). This
        is its own failure mode so the workflow can distinguish "no
        signal" from "kill rate dropped".

The script intentionally takes `--stats` and `--baseline` as path
arguments rather than guessing locations, so the GitHub Actions workflow
(working directory `api/`) and local dogfooding (working directory
`api/` inside the docker container) can pass identical relative paths.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunStats:
    killed: int
    survived: int
    total: int
    no_tests: int
    skipped: int
    suspicious: int
    timeout: int
    segfault: int

    @property
    def evaluable_total(self) -> int:
        """Mutants that produced an actual verdict.

        `no_tests` means the mutated line had no test touching it — that's
        a coverage gap, not a kill-rate signal. `skipped` is mutmut's own
        bookkeeping for mutants that couldn't be applied. Folding either
        into the denominator drags the score around for reasons unrelated
        to test quality, so we strip them out before computing the rate.
        """
        return self.total - self.no_tests - self.skipped

    @property
    def kill_rate(self) -> float:
        if self.evaluable_total <= 0:
            return 0.0
        return self.killed / self.evaluable_total


def _load_run_stats(path: Path) -> RunStats:
    with path.open() as f:
        raw = json.load(f)
    return RunStats(
        killed=int(raw.get("killed", 0)),
        survived=int(raw.get("survived", 0)),
        total=int(raw.get("total", 0)),
        no_tests=int(raw.get("no_tests", 0)),
        skipped=int(raw.get("skipped", 0)),
        suspicious=int(raw.get("suspicious", 0)),
        timeout=int(raw.get("timeout", 0)),
        segfault=int(raw.get("segfault", 0)),
    )


def _format_report(
    stats: RunStats,
    baseline_kill_rate: float | None,
    drift_threshold_pp: float,
    actual_kill_rate: float,
) -> str:
    delta_line = (
        f"baseline kill rate: {baseline_kill_rate * 100:.2f}%"
        if baseline_kill_rate is not None
        else "baseline kill rate: (none recorded — this run establishes it)"
    )
    return (
        f"Mutmut nightly drift report\n"
        f"---------------------------\n"
        f"  killed     : {stats.killed}\n"
        f"  survived   : {stats.survived}\n"
        f"  no_tests   : {stats.no_tests}   (line had no covering test — not counted)\n"
        f"  skipped    : {stats.skipped}\n"
        f"  suspicious : {stats.suspicious} (worth a manual look — tests behaved oddly)\n"
        f"  timeout    : {stats.timeout}\n"
        f"  segfault   : {stats.segfault}\n"
        f"  total      : {stats.total} mutants generated; "
        f"{stats.evaluable_total} evaluable\n"
        f"\n"
        f"  actual kill rate: {actual_kill_rate * 100:.2f}%\n"
        f"  {delta_line}\n"
        f"  drift threshold : {drift_threshold_pp:.2f} pp\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stats",
        type=Path,
        required=True,
        help="Path to mutmut-cicd-stats.json (produced by `mutmut export-cicd-stats`).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Path to .harness/mutation-baseline.json (committed).",
    )
    args = parser.parse_args(argv)

    if not args.stats.exists():
        print(
            f"::error::Mutation stats file not found: {args.stats}. "
            "Did `mutmut run` produce a `mutants/` directory and "
            "`mutmut export-cicd-stats` succeed?",
            file=sys.stderr,
        )
        return 2

    try:
        stats = _load_run_stats(args.stats)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"::error::Failed to parse {args.stats}: {exc}", file=sys.stderr)
        return 2

    if stats.evaluable_total <= 0:
        print(
            "::error::No evaluable mutants in this run "
            f"(total={stats.total}, no_tests={stats.no_tests}, skipped={stats.skipped}). "
            "Something is wrong with the test selection — every mutant "
            "either had no covering test or mutmut couldn't apply it. "
            "Check `tests_dir` in `[tool.mutmut]` and the workflow's "
            "pytest install step before treating this as drift.",
            file=sys.stderr,
        )
        return 2

    actual_kill_rate = stats.kill_rate

    baseline_kill_rate: float | None = None
    drift_threshold_pp: float = 5.0
    if args.baseline.exists():
        try:
            payload = json.loads(args.baseline.read_text())
            baseline_kill_rate = float(payload["baseline_kill_rate"])
            drift_threshold_pp = float(payload.get("drift_threshold_pp", 5.0))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            print(
                f"::error::Baseline file {args.baseline} is malformed: {exc}. "
                "Expected JSON with at least `baseline_kill_rate` (float).",
                file=sys.stderr,
            )
            return 2

    report = _format_report(stats, baseline_kill_rate, drift_threshold_pp, actual_kill_rate)
    print(report)

    if baseline_kill_rate is None:
        # First run — no baseline to compare against. Emit the run's
        # kill rate so the operator can commit it to `.harness/`.
        print(
            "\nNo baseline recorded yet. To establish one, commit this JSON to "
            "`.harness/mutation-baseline.json`:\n"
            f'  {{"baseline_kill_rate": {actual_kill_rate:.4f}, '
            f'"drift_threshold_pp": 5.0}}\n'
        )
        return 0

    floor = baseline_kill_rate - (drift_threshold_pp / 100.0)
    if actual_kill_rate < floor:
        delta_pp = (baseline_kill_rate - actual_kill_rate) * 100
        print(
            f"\n::error::Mutation kill rate dropped {delta_pp:.2f} pp "
            f"(baseline {baseline_kill_rate * 100:.2f}% → "
            f"actual {actual_kill_rate * 100:.2f}%), exceeding the "
            f"{drift_threshold_pp:.2f} pp drift threshold."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

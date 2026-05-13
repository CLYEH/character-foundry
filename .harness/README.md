# `.harness/` — local harness state

This directory holds **harness-engineering artifacts** that need to outlive a
single PR but don't belong in application code. The contents are mixed:
some files are tracked (acting as committed baselines), others are gitignored
and accumulate locally over time. Per-file scope is below.

Vocabulary follows Martin Fowler's
[*Harness Engineering for Coding Agents*](https://martinfowler.com/articles/harness-engineering.html):
guides feed the agent forward, sensors feed it back. Files here are the
sensor side — their value comes from *time accumulation*, so they need a
stable home.

---

## Files

### `mutation-baseline.json` *(tracked, T-060)*

Frozen kill-rate baseline for `mutmut` over `app/core/errors.py` +
`app/ai/circuit.py`. The nightly mutation workflow (`.github/workflows/mutation.yml`)
compares each run against this number and opens a `mutation-drift` issue
when the kill rate drops more than `drift_threshold_pp`. Update only when
`[tool.mutmut]` scope in `api/pyproject.toml` changes (see the file's own
`notes` field for the procedure).

### `skip-review.log` *(gitignored, T-063)*

Append-only JSONL audit log of `CF_SKIP_REVIEW=1` pre-push bypasses. Each
line is one bypass event:

```json
{"ts":"2026-05-13T18:42:11Z","branch":"docs/fix-typo","range":"origin/docs/fix-typo..HEAD","reason":"docs-only","source":"terminal-hook"}
```

Fields:

| Field | Meaning |
|---|---|
| `ts` | ISO 8601 UTC timestamp of the bypass |
| `branch` | Branch HEAD was on (empty if detached) |
| `range` | Commit range being pushed (`<upstream>..HEAD`, falls back to `origin/main..HEAD`) |
| `reason` | Optional free-text from `CF_SKIP_REVIEW_REASON` env var |
| `source` | `claude-hook` (Claude `PreToolUse` fired) or `terminal-hook` (`.githooks/pre-push` fired) |

Both hooks write through the same `append_skip_log` shell function, so the
schemas stay in lockstep. Failure to write the log is non-fatal — the push
itself is what matters; the log is observability.

**Why this exists.** `CONTRIBUTING.md` §7.1 lists when bypass is legitimate
(hotfix, docs-only, post-review re-push). The risk is *drift*: bypass
quietly becoming the default instead of the exception. The log gives a
quarterly retro something to look at:

```sh
# Bypass count by month
jq -r '.ts[:7]' .harness/skip-review.log | sort | uniq -c

# Reason distribution
jq -r '.reason // "<empty>"' .harness/skip-review.log | sort | uniq -c

# Bypasses on non-docs branches (eyeball for misuse)
jq -r 'select(.branch | test("^docs/") | not) | "\(.ts)\t\(.branch)\t\(.reason)"' .harness/skip-review.log
```

If empty-reason bypasses dominate, or non-`docs/`/`fix/` branches are
bypassing routinely, the review gate is being walked around. That's the
drift signal — surface it in the retro, decide whether the gate or the
norm needs adjusting.

**Why local, not synced.** Bypass cadence is personal-development rhythm,
not PR content. Pushing the log upstream would make every routine bypass
a diff. Aggregate at retro time by sharing summary stats, not the raw log.

---

## Adding new files

If you put something new here, append a section above and note in the
ticket whether the artifact is tracked or gitignored — the directory is
intentionally mixed.

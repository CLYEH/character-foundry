#!/usr/bin/env bash
# Pre-push review gate (Claude-side)
#
# Triggered by a PreToolUse hook on Bash tool calls matching `git push *`.
# Default behavior: deny the push and instruct Claude to spawn the
# `engineering-code-reviewer` subagent first. Bypass with CF_SKIP_REVIEW=1
# (e.g. for hotfixes, docs-only pushes, or when re-running the push after
# the review has been completed and accepted).
#
# T-062: if the current branch's ticket file matches security-sensitive or
# schema-migration keywords, the deny message additionally directs Claude to
# chain `security-engineer` / `db-optimizer` on the same diff.
#
# Output is the structured JSON Claude Code expects from a hook command;
# we keep dependencies to plain bash so this works on Windows too.

set -u

if [ "${CF_SKIP_REVIEW:-0}" = "1" ]; then
  cat <<'JSON'
{"permissionDecision":"allow","additionalContext":"Pre-push review bypass active (CF_SKIP_REVIEW=1)."}
JSON
  exit 0
fi

# ── Ticket detection ────────────────────────────────────────────────────
# Pull the branch name and extract a T-XXX id (case-insensitive). If we can
# find a matching ticket file under tickets/ or tickets/DONE/, grep it for
# the chain triggers.
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || true)
TICKET=$(printf '%s' "${BRANCH:-}" | grep -oiE 'T-[0-9]+' | head -1 || true)
TICKET=$(printf '%s' "${TICKET:-}" | tr '[:lower:]' '[:upper:]')

SEC_CHAIN=0
DB_CHAIN=0

if [ -n "${TICKET:-}" ]; then
  # Anchor ticket lookup to the worktree root so subdirectory invocation
  # (worktrees, `api/`-cwd shells) still resolves the ticket file.
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo .)
  for f in "$REPO_ROOT"/tickets/${TICKET}-*.md "$REPO_ROOT"/tickets/DONE/${TICKET}-*.md; do
    [ -f "$f" ] || continue
    # Strip template scaffold lines that mention OAuth purely as section
    # boilerplate (every ticket carries `## OAuth scope required` from
    # tickets/_TEMPLATE.md — keyword matches there are noise; T-064 was
    # the negative-control reproduction). `-i` keeps the strip robust to
    # template rename casing drift.
    body=$(grep -vi '^## OAuth scope' "$f")
    # Security-sensitive keywords (auth / OAuth / secrets surface).
    if printf '%s\n' "$body" | grep -qiE 'security-sensitive|oauth|\bjwt\b|\bpkce\b|bearer token|authentik|client_secret|refresh[._ ]token|scope decorator|secret scan|sast' ; then
      SEC_CHAIN=1
    fi
    # Schema-migration / DB-shape keywords. `new index` is broad on
    # purpose ("err on the side of chaining" — see CONTRIBUTING §4.4).
    if printf '%s\n' "$body" | grep -qiE 'alembic|alter table|add column|drop column|schema migration|migration script|\bbackfill\b|new index|enum column' ; then
      DB_CHAIN=1
    fi
  done
fi

EXTRA=""
if [ "$SEC_CHAIN" = "1" ]; then
  EXTRA="${EXTRA}"'\n\nADDITIONAL (security-sensitive ticket '"${TICKET}"'): also run the `security-engineer` subagent (subagent_type=security-engineer) on the same diff before pushing.'
fi
if [ "$DB_CHAIN" = "1" ]; then
  EXTRA="${EXTRA}"'\n\nADDITIONAL (schema / migration ticket '"${TICKET}"'): also run the `db-optimizer` subagent (subagent_type=db-optimizer) on the same diff before pushing.'
fi

# ── Emit JSON ───────────────────────────────────────────────────────────
# Build the JSON with a placeholder so the static body (which contains
# backticks and single quotes) isn't subject to bash expansion, then
# substitute the dynamic EXTRA in via bash parameter expansion.
TEMPLATE=$(cat <<'EOT'
{"permissionDecision":"deny","additionalContext":"PRE-PUSH REVIEW GATE\n\nBefore this push proceeds, run the `engineering-code-reviewer` subagent over the about-to-be-pushed changes.\n\nHow to run it:\n  1. Use the Agent tool with subagent_type='engineering-code-reviewer'.\n  2. In the prompt, hand the agent the diff vs upstream:\n     `git fetch origin && git diff origin/<base-branch>...HEAD`\n     plus a pointer to the ticket / PR scope.\n  3. The agent returns 🔴 blockers / 🟡 suggestions / 💭 nits.\n\nAfter the review:\n  - If clean and the user accepts → retry with `CF_SKIP_REVIEW=1` prefixed, e.g. `CF_SKIP_REVIEW=1 git push`.\n  - If blockers → address them, commit, re-run the gate.\n\nIntentional bypass (hotfix, docs-only, etc.): prefix the push with `CF_SKIP_REVIEW=1`.{{EXTRA}}"}
EOT
)

printf '%s\n' "${TEMPLATE/'{{EXTRA}}'/$EXTRA}"
exit 0

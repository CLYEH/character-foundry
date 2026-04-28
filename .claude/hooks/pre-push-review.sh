#!/usr/bin/env bash
# Pre-push review gate (Claude-side)
#
# Triggered by a PreToolUse hook on Bash tool calls matching `git push *`.
# Default behavior: deny the push and instruct Claude to spawn the
# `engineering-code-reviewer` subagent first. Bypass with CF_SKIP_REVIEW=1
# (e.g. for hotfixes, docs-only pushes, or when re-running the push after
# the review has been completed and accepted).
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

cat <<'JSON'
{"permissionDecision":"deny","additionalContext":"PRE-PUSH REVIEW GATE\n\nBefore this push proceeds, run the `engineering-code-reviewer` subagent over the about-to-be-pushed changes.\n\nHow to run it:\n  1. Use the Agent tool with subagent_type='engineering-code-reviewer'.\n  2. In the prompt, hand the agent the diff vs upstream:\n     `git fetch origin && git diff origin/<base-branch>...HEAD`\n     plus a pointer to the ticket / PR scope.\n  3. The agent returns 🔴 blockers / 🟡 suggestions / 💭 nits.\n\nAfter the review:\n  - If clean and the user accepts → retry with `CF_SKIP_REVIEW=1` prefixed, e.g. `CF_SKIP_REVIEW=1 git push`.\n  - If blockers → address them, commit, re-run the gate.\n\nIntentional bypass (hotfix, docs-only, etc.): prefix the push with `CF_SKIP_REVIEW=1`."}
JSON
exit 0

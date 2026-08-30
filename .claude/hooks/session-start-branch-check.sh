#!/bin/sh
# SessionStart hook: report the current git branch, and warn about states that
# have caused real problems in this repo - working directly on main, and
# uncommitted or unpushed changes left over from a previous session.
set -eu

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root"

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
  exit 0
fi

warnings=""

if [ "$branch" = "main" ]; then
  warnings="${warnings}- main で直接作業しようとしています。作業ブランチを checkout してください。
"
fi

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  warnings="${warnings}- コミットされていない変更が残っています（前回のセッションの続きかもしれません）。
"
fi

upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "")
if [ -n "$upstream" ]; then
  ahead=$(git rev-list --count "$upstream..HEAD" 2>/dev/null || echo 0)
  if [ "$ahead" -gt 0 ]; then
    warnings="${warnings}- ${upstream} に push されていないコミットが ${ahead} 件あります。
"
  fi
fi

message="現在のブランチ: ${branch}"
if [ -n "$warnings" ]; then
  message="${message}

注意:
${warnings}"
fi

jq -n --arg msg "$message" \
  '{systemMessage: $msg, hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $msg}}'

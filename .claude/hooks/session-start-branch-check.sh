#!/bin/sh
# SessionStart hook: report the current git branch, and warn about states that
# have caused real problems in this repo - working directly on main, and
# uncommitted or unpushed changes left over from a previous session.
#
# This used to build its JSON with jq, and jq is not installed on the machine
# the hook actually runs on. Under `set -eu` it exited 127 and printed nothing,
# so every warning it exists to raise had been silently missing. Nobody knows
# since when. It only calls git now; tests/test_hooks.py holds that line.
#
# Keep " and \ out of the fixed prose below: it is interpolated into JSON by
# printf, with no escaping. Values that come from outside - branch names, the
# upstream name - go through json_escape, because a branch name IS allowed to
# contain a double quote.
set -eu

# Never die silently. Whatever goes wrong next, say so rather than print
# nothing: printing nothing is indistinguishable from having no warnings, and
# that is how this went unnoticed.
broken='{"systemMessage":"SessionStart フックが途中で落ちました。枝の警告は出ていません。.claude/hooks/session-start-branch-check.sh を見てください。","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"SessionStart フックが途中で落ちました。枝の警告は出ていません。"}}'
trap 'printf "%s\n" "$broken"' EXIT

# Leave without the failure notice - used where having nothing to say is the
# right answer (not a repo, detached HEAD).
quiet_exit() {
  trap - EXIT
  exit 0
}

# Escape a value for embedding in a JSON string. Only " and \ can occur here:
# git rejects \ in a refname and control characters too, but it accepts ", so
# a branch called feat/say"hi" would otherwise produce broken JSON - the same
# silent death, one layer further in.
json_escape() {
  _rest=$1
  _out=''
  while [ -n "$_rest" ]; do
    _head=${_rest%"${_rest#?}"}
    _rest=${_rest#?}
    case $_head in
      '"') _out="${_out}\\\"" ;;
      '\') _out="${_out}\\\\" ;;
      *) _out="${_out}${_head}" ;;
    esac
  done
  printf '%s' "$_out"
}

# git missing is worth a line. Saying nothing is what hid the jq failure for
# an unknown length of time, and a PATH without git is the same shape of
# accident. Not being inside a repo, by contrast, is a normal quiet case.
if ! command -v git >/dev/null 2>&1; then
  trap - EXIT
  printf '%s\n' '{"systemMessage":"git が見つかりません。枝の警告は出せません。","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"git が見つかりません。枝の警告は出せません。"}}'
  exit 0
fi

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || quiet_exit
cd "$repo_root" || quiet_exit

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
  quiet_exit
fi

# Newlines are written as the two characters \ and n from the start, so the
# message needs no further conversion on the way into JSON. printf does not
# expand escapes inside a %s argument, so they survive as written.
warnings=""

if [ "$branch" = "main" ]; then
  warnings="${warnings}- main で直接作業しようとしています。作業ブランチを checkout してください。\\n"
fi

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  warnings="${warnings}- コミットされていない変更が残っています（前回のセッションの続きかもしれません）。\\n"
fi

upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "")
if [ -n "$upstream" ]; then
  ahead=$(git rev-list --count "$upstream..HEAD" 2>/dev/null || echo 0)
  if [ "$ahead" -gt 0 ]; then
    warnings="${warnings}- $(json_escape "$upstream") に push されていないコミットが ${ahead} 件あります。\\n"
  fi
fi

message="現在のブランチ: $(json_escape "$branch")"
if [ -n "$warnings" ]; then
  message="${message}\\n\\n注意:\\n${warnings}"
fi

trap - EXIT
printf '{"systemMessage":"%s","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' \
  "$message" "$message"

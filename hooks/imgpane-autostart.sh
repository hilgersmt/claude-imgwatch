#!/bin/bash
# SessionStart hook: open an imgwatch pane next to an interactive Claude session in iTerm2.
# Opt out per launch with:  CLAUDE_NO_IMGPANE=1 claude
# iTerm2 (tested); WezTerm and kitty (experimental: split via their CLIs, see bin/imgpane)
case "$TERM_PROGRAM" in
  iTerm.app) [ -n "$ITERM_SESSION_ID" ] || exit 0 ;;
  WezTerm)   [ -n "$WEZTERM_PANE" ] || exit 0 ;;
  *)         [ -n "$KITTY_WINDOW_ID" ] || exit 0 ;;
esac
[ "${CLAUDE_CODE_ENTRYPOINT:-cli}" = cli ] || exit 0      # skip claude -p / SDK runs
[ -n "$CLAUDE_NO_IMGPANE" ] && exit 0
# config: IMGWATCH_PYTHON, IMGWATCH_BROWSER_PROFILE, IMGPANE_WIDTH (env vars in ~/.zshrc do not
# reach the pane, which runs imgwatch without a shell, so settings live in this file)
set -a; [ -f ~/.config/imgwatch/config ] && . ~/.config/imgwatch/config; set +a
read -r sid cwd < <("${IMGWATCH_PYTHON:-python3}" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("session_id",""), d.get("cwd",""))' 2>/dev/null)
[ -n "$sid" ] || exit 0

# the Claude CLI process that owns this session (watcher exits with it)
owner=$CLAUDE_PID
if [ -z "$owner" ]; then
  pid=$PPID
  while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
    case "$(ps -o comm= -p "$pid")" in *claude*) owner=$pid; break;; esac
    pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
  done
fi
[ -n "$owner" ] || exit 0

# already watching this session (resume/compact)? keep it. Watching an old one (/clear)? replace it.
# Anchored on "<interpreter> <path>/imgwatch.py --owner N" so the zsh -c wrapper that imgpane
# starts (and any shell that merely mentions imgwatch) never matches.
w="^[^ ]+ [^ ]*/imgwatch\.py --owner $owner"
existing=$(pgrep -f "$w( |$)")
if [ -n "$existing" ]; then
  pgrep -f "$w --session $sid" >/dev/null && exit 0
  kill $existing
fi
bp=${IMGWATCH_BROWSER_PROFILE-imgbrowser}   # set it to "" in the config to disable the browser lane
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
cd "${cwd:-$PWD}" 2>/dev/null
"$here/../bin/imgpane" --owner "$owner" --session "$sid" ${bp:+--browser-profile "$bp"} >/dev/null 2>&1 &
exit 0

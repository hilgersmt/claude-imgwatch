#!/bin/bash
# Install or update claude-imgwatch: symlink scripts into ~/bin and ~/.claude/hooks, write
# ~/.config/imgwatch/config if missing, and merge the two hooks into ~/.claude/settings.json.
# Re-running is safe. Existing non-symlink files are backed up first. -n = dry run.
set -euo pipefail
repo=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
dry=; [ "${1:-}" = -n ] && dry=echo
stamp=$(date +%Y-%m-%d_%H%M)
bak=~/bin/backups/imgwatch_pre-repo_$stamp

say() { printf '\033[1m%s\033[0m\n' "$*"; }
link() {  # link <repo file> <target>
  local src=$repo/$1 dst=$2
  if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then echo "  ok   $dst"; return; fi
  if [ -e "$dst" ] || [ -L "$dst" ]; then $dry mkdir -p "$bak"; $dry mv "$dst" "$bak/"; echo "  bak  $dst -> $bak/"; fi
  $dry mkdir -p "$(dirname "$dst")"; $dry ln -s "$src" "$dst"; echo "  link $dst -> $src"
}

say "prerequisites"
[ -d /Applications/iTerm.app ] && echo "  iTerm2 $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' /Applications/iTerm.app/Contents/Info.plist)" || echo "  !! iTerm2 not found"
command -v ffmpeg >/dev/null && echo "  ffmpeg: $(command -v ffmpeg)" || echo "  (no ffmpeg: video lane disabled)"

say "config"
cfg=~/.config/imgwatch/config
if [ -f "$cfg" ]; then echo "  ok   $cfg (kept)"; else
  py=
  for c in /opt/anaconda3/bin/python /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
    [ -x "$c" ] && "$c" -c 'import iterm2' 2>/dev/null && { py=$c; break; }
  done
  [ -n "$py" ] || py=$(command -v python3)
  echo "  new  $cfg (IMGWATCH_PYTHON=$py)"
  if [ -z "$dry" ]; then mkdir -p "$(dirname "$cfg")"; sed "s|^IMGWATCH_PYTHON=.*|IMGWATCH_PYTHON=$py|" "$repo/config.example" > "$cfg"; fi
fi
if [ -f "$cfg" ]; then . "$cfg"; fi
py=${IMGWATCH_PYTHON:-python3}
for m in iterm2 selenium; do "$py" -c "import $m" 2>/dev/null && echo "  $py has $m" || echo "  ($py lacks $m)"; done

say "files"
link bin/imgwatch      ~/bin/imgwatch
link bin/imgpane       ~/bin/imgpane
link hooks/imgpane-autostart.sh ~/.claude/hooks/imgpane-autostart.sh
link hooks/imgwatch-enqueue.sh  ~/.claude/hooks/imgwatch-enqueue.sh

say "settings.json hooks"
s=~/.claude/settings.json
if [ -n "$dry" ]; then echo "  (dry run: would merge hooks into $s)"; else
  [ -f "$s" ] && { mkdir -p ~/.claude/backups; cp "$s" ~/.claude/backups/settings.json.$stamp; }
  "$py" - "$s" <<'PY'
import json, os, sys
p = sys.argv[1]
d = json.load(open(p)) if os.path.exists(p) else {}
want = {"PostToolUse": ("Read|Write", "~/.claude/hooks/imgwatch-enqueue.sh", 5),
        "SessionStart": (None, "~/.claude/hooks/imgpane-autostart.sh", 10)}
for ev, (matcher, cmd, to) in want.items():
    groups = d.setdefault("hooks", {}).setdefault(ev, [])
    if any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
        print(f"  ok   {ev}: {cmd}"); continue
    g = {"hooks": [{"type": "command", "command": cmd, "timeout": to}]}
    if matcher: g = {"matcher": matcher, **g}
    groups.append(g); print(f"  add  {ev}: {cmd}")
json.dump(d, open(p, "w"), indent=2); open(p, "a").write("\n")
PY
fi

say "iTerm2 (manual, see README)"
echo "  - Settings > General > Magic > Enable Python API"
echo "  - Browser-type profile named '${IMGWATCH_BROWSER_PROFILE-imgbrowser}' (GUI, or iterm2/imgbrowser.dynamic-profile.json)"

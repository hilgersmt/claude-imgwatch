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
  # an explicit IMGWATCH_PYTHON (IMGWATCH_PYTHON=~/venv/bin/python ./install.sh, tests/run.sh) wins
  if [ -n "${IMGWATCH_PYTHON:-}" ] && [ -x "$(command -v "$IMGWATCH_PYTHON")" ]; then py=$(command -v "$IMGWATCH_PYTHON"); fi
  [ -n "$py" ] || for c in /opt/anaconda3/bin/python /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
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
# the enqueue hook runs on every tool (it reads paths out of Bash/MCP output); older installs had
# matcher "Read|Write", which is widened here
want = {"PostToolUse": (None, "~/.claude/hooks/imgwatch-enqueue.sh", 5),
        "SessionStart": (None, "~/.claude/hooks/imgpane-autostart.sh", 10)}
for ev, (matcher, cmd, to) in want.items():
    groups = d.setdefault("hooks", {}).setdefault(ev, [])
    mine = [g for g in groups if any(h.get("command") == cmd for h in g.get("hooks", []))]
    if mine:
        g = mine[0]
        if g.get("matcher", "") not in ("", "*", matcher or ""):
            if len(g["hooks"]) == 1:
                g.pop("matcher")
            else:  # shared group: move our hook into its own all-tools group
                g["hooks"] = [h for h in g["hooks"] if h.get("command") != cmd]
                groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": to}]})
            print(f"  fix  {ev}: {cmd} now runs for all tools")
        else:
            print(f"  ok   {ev}: {cmd}")
        continue
    g = {"hooks": [{"type": "command", "command": cmd, "timeout": to}]}
    if matcher: g = {"matcher": matcher, **g}
    groups.append(g); print(f"  add  {ev}: {cmd}")
json.dump(d, open(p, "w"), indent=2); open(p, "a").write("\n")
PY
fi

say "iTerm2 browser profile"
prof=${IMGWATCH_BROWSER_PROFILE-imgbrowser}
dyn="$HOME/Library/Application Support/iTerm2/DynamicProfiles"
if [ -z "$prof" ]; then echo "  (browser lane disabled in config)"
elif "$py" - "$prof" <<'PY'
import json, pathlib, plistlib, sys
name, home = sys.argv[1], pathlib.Path.home()
names = []
pl = home / "Library/Preferences/com.googlecode.iterm2.plist"
if pl.exists():
    names += [p.get("Name") for p in plistlib.load(open(pl, "rb")).get("New Bookmarks", [])]
d = home / "Library/Application Support/iTerm2/DynamicProfiles"
for f in d.glob("*") if d.exists() else []:
    try: names += [p.get("Name") for p in json.loads(f.read_text()).get("Profiles", [])]
    except Exception: pass
sys.exit(0 if name in names else 1)
PY
then echo "  ok   profile '$prof' exists"
else
  echo "  new  $dyn/imgwatch.json (Browser profile '$prof')"
  if [ -z "$dry" ]; then mkdir -p "$dyn"; sed "s/\"Name\": \"imgbrowser\"/\"Name\": \"$prof\"/" "$repo/iterm2/imgbrowser.dynamic-profile.json" > "$dyn/imgwatch.json"; fi
fi

say "check"
if [ -n "$dry" ]; then echo "  (dry run: would run imgwatch --doctor)"; else ~/bin/imgwatch --doctor || true; fi
echo "Manual, once: iTerm2 > Settings > General > Magic > Enable Python API (browser lane)."

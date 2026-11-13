#!/bin/bash
# Guided setup for claude-imgwatch: walks you through every step, explains it, asks before
# installing anything, and checks each manual step before moving on. Safe to re-run.
#   ./setup.sh            interactive (run it in iTerm2)
#   ./setup.sh --demo     just the live demo, to check an existing install
# Non-interactive installs (scripts, Claude) use ./install.sh instead.
set -u
repo=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
cfg=~/.config/imgwatch/config
B=$'\033[1m' D=$'\033[2m' G=$'\033[32m' Y=$'\033[33m' R=$'\033[31m' C=$'\033[36m' N=$'\033[0m'

[ -t 0 ] && [ -t 1 ] || { echo "setup.sh is interactive; for unattended installs run ./install.sh"; exit 1; }

step=0
title() { step=$((step + 1)); printf '\n%s── Step %d · %s%s\n' "$B$C" "$step" "$1" "$N"; }
say()   { printf '%s\n' "$*"; }
good()  { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
meh()   { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
nope()  { printf '  %s✗%s %s\n' "$R" "$N" "$*"; }
# prompts: end of input counts as "no" / "skip", so nothing can loop
ask()   { local a; read -r -p "  $1 [Y/n] " a || return 1; [[ -z $a || $a =~ ^[Yy] ]]; }   # default yes
ask_no(){ local a; read -r -p "  $1 [y/N] " a || return 1; [[ $a =~ ^[Yy] ]]; }           # default no
pause() { local a; read -r -p "  ${D}Press Enter when done (or type s to skip)…${N} " a || return 1; [[ $a != s ]]; }
has()   { "$py" -c "import $1" 2>/dev/null; }

# ---------------------------------------------------------------- live demo (step 8, or ./setup.sh --demo)
demo_step() {
if ask "Open a demo pane next to this one and show a few things?"; then
  demo=$(mktemp -d /tmp/imgwatch-demo.XXXX)
  sleep 600 </dev/null & own=$!; disown $own  # no "Terminated" job message when killed
  (cd "$demo" && "$repo/bin/imgpane" --owner $own "$demo" </dev/null)
  sleep 3
  "$py" - "$demo" <<'PY'
import base64, struct, sys, zlib
d = sys.argv[1]
def png(path, w=360, h=200):  # a little sine-wave PNG without any plotting library
    import math
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            on = abs(y - (h / 2 - (h / 2.6) * math.sin(x / w * 4 * math.pi))) < 3
            row += bytes((40, 200, 255) if on else (20, 20, 30))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows))
    ch = lambda t, b: struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", raw) + ch(b"IEND", b""))
png(f"{d}/sine_wave.png")
PY
  if ask "A ${B}blue sine wave${N} appeared in the new pane on the right?"; then good "images work"
  else meh "Not shown. Is the right-hand pane there at all? If not, step 6 (Automation) is the usual cause."
       say "  If the pane is there but empty, run ${B}imgwatch --doctor${N} and check the FAIL lines."; fi
  if has iterm2; then
    cat > "$demo/page.html" <<'HTML'
<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;background:#111;color:#eee;font:16px -apple-system}
canvas{display:block;margin:20px auto}</style></head><body><h3 style="text-align:center">imgwatch browser pane</h3>
<canvas id="c" width="300" height="300"></canvas><script>
var c=document.getElementById('c').getContext('2d'),t=0;(function f(){c.fillStyle='#111';c.fillRect(0,0,300,300);
c.save();c.translate(150,150);c.rotate(t+=0.03);c.fillStyle='#4cc9f0';c.fillRect(-60,-60,120,120);c.restore();
requestAnimationFrame(f)})();</script></body></html>
HTML
    if ask "A ${B}spinning blue square${N} appeared in a browser pane below it?"; then good "web pages work"
    else meh "Not shown. Check step 5 (Python API) and that the 'imgbrowser' profile exists:"
         say "  /Applications/iTerm.app/Contents/Resources/utilities/it2 profile list"; fi
  fi
  kill $own 2>/dev/null; sleep 2
  ask "The demo pane(s) closed again?" && good "clean-up works" || meh "close them by hand; they will close with Claude in real use"
  rm -rf "$demo"
fi
}

if [ "${1:-}" = --demo ]; then  # just the live demo, using the installed config
  py=$(sed -n 's/^IMGWATCH_PYTHON=//p' "$cfg" 2>/dev/null); py=${py:-python3}
  printf '%s── Demo%s\n' "$B$C" "$N"; demo_step; exit
fi

printf '%sclaude-imgwatch setup%s  %s\n' "$B" "$N" "$(git -C "$repo" log -1 --format='%h %cs' 2>/dev/null)"
say "This gives every Claude Code session in iTerm2 a side pane that shows the images, plots,"
say "videos, PDFs, web pages and molecular structures it makes. About 10 minutes. Nothing is"
say "installed without asking, and every step can be skipped and re-run later."

# ---------------------------------------------------------------- 1 terminal
title "iTerm2"
if [ ! -d /Applications/iTerm.app ]; then
  nope "iTerm2 is not installed. imgwatch draws images with iTerm2's inline-image protocol."
  if command -v brew >/dev/null && ask "Install it with Homebrew (brew install --cask iterm2)?"; then
    brew install --cask iterm2
  else
    say "  Download it from https://iterm2.com, then re-run ./setup.sh from an iTerm2 window."; exit 1
  fi
fi
v=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' /Applications/iTerm.app/Contents/Info.plist 2>/dev/null)
if [[ $(printf '%s\n3.7\n' "$v" | sort -V | head -1) == 3.7 ]]; then good "iTerm2 $v"
else meh "iTerm2 $v: images work, but interactive web pages need 3.7 or newer (iTerm2 > Check for Updates)."; fi
if [ "${TERM_PROGRAM:-}" != iTerm.app ]; then
  meh "You are running this in ${TERM_PROGRAM:-another terminal}. The later checks need iTerm2:"
  say "  open iTerm2 and run ${B}$repo/setup.sh${N} there."; ask_no "Continue anyway?" || exit 0
fi

# ---------------------------------------------------------------- 2 python
title "Python"
say "imgwatch is a Python script. Pick the Python it should use; the one with the most extras ticked"
say "is usually your Anaconda/conda or Homebrew Python."
cands=()
for c in /opt/anaconda3/bin/python ~/anaconda3/bin/python ~/miniconda3/bin/python ~/miniforge3/bin/python \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
  [ -x "$c" ] || continue
  r=$(readlink -f "$c"); [[ " ${seen:-} " == *" $r "* ]] && continue; seen="${seen:-} $r"
  [[ $c == /usr/bin/python3 ]] && ! xcrun -f python3 >/dev/null 2>&1 && continue  # stub without CLT
  cands+=("$c")
done
[ ${#cands[@]} -gt 0 ] || { nope "No Python 3 found. Install one (e.g. brew install python) and re-run."; exit 1; }
cur=; [ -f "$cfg" ] && cur=$(sed -n 's/^IMGWATCH_PYTHON=//p' "$cfg")
i=0
for c in "${cands[@]}"; do
  i=$((i + 1)); mods=""
  for m in iterm2 watchdog PIL rdkit pymol selenium; do
    "$c" -c "import $m" 2>/dev/null && mods+=" $G$m$N" || mods+=" $D$m$N"
  done
  printf '  %d) %s %s(%s)%s%s\n' $i "$c" "$D" "$("$c" -V 2>&1 | cut -d' ' -f2)" "$N" "$mods$([ "$c" = "$cur" ] && echo "  ${Y}← current${N}")"
done
read -r -p "  Which one? [1] " n || n=1; n=${n:-1}
py=${cands[$((n - 1))]:-${cands[0]}}
good "using $py"

# ---------------------------------------------------------------- 3 extras
title "Optional extras"
say "Everything below is optional: without it, that one file type is skipped or shown more simply."
pipi() {  # pipi <pip args...>: pip install, with the Anaconda certifi workaround
  "$py" -m pip install -q "$@" 2>"/tmp/imgwatch-pip.$$" && return 0
  grep -q certifi "/tmp/imgwatch-pip.$$" && "$py" -m pip install -q --ignore-installed certifi "$@" && return 0
  tail -3 "/tmp/imgwatch-pip.$$"; return 1
}
extra() {  # extra <module> <pip name> <what it is for>
  if has "$1"; then good "$1 — $3"; return; fi
  meh "$1 missing — $3"
  if ask "  Install it now ($py -m pip install $2)?"; then pipi "$2" && good "installed $2" || nope "could not install $2"; fi
}
extra iterm2   iterm2   "live web pages (Plotly, Bokeh…) and Mol* structures in a browser pane"
if has iterm2 && has streamlit && ! "$py" -c 'import google.protobuf as p, sys; sys.exit(int(p.__version__.split(".")[0]) >= 7)'; then
  meh "the iterm2 package pulled in protobuf 7, which breaks streamlit"
  ask "  Pin protobuf below 7 (works for both)?" && pipi 'protobuf<7' && good "protobuf pinned"
fi
extra watchdog watchdog "reacts to new files instantly instead of rescanning folders every second"
extra PIL      pillow   "contact sheets when many images arrive at once"
extra rdkit    rdkit    "SMILES lists and 2D SDF files drawn as molecule grids"
extra selenium selenium "screenshots of web pages when no browser pane is available"
if has pymol; then good "pymol — snapshots of PyMOL sessions (.pse)"
else
  meh "pymol missing — snapshots of PyMOL sessions (.pse)"
  if [ -x "$(dirname "$py")/conda" ] && ask "  Install open-source PyMOL with conda (conda install -c conda-forge pymol-open-source)?"; then
    "$(dirname "$py")/conda" install -y -q -c conda-forge pymol-open-source && good "installed pymol"
  else say "  ${D}(skip: .pse files will just show their name; install pymol into this Python later to enable)${N}"; fi
fi
if command -v ffmpeg >/dev/null || [ -x /opt/homebrew/bin/ffmpeg ]; then good "ffmpeg — videos play as animated previews"
else
  meh "ffmpeg missing — videos play as animated previews"
  command -v brew >/dev/null && ask "  Install it with Homebrew (brew install ffmpeg)?" && brew install ffmpeg && good "installed ffmpeg"
fi
[ -d "/Applications/Google Chrome.app" ] && good "Google Chrome — page screenshots (fallback only)" \
  || say "  ${D}Google Chrome not found — only needed for the screenshot fallback; fine to skip.${N}"

# ---------------------------------------------------------------- 4 install
title "Install the scripts and Claude Code hooks"
say "This links imgwatch into ~/bin, adds two hooks to ~/.claude/settings.json (your other settings"
say "are kept; a backup is made first) and creates an iTerm2 browser profile called 'imgbrowser'."
mkdir -p "$(dirname "$cfg")"
if [ -f "$cfg" ]; then sed -i '' "s|^IMGWATCH_PYTHON=.*|IMGWATCH_PYTHON=$py|" "$cfg"
else sed "s|^IMGWATCH_PYTHON=.*|IMGWATCH_PYTHON=$py|" "$repo/config.example" > "$cfg"; fi
good "config: $cfg"
say "  ${D}Here is what it will do:${N}"
"$repo/install.sh" -n </dev/null 2>&1 | grep -E '^  (bak|link|new|add|fix|ok)' | sed 's/^/  /'
if ask "Go ahead?"; then "$repo/install.sh" </dev/null >/tmp/imgwatch-install.$$ 2>&1 && good "installed" || { nope "install.sh failed:"; tail -20 /tmp/imgwatch-install.$$; exit 1; }
else say "  Stopped. Re-run ./setup.sh whenever you like."; exit 0; fi

# ---------------------------------------------------------------- 5 python API (manual)
title "Let imgwatch talk to iTerm2 (one checkbox)"
api_ok() { "$py" - <<'PY' 2>/dev/null
import sys, iterm2
async def main(c): await iterm2.async_get_app(c)
try:
    iterm2.run_until_complete(main); print("ok")
except BaseException:
    pass
PY
}
if ! has iterm2; then meh "skipped (the iterm2 module isn't installed; web pages will be screenshots or links)"
elif [ "$(api_ok)" = ok ]; then good "iTerm2's Python API is already enabled"
else
  say "imgwatch opens the browser pane through iTerm2's Python API, which is off by default."
  say "  1. Click into iTerm2 and press ${B}⌘ ,${N} (Command + comma) to open Settings."
  say "  2. Choose the ${B}General${N} tab at the top, then the ${B}Magic${N} sub-tab."
  say "  3. Tick ${B}Enable Python API${N}. iTerm2 asks you to confirm; click ${B}Accept${N}."
  say "  4. Close the Settings window."
  while pause; do
    if [ "$(api_ok)" = ok ]; then good "connected to iTerm2's Python API"; break; fi
    nope "still can't connect. Check the box is ticked (General → Magic → Enable Python API), then try again."
  done
fi

# ---------------------------------------------------------------- 6 automation (manual prompt)
title "Allow pane splitting (one macOS prompt)"
say "imgwatch opens its pane by asking iTerm2 to split the window (AppleScript). macOS may now show"
say "\"iTerm2 wants access to control iTerm2\" — click ${B}Allow${N}. If nothing appears, it is already allowed."
if osascript -e 'tell application "iTerm2" to get name of current window' >/dev/null 2>&1; then
  good "iTerm2 can be scripted"
else
  nope "not allowed. Open System Settings → Privacy & Security → Automation, find iTerm2 and"
  say "  switch on ${B}iTerm2${N} under it. Then press Enter."
  pause && osascript -e 'tell application "iTerm2" to get name of current window' >/dev/null 2>&1 \
    && good "iTerm2 can be scripted" || meh "still blocked; the pane won't open on its own until this is allowed"
fi

# ---------------------------------------------------------------- 7 check
title "Health check"
"$repo/bin/imgwatch" --doctor </dev/null | sed '1,2d'
"$repo/bin/imgwatch" --doctor </dev/null >/dev/null 2>&1 || meh "there is at least one FAIL above; the lines say what to fix (re-run ./setup.sh after)"

# ---------------------------------------------------------------- 8 live demo
title "Try it"
demo_step

# ---------------------------------------------------------------- done
title "Done"
say "Start a new ${B}claude${N} session in iTerm2: the pane opens on the right by itself and shows whatever"
say "that session reads, writes or saves (figures, PDFs, web pages, .pdb/.cif, .smi, .pse, slides…)."
say "  ${D}Skip it for one session:${N}  CLAUDE_NO_IMGPANE=1 claude"
say "  ${D}Check the setup any time:${N}  imgwatch --doctor      ${D}Full self-test:${N}  $repo/tests/run.sh"
say "  ${D}Update later:${N}  cd $repo && git pull"

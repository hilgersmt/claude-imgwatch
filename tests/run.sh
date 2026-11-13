#!/bin/bash
# Headless test suite for claude-imgwatch: everything that does not need a real iTerm2 pane.
# Runs against a throwaway HOME, so your install, queue, cache and settings are never touched.
#   tests/run.sh        run all tests
#   tests/run.sh -k     keep the temp dir afterwards (for poking at logs)
# Needs: the configured interpreter (IMGWATCH_PYTHON, read from ~/.config/imgwatch/config) with
# matplotlib for making fixtures; optional: ffmpeg (video), Pillow (contact sheets), network or a
# cached Mol* (structures). Missing optional pieces are reported as SKIP, not FAIL.
set -u
repo=$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)
keep=; [ "${1:-}" = -k ] && keep=1
cfg=~/.config/imgwatch/config
[ -f "$cfg" ] && { set -a; . "$cfg"; set +a; }
export IMGWATCH_PYTHON=${IMGWATCH_PYTHON:-python3}
py=$IMGWATCH_PYTHON
realvendor=~/.claude/imgwatch/vendor

T=$(mktemp -d "${TMPDIR:-/tmp}/imgwatch-test.XXXXXX"); T=$(cd "$T" && pwd -P)  # FSEvents reports /private/var/...
E=$'\e'
export HOME=$T/home
mkdir -p "$HOME/.claude" "$HOME/bin" "$T/watch" "$T/fix"
echo '{}' > "$HOME/.claude/settings.json"
[ -d "$realvendor" ] && { mkdir -p "$HOME/.claude/imgwatch"; cp -R "$realvendor" "$HOME/.claude/imgwatch/"; }
trap '[ -n "$keep" ] && echo "kept $T" || rm -rf "$T"; [ -n "${W:-}" ] && kill $W 2>/dev/null' EXIT

pass=0 fail=0 skip=0
ok()   { pass=$((pass+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { fail=$((fail+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
skp()  { skip=$((skip+1)); printf '  \033[33mSKIP\033[0m  %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }   # check "<name>" "<shell test>"
log()  { LC_ALL=C cat "$T/watch.log"; }
waitfor() {  # waitfor <fixed string> [seconds]: wait until the watcher log contains it
  local i; for ((i = 0; i < ${2:-10} * 5; i++)); do log | grep -qF -- "$1" && return 0; sleep 0.2; done; return 1
}

printf '\033[1mclaude-imgwatch tests\033[0m  (%s, %s)\n' "$(git -C "$repo" log -1 --format='%h %cs' 2>/dev/null)" "$py"

# ------------------------------------------------------------------ fixtures
"$py" - "$T/fix" <<'PY' || { echo "fixture build failed (needs matplotlib)"; exit 1; }
import sys, numpy as np, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
d = sys.argv[1]
def fig(name, title):
    f, ax = plt.subplots(figsize=(4, 3), dpi=60); ax.plot(np.sin(np.linspace(0, 6, 50))); ax.set_title(title)
    f.savefig(f"{d}/{name}"); plt.close(f)
fig("single.png", "single")
for i in range(8):
    fig(f"burst_{i}.png", f"burst {i}")
fig("chart.svg", "svg")
with PdfPages(f"{d}/doc.pdf") as pdf:
    for k in range(3):
        f, ax = plt.subplots(); ax.set_title(f"page {k+1}"); pdf.savefig(f); plt.close(f)
open(f"{d}/page.html", "w").write("<!doctype html><html><head></head><body><p>hello</p></body></html>")
# a tiny but valid PDB (3 residues of crambin)
open(f"{d}/tiny.pdb", "w").write("""\
ATOM      1  N   THR A   1      17.047  14.099   3.625  1.00 13.79           N
ATOM      2  CA  THR A   1      16.967  12.784   4.338  1.00 10.80           C
ATOM      3  C   THR A   1      15.685  12.755   5.133  1.00  9.19           C
ATOM      4  O   THR A   1      15.268  13.825   5.594  1.00  9.85           O
ATOM      5  N   THR A   2      15.115  11.555   5.265  1.00  7.81           N
ATOM      6  CA  THR A   2      13.856  11.469   6.066  1.00  8.31           C
ATOM      7  C   THR A   2      14.164  10.785   7.379  1.00  5.80           C
ATOM      8  O   THR A   2      14.993   9.862   7.443  1.00  6.94           O
END
""")
PY
command -v ffmpeg >/dev/null && ffmpeg -v error -f lavfi -i testsrc=duration=2:size=160x120:rate=10 -pix_fmt yuv420p "$T/fix/clip.mp4"

# ------------------------------------------------------------------ static checks
echo "static"
check "imgwatch.py compiles" "'$py' -m py_compile '$repo/bin/imgwatch.py'"
check "shell scripts parse" "bash -n '$repo/bin/imgpane' && bash -n '$repo/bin/imgwatch' && bash -n '$repo'/hooks/*.sh && bash -n '$repo/install.sh'"
check "-V prints a version" "'$repo/bin/imgwatch' -V | grep -q '^imgwatch '"
q=$HOME/.claude/imgwatch/queue
enq() { printf '{"session_id":"S","tool_input":{"file_path":"%s"}}' "$1" | "$repo/hooks/imgwatch-enqueue.sh"; }
rm -f "$q"; enq /a/x.png; enq /a/y.bcif; enq /a/z.pdf; enq /a/w.mp4; enq /a/notes.txt; enq /a/data.csv
check "enqueue hook: media in, text out" "[ \"\$(cut -f2 '$q' | tr '\n' ' ')\" = '/a/x.png /a/y.bcif /a/z.pdf /a/w.mp4 ' ]"
check "enqueue hook: line format session<TAB>path" "head -1 '$q' | grep -q \$'^S\t/a/x.png\$'"
rm -f "$q"

# ------------------------------------------------------------------ install
echo "install"
"$repo/install.sh" > "$T/install1.log" 2>&1
check "install.sh exits 0" "[ $? -eq 0 ]"
check "links point into the repo" "[ \"\$(readlink -f '$HOME/bin/imgwatch')\" = '$repo/bin/imgwatch' ] && [ \"\$(readlink -f '$HOME/.claude/hooks/imgwatch-enqueue.sh')\" = '$repo/hooks/imgwatch-enqueue.sh' ]"
check "settings.json has both hooks" "grep -q imgwatch-enqueue.sh '$HOME/.claude/settings.json' && grep -q imgpane-autostart.sh '$HOME/.claude/settings.json'"
check "browser profile created" "grep -q '\"Custom Command\": \"Browser\"' '$HOME/Library/Application Support/iTerm2/DynamicProfiles/imgwatch.json'"
"$repo/install.sh" > "$T/install2.log" 2>&1
check "install.sh is idempotent" "! grep -qE '^  (link|add|new|bak) ' '$T/install2.log'"
"$repo/bin/imgwatch" --doctor > "$T/doctor.log" 2>&1
check "doctor: clean on a fresh install" "[ $? -eq 0 ] && ! grep -q FAIL '$T/doctor.log'"
check "doctor: pgrep pattern matches the watcher" "grep -q 'pgrep pattern matches' '$T/doctor.log'"

# ------------------------------------------------------------------ pruning
echo "housekeeping"
c=$HOME/.claude/imgwatch/cache; mkdir -p "$c"
for i in 1 2; do head -c 1000 /dev/urandom > "$c/old$i.png"; touch -t 202001010000 "$c/old$i.png"; done
for i in 1 2 3; do head -c 2000000 /dev/urandom > "$c/new$i.png"; touch -t 20991231000$i "$c/new$i.png"; done
"$py" -c "open('$q','w').write(''.join(f'X\t/q/{i:07d}.png\n' for i in range(80000)))"
TERM_PROGRAM=iTerm.app "$repo/bin/imgwatch" --no-poll --cache-mb 5 > /dev/null 2>&1 & W=$!; sleep 2; kill $W; W=
check "cache: files older than --cache-days removed" "[ ! -e '$c/old1.png' ] && [ ! -e '$c/old2.png' ]"
check "cache: oldest removed to fit --cache-mb" "[ ! -e '$c/new1.png' ] && [ -e '$c/new3.png' ]"
check "queue: trimmed to its tail on a line boundary" "[ \$(wc -c < '$q') -lt 70000 ] && head -1 '$q' | grep -q \$'^X\t/q/'"
rm -f "$c"/new*.png; : > "$q"

# ------------------------------------------------------------------ rendering
echo "rendering (headless watcher, link mode)"
cd "$T/watch"
TERM_PROGRAM=iTerm.app COLUMNS=100 "$repo/bin/imgwatch" --session S1 --html-mode link "$T/watch" > "$T/watch.log" 2>&1 & W=$!
check "watcher starts with a banner" "waitfor '+ hook queue' 5"
cp "$T/fix/single.png" "$T/watch/"
check "PNG: header with clickable name" "waitfor '${E}]8;;file://localhost$T/watch/single.png' 8"
cp "$T/fix"/burst_*.png "$T/watch/"
if "$py" -c 'import PIL' 2>/dev/null; then
  # FSEvents occasionally delivers a burst in two groups (e.g. 6 + 2), so only require that a sheet is made
  check "burst of 8 stills: contact sheet" "waitfor ' images' 10 && log | grep -qE '[0-9]+ images' && ls '$c'/sheet_*.png >/dev/null 2>&1"
else skp "burst (no Pillow)"; fi
cp "$T/fix/chart.svg" "$T/watch/"
check "SVG: rasterised by Quick Look" "waitfor 'chart.svg' 8 && sleep 1.5 && [ \$(ls '$c'/*.png 2>/dev/null | grep -vc sheet_) -ge 1 ]"
cp "$T/fix/doc.pdf" "$T/watch/"
check "PDF: page 1 of 3" "waitfor 'page 1 of 3' 10"
cp "$T/fix/page.html" "$T/watch/"
check "HTML (link mode): OSC 8 link to the page" "waitfor '${E}]8;;file://localhost$T/watch/page.html${E}\\${E}[4;36m' 8"
cp "$T/fix/tiny.pdb" "$T/watch/"
if waitfor 'structure → Mol*' 8 && waitfor 'tiny.pdb.html' 40; then
  pg=$(ls "$c"/*_tiny.pdb.html 2>/dev/null | head -1)
  check "structure: Mol* page with the data embedded" "[ -n '$pg' ] && grep -q 'loadStructureFromData' '$pg' && grep -q 'THR A   1' '$pg'"
else
  log | grep -q 'cannot fetch Mol' && skp "structure (Mol* not cached and no network)" || bad "structure: no Mol* page"
fi
if [ -f "$T/fix/clip.mp4" ]; then
  cp "$T/fix/clip.mp4" "$T/watch/"
  check "video: animated-GIF preview" "waitfor 'preview ' 30 && ls '$c'/*.gif >/dev/null 2>&1"
else skp "video (no ffmpeg)"; fi
printf 'S1\t%s\n' "$T/fix/single.png" >> "$q"; printf 'OTHER\t%s\n' "$T/fix/burst_0.png" >> "$q"
sleep 3
check "queue: own session's file shown again" "[ \$(log | grep -c 'single.png') -ge 2 ]"
check "queue: other session's file ignored" "! log | grep -qF '$T/fix/burst_0.png'"
check "no Python traceback in the log" "! log | grep -q Traceback"
kill $W 2>/dev/null; W=

printf '\n%d passed, %d failed, %d skipped\n' $pass $fail $skip
[ $fail -eq 0 ]

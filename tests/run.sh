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
export IMGWATCH_DOCTOR_OFFLINE=1   # the doctor must not ask the real iTerm2 which profiles it loaded
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

printf '\033[1mclaude-imgwatch tests\033[0m  (%s, %s)\n' "$("$repo/bin/imgwatch.py" -V 2>/dev/null | sed 's/^imgwatch //')" "$py"

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
try:
    from rdkit import Chem; from rdkit.Chem import AllChem
    open(f"{d}/hits.smi", "w").write("smiles name\nCC(=O)Oc1ccccc1C(=O)O aspirin\nCN1C=NC2=C1C(=O)N(C(=O)N2C)C caffeine\nnot_a_smiles broken\n")
    w = Chem.SDWriter(f"{d}/series_2d.sdf")
    for s in ("c1ccccc1O", "CCO", "CC(=O)N"):
        m = Chem.MolFromSmiles(s); AllChem.Compute2DCoords(m); w.write(m)
    w.close()
    m = Chem.AddHs(Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")); AllChem.EmbedMolecule(m, randomSeed=3)
    w = Chem.SDWriter(f"{d}/one_3d.sdf"); w.write(m); w.close()
except ImportError:
    pass
try:
    import pymol; pymol.finish_launching(["pymol", "-qc"]); from pymol import cmd
    cmd.fab("ACDEFGHIK", "pep", ss=1); cmd.show_as("cartoon"); cmd.save(f"{d}/pep.pse")
except Exception:
    pass
try:
    from pptx import Presentation
    p = Presentation(); p.slides.add_slide(p.slide_layouts[0]).shapes.title.text = "deck"; p.save(f"{d}/deck.pptx")
except ImportError:
    pass
PY
command -v ffmpeg >/dev/null && ffmpeg -v error -f lavfi -i testsrc=duration=2:size=160x120:rate=10 -pix_fmt yuv420p "$T/fix/clip.mp4"

# ------------------------------------------------------------------ static checks
echo "static"
check "imgwatch.py compiles" "'$py' -m py_compile '$repo/bin/imgwatch.py'"
check "shell scripts parse" "bash -n '$repo/bin/imgpane' && bash -n '$repo/bin/imgwatch' && bash -n '$repo'/hooks/*.sh && bash -n '$repo/install.sh'"
check "setup.sh parses and refuses to run unattended" "bash -n '$repo/setup.sh' && ! '$repo/setup.sh' </dev/null >/dev/null 2>&1"
check "imgwatch-plugin parses; verify rejects an unsigned copy" "bash -n '$repo/bin/imgwatch-plugin' && mkdir -p '$T/fake/iTermBrowserPlugin.app/Contents' && printf '<plist><dict><key>CFBundleIdentifier</key><string>com.googlecode.iterm2.iTermBrowserPlugin</string></dict></plist>' > '$T/fake/iTermBrowserPlugin.app/Contents/Info.plist' && ! '$repo/bin/imgwatch-plugin' verify '$T/fake/iTermBrowserPlugin.app' >/dev/null"
if [ -d /Applications/iTermBrowserPlugin.app ]; then
  check "imgwatch-plugin verifies the installed plugin" "'$repo/bin/imgwatch-plugin' verify /Applications/iTermBrowserPlugin.app >/dev/null"
fi
if [ -n "${IMGWATCH_TEST_NET:-}" ]; then  # opt-in: real download (~0.5 MB) + verification
  check "imgwatch-plugin install downloads and verifies (network)" "'$repo/bin/imgwatch-plugin' install -d '$T/plug' >/dev/null && [ -d '$T/plug/iTermBrowserPlugin.app' ]"
else skp "plugin download (set IMGWATCH_TEST_NET=1)"; fi
check "-V prints a version" "'$repo/bin/imgwatch' -V | grep -qE '^imgwatch (v[0-9]+\.[0-9]+\.[0-9]+(\+[0-9]+)? \([0-9a-f]+|[0-9a-f]{7})'"
check "CHANGELOG has an entry for the current release tag" "t=\$(git -C '$repo' describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null); [ -z \"\$t\" ] || grep -q \"^## \$t \" '$repo/CHANGELOG.md'"
q=$HOME/.claude/imgwatch/queue
enq() { printf '{"session_id":"S","tool_input":{"file_path":"%s"}}' "$1" | "$repo/hooks/imgwatch-enqueue.sh"; }
hook() { "$repo/hooks/imgwatch-enqueue.sh"; }
mkdir -p "$T/out dir"; echo x > "$T/out dir/fig 1.png"; echo x > "$T/stale.png"; touch -t 202001010000 "$T/stale.png"
echo x > "$T/pymol.png"; mkdir -p "$HOME/.claude/imgwatch/cache"; echo x > "$HOME/.claude/imgwatch/cache/own.png"
rm -f "$q"
"$py" -c 'import json,sys; print(json.dumps({"session_id":"S","cwd":sys.argv[1],"tool_name":"Bash","tool_input":{"command":"python plot.py"},"tool_response":{"stdout":"saved "+sys.argv[1]+"/out dir/fig 1.png"}}))' "$T" | hook
"$py" -c 'import json,sys; print(json.dumps({"session_id":"S","cwd":sys.argv[1],"tool_name":"mcp__pymol__parse_and_execute","tool_input":{"command":"png "+sys.argv[1]+"/pymol.png, ray=1"},"tool_response":"ok"}))' "$T" | hook
check "hook: Bash output path with spaces" "grep -qF '$T/out dir/fig 1.png' '$q'"
check "hook: path in an MCP tool's input (PyMOL png)" "grep -qF '$T/pymol.png' '$q'"
rm -f "$q"
"$py" -c 'import json,sys; print(json.dumps({"session_id":"S","cwd":sys.argv[1],"tool_name":"Bash","tool_input":{"command":"ls"},"tool_response":{"stdout":sys.argv[1]+"/stale.png "+sys.argv[2]}}))' "$T" "$HOME/.claude/imgwatch/cache/own.png" | hook
"$py" -c 'import json,sys; print(json.dumps({"session_id":"S","cwd":sys.argv[1],"tool_name":"Grep","tool_input":{},"tool_response":{"filenames":[sys.argv[1]+"/pymol.png"]}}))' "$T" | hook
check "hook: ignores stale files, its own cache, Grep output" "[ ! -s '$q' ]"
rm -f "$q"; enq /a/x.png; enq /a/y.bcif; enq /a/z.pdf; enq /a/w.mp4; enq /a/v.HEIC; enq /a/notes.txt; enq /a/data.csv
check "enqueue hook: media in, text out" "[ \"\$(cut -f2 '$q' | tr '\n' ' ')\" = '/a/x.png /a/y.bcif /a/z.pdf /a/w.mp4 /a/v.HEIC ' ]"
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
"$py" - "$HOME/.claude/settings.json" <<'PY'
import json, sys; p = sys.argv[1]; d = json.load(open(p))
for g in d["hooks"]["PostToolUse"]:
    if any(h["command"].endswith("imgwatch-enqueue.sh") for h in g["hooks"]): g["matcher"] = "Read|Write"
json.dump(d, open(p, "w"))
PY
"$repo/bin/imgwatch" --doctor > "$T/doctor0.log" 2>&1
check "doctor: warns about an old Read|Write-only hook" "grep -q 'only runs for' '$T/doctor0.log'"
"$repo/install.sh" > "$T/install3.log" 2>&1
check "install.sh widens the old Read|Write matcher" "grep -q 'now runs for all tools' '$T/install3.log' && ! grep -q 'Read|Write' '$HOME/.claude/settings.json'"
"$repo/bin/imgwatch" --doctor > "$T/doctor.log" 2>&1
check "doctor: clean on a fresh install" "[ $? -eq 0 ] && ! grep -q FAIL '$T/doctor.log'"
check "doctor: pgrep pattern matches the watcher" "grep -q 'pgrep pattern matches' '$T/doctor.log'"
IMGWATCH_DOCTOR_LIVE=Default "$HOME/bin/imgwatch" --doctor > "$T/doctor_noplugin.log" 2>&1
check "doctor: Browser profile defined but not loaded -> plugin FAIL" "[ $? -ne 0 ] && grep -q 'Browser Plugin' '$T/doctor_noplugin.log'"
IMGWATCH_DOCTOR_LIVE=Default,imgbrowser "$HOME/bin/imgwatch" --doctor > "$T/doctor_live.log" 2>&1
check "doctor: profile loaded by iTerm2 -> ok" "grep -q 'loaded by iTerm2' '$T/doctor_live.log'"

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
if sips -s format heic "$T/fix/single.png" --out "$T/fix/photo.heic" >/dev/null 2>&1; then
  cp "$T/fix/photo.heic" "$T/watch/"
  check "HEIC: converted to JPEG with sips" "waitfor 'photo.heic' 8 && sleep 1.5 && ls '$c'/*.jpg >/dev/null 2>&1"
else skp "HEIC (sips can't write HEIC here)"; fi
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
if [ -f "$T/fix/hits.smi" ]; then
  cp "$T/fix/hits.smi" "$T/watch/"
  check "SMILES list: grid, unreadable counted" "waitfor '2 molecules, 1 unreadable' 10 && ls '$c'/grid_*.png >/dev/null 2>&1"
  cp "$T/fix/series_2d.sdf" "$T/watch/"
  check "2D multi-molecule SDF: grid" "waitfor '3 molecules' 10"
  cp "$T/fix/one_3d.sdf" "$T/watch/"
  check "single 3D SDF: Mol*" "waitfor 'one_3d.sdf.html' 20"
else skp "compounds (no RDKit)"; fi
if [ -f "$T/fix/pep.pse" ]; then
  cp "$T/fix/pep.pse" "$T/watch/"
  check "PyMOL session: ray-traced snapshot" "waitfor 'ray-traced snapshot' 8 && for i in \$(seq 60); do ls '$c'/pse_*.png >/dev/null 2>&1 && break; sleep 0.5; done; ls '$c'/pse_*.png >/dev/null 2>&1"
else skp "PyMOL session (pymol not importable)"; fi
if [ -f "$T/fix/deck.pptx" ]; then
  cp "$T/fix/deck.pptx" "$T/watch/"
  check "PowerPoint: first slide via Quick Look" "waitfor 'first slide/page' 10"
else skp "Office (no python-pptx to make a fixture)"; fi
printf 'S1\t%s\n' "$T/fix/single.png" >> "$q"; printf 'OTHER\t%s\n' "$T/fix/burst_0.png" >> "$q"
sleep 3
check "queue: own session's file shown again" "[ \$(log | grep -c 'single.png') -ge 2 ]"
check "queue: other session's file ignored" "! log | grep -qF '$T/fix/burst_0.png'"
check "no Python traceback in the log" "! log | grep -q Traceback"
kill $W 2>/dev/null; W=

printf '\n%d passed, %d failed, %d skipped\n' $pass $fail $skip
[ $fail -eq 0 ]

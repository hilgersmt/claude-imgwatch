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
open(f"{d}/notes.md", "w").write("---\ntags: [t]\n---\n# Title\n\nSee [[other note|the other]] and ![[single.png]].\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
import base64, io, json
b = io.BytesIO(); f, ax = plt.subplots(figsize=(2, 2), dpi=40); ax.plot([1, 2]); f.savefig(b, format="png"); plt.close(f)
json.dump({"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"display_name": "Python 3"}}, "cells": [
    {"cell_type": "markdown", "metadata": {}, "source": ["# NB title"]},
    {"cell_type": "code", "execution_count": 1, "metadata": {}, "source": ["x"], "outputs": [
        {"output_type": "execute_result", "execution_count": 1, "metadata": {}, "data": {"text/plain": ["array([0, 1])"]}},
        {"output_type": "display_data", "metadata": {}, "data": {"image/png": base64.b64encode(b.getvalue()).decode()}},
        {"output_type": "error", "ename": "E", "evalue": "v", "traceback": ["\u001b[31mBoomError\u001b[0m: boom"]}]}]},
    open(f"{d}/nb.ipynb", "w"))
open(f"{d}/kin.fasta", "w").write(">BTK\nGSWEIDPKDLTFLKELG\n>ITK\nGSWVIDPSELTFVQEIG\n>TEC\nGSWLIEPSELVFIKELG\n")
open(f"{d}/hits.a3m", "w").write(">q\nMKTAYIAKQR\n>h1\nMKTaaAYIAK-R\n>h2\nMK-AYIgAKQR\n")
open(f"{d}/pair.aln", "w").write("CLUSTAL W (1.83)\n\nseqA   MKTAYIAKQR\nseqB   MKSAYIAKQR\n       **:*******\n")
open(f"{d}/one.fa", "w").write(">long single\n" + "ACDEFGHIKLMNPQRSTVWY" * 7 + "\n")
open(f"{d}/page.html", "w").write("<!doctype html><html><head></head><body><p>hello</p></body></html>")
open(f"{d}/dash.html", "w").write("<!doctype html><body><p id=n></p><script>fetch('/api/now').then(r => r.json())</script></body>")
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
# documents, logs, an unaligned FASTA, and a stand-in `mafft` (pads with gaps) so aligning is testable
"$py" - "$T/fix" <<'PY'
import sys; d = sys.argv[1]
open(f"{d}/hits.csv", "w").write("compound,IC50_nM,ligand efficiency\nA-1,12.5,0.41\nA-2,3,0.44\nA-10,250,0.30\n")
open(f"{d}/cfg.json", "w").write('{"model": {"chains": ["A", "B"], "res": 1.7}, "note": "</script> safe"}')
open(f"{d}/unal.fasta", "w").write(">long\nMKTAYIAKQRQISFVKSHFSRQ\n>short\nMKTAYIAKQR\n")
open(f"{d}/refine.log", "w").write("""
 ###############################################################
 ### CCP4 9.0: Refmac_5.8.0425  version 5.8.0425 : 13/07/24###
 $TABLE: Rfactor analysis, stats vs cycle  :
$GRAPHS:<Rfactor> vs cycle :N:1,2,3:
$$
    Ncyc    Rfact    Rfree     FOM      -LL     -LLfree  rmsBOND  zBOND rmsANGL  zANGL rmsCHIRAL $$
$$
       0   0.2222   0.2346   0.671      392212.   20818.5   0.0241  2.176   2.307  1.325   0.138
       1   0.2223   0.2345   0.662      392497.   20825.8   0.0118  0.980   2.015  1.104   0.103
       2   0.2219   0.2343   0.646      392656.   20850.7   0.0103  0.829   1.892  1.014   0.097
 $$
 $TEXT:Result: $$ Final results $$
                      Initial    Final
           R factor    0.2222   0.2219
             R free    0.2346   0.2343
     Rms BondLength    0.0241   0.0103
      Rms BondAngle    2.3070   1.8920
""")
open(f"{d}/phenix.log", "w").write("""phenix.refine: macromolecular structure refinement
Start R-work = 0.2532, R-free = 0.2803
 stage                r-work r-free bonds angles b_min b_max b_ave n_water shift
     0     :          0.2532 0.2803 0.010  1.172   5.0  80.3  24.1    0  0.000
   1_bss   :          0.2481 0.2765 0.010  1.172   5.0  80.3  24.1    0  0.000
   1_xyz   :          0.2301 0.2611 0.008  1.010   5.0  80.3  24.1    0  0.120
   end     :          0.1910 0.2246 0.007  0.950   5.0  80.3  24.1    0  0.050
Final R-work = 0.1910, R-free = 0.2246
""")
open(f"{d}/build.log", "w").write("make: all done\n")
PY
mkdir -p "$T/stub"
cat > "$T/stub/mafft" <<STUB
#!$py
import sys
seqs, name = [], None
for line in open(sys.argv[-1]):
    line = line.strip()
    if line.startswith(">"): seqs.append([line[1:], ""])
    elif seqs: seqs[-1][1] += line
L = max(len(s) for _, s in seqs)
for n, s in seqs: print(">" + n); print(s + "-" * (L - len(s)))
STUB
chmod +x "$T/stub/mafft"

# ------------------------------------------------------------------ static checks
echo "static"
check "imgwatch.py compiles" "'$py' -m py_compile '$repo/bin/imgwatch.py'"
check "shell scripts parse" "bash -n '$repo/bin/imgpane' && bash -n '$repo/bin/imgwatch' && bash -n '$repo'/hooks/*.sh && bash -n '$repo/install.sh'"
check "setup.sh parses and refuses to run unattended" "bash -n '$repo/setup.sh' && ! '$repo/setup.sh' </dev/null >/dev/null 2>&1"
check "imgwatch-plugin parses; verify rejects an unsigned copy" "bash -n '$repo/bin/imgwatch-plugin' && mkdir -p '$T/fake/iTermBrowserPlugin.app/Contents' && printf '<plist><dict><key>CFBundleIdentifier</key><string>com.googlecode.iterm2.iTermBrowserPlugin</string></dict></plist>' > '$T/fake/iTermBrowserPlugin.app/Contents/Info.plist' && ! '$repo/bin/imgwatch-plugin' verify '$T/fake/iTermBrowserPlugin.app' >/dev/null"
if [ -d /Applications/iTermBrowserPlugin.app ]; then
  check "imgwatch-plugin verifies the installed plugin" "'$repo/bin/imgwatch-plugin' verify /Applications/iTermBrowserPlugin.app >/dev/null"
fi
if [ -d /Applications/iTermBrowserPlugin.app ]; then
  mkdir -p "$T/Downloads" && ditto /Applications/iTermBrowserPlugin.app "$T/Downloads/iTermBrowserPlugin.app"
  check "imgwatch-plugin move relocates a copy out of Downloads" "'$repo/bin/imgwatch-plugin' move '$T/Downloads/iTermBrowserPlugin.app' -d '$T/Apps' >/dev/null && [ -d '$T/Apps/iTermBrowserPlugin.app' ] && [ ! -e '$T/Downloads/iTermBrowserPlugin.app' ]"
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
echo x > "$T/fresh.pdb"
"$py" -c 'import json,sys; print(json.dumps({"session_id":"S","cwd":sys.argv[1],"tool_name":"Bash","tool_input":{"command":"imgwatch --annotate "+sys.argv[1]+"/fresh.pdb"},"tool_response":{"stdout":"mark-up page opened"}}))' "$T" | hook
check "hook: an imgwatch command's own file argument is not re-queued" "[ ! -s '$q' ]"
rm -f "$q"
"$py" -c 'import json,base64; d=base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode(); print(json.dumps({"session_id":"S","tool_name":"mcp__claude-in-chrome__computer","tool_input":{"action":"screenshot"},"tool_response":[{"type":"text","text":"ok"},{"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":d}},{"type":"image","mimeType":"image/png","data":d}]}))' | hook
check "hook: inline images in a tool result saved and queued (both block shapes)" "[ \$(grep -c '/.claude/imgwatch/inline/chrome-computer_' '$q') -eq 2 ] && ls '$HOME/.claude/imgwatch/inline/'*.jpg '$HOME/.claude/imgwatch/inline/'*.png >/dev/null"
rm -f "$q"
"$py" -c 'import json,base64; d=base64.b64encode(b"x").decode(); print(json.dumps({"session_id":"S","tool_name":"Read","tool_input":{"file_path":"/a/notes.txt"},"tool_response":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":d}}]}))' | hook
check "hook: Read results are not re-captured as inline images" "[ ! -s '$q' ]"
printf '{"session_id":"S","tool_name":"Write","tool_input":{"file_path":"/a/n.md"}}' | hook
printf '{"session_id":"S","tool_name":"Read","tool_input":{"file_path":"/a/r.md"}}' | hook
printf '{"session_id":"S","tool_name":"Write","tool_input":{"file_path":"%s/.claude/memory/m.md"}}' "$HOME" | hook
check "hook: Markdown only on Write/Edit, never under ~/.claude" "[ \"\$(cut -f2 '$q' | tr '\n' ' ')\" = '/a/n.md ' ]"
rm -f "$q"; enq /a/x.png; enq /a/y.bcif; enq /a/z.pdf; enq /a/w.mp4; enq /a/v.HEIC; enq /a/notes.txt; enq /a/data.csv; enq /a/nb.ipynb; enq /a/aln.a3m
check "enqueue hook: media in, text out" "[ \"\$(cut -f2 '$q' | tr '\n' ' ')\" = '/a/x.png /a/y.bcif /a/z.pdf /a/w.mp4 /a/v.HEIC /a/nb.ipynb /a/aln.a3m ' ]"
check "enqueue hook: line format session<TAB>path" "head -1 '$q' | grep -q \$'^S\t/a/x.png\$'"
rm -f "$q"

# ------------------------------------------------------------------ install
echo "install"
"$repo/install.sh" > "$T/install1.log" 2>&1
check "install.sh exits 0" "[ $? -eq 0 ]"
check "links point into the repo" "[ \"\$(readlink -f '$HOME/bin/imgwatch')\" = '$repo/bin/imgwatch' ] && [ \"\$(readlink -f '$HOME/.claude/hooks/imgwatch-enqueue.sh')\" = '$repo/hooks/imgwatch-enqueue.sh' ]"
check "settings.json has all three hooks" "grep -q imgwatch-enqueue.sh '$HOME/.claude/settings.json' && grep -q imgpane-autostart.sh '$HOME/.claude/settings.json' && grep -q imgwatch-annotations.sh '$HOME/.claude/settings.json'"
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
rm -f "$q"
shot() { "$py" -c 'import json,base64; d=base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode(); print(json.dumps({"session_id":"S","tool_name":"mcp__claude-in-chrome__computer","tool_input":{"action":"screenshot"},"tool_response":[{"type":"text","text":"ok\nTab Context:\n- Executed on tabId: 2\n- Available tabs:\n  \u2022 tabId 1: \"Bank\" (\"https://secure.chase.com/\")\n  \u2022 tabId 2: \"PDB\" (\"https://www.rcsb.org/structure/8FD9\")"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":d}}]}))'; }
shot | IMGWATCH_INLINE=off hook; shot | IMGWATCH_INLINE_SKIP_SITES=rcsb.org hook; shot | IMGWATCH_INLINE_SKIP_TOOLS=chrome hook
check "hook: inline images off / skipped by site (subdomains too) / by tool" "[ ! -s '$q' ]"
shot | IMGWATCH_INLINE_SKIP_SITES=chase.com hook
check "hook: a skip-listed site in another tab doesn't hide this tab's screenshot" "[ \$(grep -c inline '$q') -eq 1 ]"
rm -f "$q"
rd() { printf '{"session_id":"S","tool_name":"%s","tool_input":{"file_path":"%s"}}' "$1" "$2" | hook; }
rd Read "$T/fix/notes.md"; rd Read "$T/fix/hits.csv"; rd Read "$T/fix/build.log"
check "hook: documents not shown on Read, plain logs never" "[ ! -s '$q' ]"
rd Write "$T/fix/hits.csv"; rd Read "$T/fix/refine.log"; printf '{"session_id":"S","tool_name":"Read","tool_input":{"file_path":"%s"}}' "$T/fix/notes.md" | IMGWATCH_DOCS_ON_READ=1 hook
check "hook: CSV on Write, REFMAC log on Read, Markdown on Read with IMGWATCH_DOCS_ON_READ=1" "grep -q hits.csv '$q' && grep -q refine.log '$q' && grep -q notes.md '$q'"
rm -f "$c"/new*.png; : > "$q"

# ------------------------------------------------------------------ rendering
echo "rendering (headless watcher, link mode)"
cd "$T/watch"
sleep 600 & OWN=$!   # stands in for the Claude CLI (--owner): the control channel is keyed on it
IMGWATCH_URLMAP="$T/watch/served.html=http://127.0.0.1:9/dash/;$T/watch/site=http://127.0.0.1:9/" \
PATH="$T/stub:$PATH" IMGWATCH_NO_OPEN=1 TERM_PROGRAM=iTerm.app COLUMNS=100 "$repo/bin/imgwatch" --session S1 --owner $OWN --html-mode link "$T/watch" > "$T/watch.log" 2>&1 & W=$!
check "watcher starts with a banner" "waitfor '+ hook queue' 5"
cp "$T/fix/single.png" "$T/watch/"
check "PNG: header with clickable name" "waitfor '${E}]8;;file://localhost$T/watch/single.png' 8"
cp "$T/fix"/burst_*.png "$T/watch/"
if "$py" -c 'import PIL' 2>/dev/null; then
  # FSEvents occasionally delivers a burst in two groups (e.g. 6 + 2), so only require that a sheet is made
  check "burst of 8 stills: contact sheet" "waitfor ' images' 10 && log | grep -qE '[0-9]+ images' && for i in \$(seq 20); do ls '$c'/sheet_*.png >/dev/null 2>&1 && break; sleep 0.3; done; ls '$c'/sheet_*.png >/dev/null 2>&1"
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
cp "$T/fix/dash.html" "$T/watch/served.html"
check "HTML served by a local server (IMGWATCH_URLMAP file): link to its URL" "waitfor '${E}]8;;http://127.0.0.1:9/dash/${E}\\' 8"
mkdir -p "$T/watch/site"; cp "$T/fix/page.html" "$T/watch/site/a b.html"
check "IMGWATCH_URLMAP folder: URL + quoted relative path" "waitfor '${E}]8;;http://127.0.0.1:9/a%20b.html${E}\\' 8"
cp "$T/fix/dash.html" "$T/watch/"
check "unmapped page that fetch()es its server: hint to map it" "waitfor 'map it to its URL with IMGWATCH_URLMAP' 8"
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
sq() { printf 'S1\t%s\n' "$1" >> "$q"; }
cp "$T/fix/notes.md" "$T/watch/"; sleep 2.5
check "Markdown in a watched folder is ignored (hook only)" "! log | grep -qF 'markdown → page'"
sq "$T/fix/notes.md"
if waitfor 'markdown → page' 8 && waitfor 'notes.md' 20 && sleep 1 && ls "$c"/md2_*notes.md.html >/dev/null 2>&1; then
  pg=$(ls "$c"/md2_*notes.md.html | head -1)
  check "Markdown: page with marked.js, wikilinks/embeds/frontmatter converted" "grep -q 'marked' '$pg' && grep -q 'the other' '$pg' && grep -q '(&lt;single.png&gt;)' '$pg' && grep -q 'class=\"fm\"' '$pg' && grep -q '<base href=\"file://localhost$T/fix/\">' '$pg'"
else log | grep -q 'cannot fetch marked' && skp "Markdown (marked.js not cached and no network)" || bad "Markdown: no page"; fi
sq "$T/fix/nb.ipynb"
if waitfor 'notebook → page' 8 && sleep 1 && ls "$c"/nb1_*nb.ipynb.html >/dev/null 2>&1; then
  pg=$(ls "$c"/nb1_*nb.ipynb.html | head -1)
  check "notebook: code, text/plain, PNG output and ANSI-free traceback" "grep -q 'array(\[0, 1\])' '$pg' && grep -q 'data:image/png;base64' '$pg' && grep -q 'BoomError' '$pg' && ! grep -q \$'\x1b' '$pg'"
else log | grep -q 'cannot fetch marked' && skp "notebook (marked.js not cached and no network)" || bad "notebook: no page"; fi
sq "$T/fix/kin.fasta"
check "alignment (FASTA): columns, identity, conservation row" "waitfor '3 sequences × 17 columns' 8 && sleep 1 && grep -q conservation \$(ls '$c'/aln4_*kin.fasta.html | head -1)"
sq "$T/fix/hits.a3m"
check "alignment (A3M): lowercase insertions dropped" "waitfor '3 sequences × 10 columns' 8"
sq "$T/fix/pair.aln"
check "alignment (Clustal): parsed, consensus line skipped" "waitfor '2 sequences × 10 columns, 90% mean identity' 8"
sq "$T/fix/one.fa"
check "single sequence: wrapped in numbered 60-residue lines" "waitfor '1 sequence, longest 140' 8 && sleep 1 && grep -q '>121 ' \$(ls '$c'/aln4_*one.fa.html | head -1)"
cp "$T/fix/single.png" "$T/fix/again.png"; sq "$T/fix/again.png"; sleep 3; sq "$T/fix/again.png"
check "same file twice in a row: replaced in place (↻2)" "waitfor '↻2 ' 8"
printf 'pin\t%s\n' "$T/fix/single.png" >> "$HOME/.claude/imgwatch/control_$OWN"
check "control channel: pin shows the pinned item" "waitfor '📌' 8 && waitfor 'pinned (p or' 2"
printf 'unpin\t\n' >> "$HOME/.claude/imgwatch/control_$OWN"; sleep 1.5
printf 'annotate\t%s\n' "$T/fix/single.png" >> "$HOME/.claude/imgwatch/control_$OWN"
if waitfor 'mark up single.png' 8; then
  url=$(log | grep -ao 'http://127.0.0.1:[0-9]*/[A-Za-z0-9_-]*/a/[0-9]*' | tail -1)
  check "mark-up page served on localhost" "curl -sf '$url' | grep -q 'Send to Claude'"
  imgurl=${url/\/a\//\/img\/}; badurl=${url%/a/*}x/a/1
  check "mark-up image served; wrong token refused" "curl -sf '$imgurl' | head -c 8 | LC_ALL=C grep -aq PNG && ! curl -sf '$badurl' >/dev/null"
  curl -sf -X POST -H 'Content-Type: application/json' --data '{"marks":[{"kind":"box","x":10,"y":12,"w":40,"h":30,"note":"label overlaps"},{"kind":"point","x":100,"y":80,"note":""}],"note":"bigger font"}' "$url" > "$T/post.json"
  check "marks saved: marked-up PNG + record, shown in the pane" "grep -q '\"ok\": *true' '$T/post.json' && waitfor '2 marks saved' 8 && ls '$HOME/.claude/imgwatch/annotations/'*_marked.png >/dev/null && [ -s '$HOME/.claude/imgwatch/annotations/S1.jsonl' ]"
  printf '{"session_id":"S1","prompt":"fix it"}' | "$repo/hooks/imgwatch-annotations.sh" > "$T/ctx1.txt"
  printf '{"session_id":"S1","prompt":"again"}' | "$repo/hooks/imgwatch-annotations.sh" > "$T/ctx2.txt"
  check "prompt hook: marks reach Claude once (box, point, notes, marked copy)" "grep -q 'box x 10-50, y 12-42: label overlaps' '$T/ctx1.txt' && grep -q 'point (100, 80): (no note)' '$T/ctx1.txt' && grep -q 'Overall note: bigger font' '$T/ctx1.txt' && grep -q '_marked.png' '$T/ctx1.txt' && [ ! -s '$T/ctx2.txt' ]"
else bad "mark-up: page not opened"; fi
sq "$T/fix/unal.fasta"
check "unaligned FASTA: aligned with mafft (stub), identity computed" "waitfor 'aligned here with mafft' 10 && log | grep -q '2 sequences × 22 columns'"
sq "$T/fix/hits.csv"
check "CSV: sortable table, numeric column detected" "waitfor 'table: 3 rows × 3 columns' 8 && sleep 0.5 && grep -q 'data-num=\"1\">IC50_nM' \$(ls '$c'/tab1_*hits.csv.html | head -1)"
sq "$T/fix/cfg.json"
check "JSON: tree page, </script> in a string can't break out" "waitfor 'JSON object, 2 keys' 8 && sleep 0.5 && grep -q '<\\\\/script> safe' \$(ls '$c'/json1_*cfg.json.html | head -1)"
sq "$T/fix/refine.log"
check "REFMAC log: cycles, R and Rfree first→last" "waitfor 'REFMAC5 · 2 cycles · Rfact 0.2222→0.2219, Rfree 0.2346→0.2343' 8"
sq "$T/fix/phenix.log"
check "phenix.refine log: stage table" "waitfor 'phenix.refine · 4 steps · Rwork 0.2532→0.1910, Rfree 0.2803→0.2246' 8"
sq "$T/fix/build.log"; sleep 2
check "a log that isn't from refinement is ignored" "! log | grep -qF 'build.log'"
printf 'prev\t\n' >> "$HOME/.claude/imgwatch/control_$OWN"
check "history: ← shows an earlier item on its own" "waitfor '◀ ' 6"
for i in 1 2 3 4 5 6 7 8; do printf 'next\t\n' >> "$HOME/.claude/imgwatch/control_$OWN"; done; sleep 2
check "history: → past the newest returns to live" "[ \$(log | grep -c '◀ ') -ge 1 ] && log | tail -40 | grep -q 'phenix.log'"
printf 'compare\t%s\t%s\n' "$T/fix/burst_1.png" "$T/fix/burst_2.png" >> "$HOME/.claude/imgwatch/control_$OWN"
if waitfor 'compare burst_1.png → burst_2.png' 8; then
  curl_c=$(log | grep -ao 'http://127.0.0.1:[0-9]*/[A-Za-z0-9_-]*/c/[0-9]*' | tail -1); imgb=${curl_c/\/c\//\/imgb\/}
  check "compare page: slider/blink/side/difference, both images served" "curl -sf '$curl_c' | grep -q 'Difference' && curl -sf '$imgb' | head -c 8 | LC_ALL=C grep -aq PNG"
else bad "compare: page not opened"; fi
printf 'annotate\t%s\n' "$T/fix/tiny.pdb" >> "$HOME/.claude/imgwatch/control_$OWN"
if waitfor 'pick atoms in tiny.pdb' 8; then
  surl=$(log | grep -ao 'http://127.0.0.1:[0-9]*/[A-Za-z0-9_-]*/s/[0-9]*' | tail -1)
  if curl -sf "$surl" > "$T/pick.html"; then
    check "structure mark-up: Mol* page with the atom-picking panel" "grep -q 'setupPick' '$T/pick.html' && grep -q 'THR A   1' '$T/pick.html'"
    curl -sf -X POST -H 'Content-Type: application/json' --data '{"kind":"atoms","atoms":[{"chain":"A","resn":"THR","resi":1,"ins":"","atom":"CA","alt":"","element":"C","x":16.967,"y":12.784,"z":4.338,"b":10.8,"model":1,"note":"why here?"}],"note":"","shot":""}' "$surl" > "$T/postatoms.json"
    printf '{"session_id":"S1","prompt":"look"}' | "$repo/hooks/imgwatch-annotations.sh" > "$T/ctx3.txt"
    check "picked atoms reach Claude: label, CID, PyMOL selection, xyz, note" "grep -q '\"ok\": *true' '$T/postatoms.json' && waitfor '1 atom picked' 6 && grep -qF 'A/THR 1/CA  [CID //A/1/CA | PyMOL chain A and resi 1 and name CA | xyz (16.967, 12.784, 4.338)]: why here?' '$T/ctx3.txt'"
  else log | grep -q 'cannot fetch Mol' && skp "atom picking (Mol* not cached and no network)" || bad "atom picking: page not served"; fi
else bad "structure mark-up: picking page not opened"; fi
"$py" -c 'import json,time,sys; print(json.dumps({"orig":"/x/old.png","src":"/x/old.png","size":[10,10],"marks":[{"kind":"point","x":1,"y":2,"note":""}],"note":"","marked":"/x/old_marked.png","time":"20200101-000000","epoch":time.time()-7200}))' > "$HOME/.claude/imgwatch/annotations/S1.jsonl"
printf '{"session_id":"S1","prompt":"new topic"}' | "$repo/hooks/imgwatch-annotations.sh" > "$T/ctx4.txt"
check "prompt hook: marks older than 30 min listed in one line with their age, not attached" "grep -q 'older mark-up, probably made for an earlier message' '$T/ctx4.txt' && grep -q '2.0 h ago' '$T/ctx4.txt' && ! grep -q 'point (1, 2)' '$T/ctx4.txt'"
printf 'S1\t%s\n' "$T/fix/single.png" >> "$q"; printf 'OTHER\t%s\n' "$T/fix/burst_0.png" >> "$q"
sleep 3
check "queue: own session's file shown again" "[ \$(log | grep -c 'single.png') -ge 2 ]"
check "queue: other session's file ignored" "! log | grep -qF '$T/fix/burst_0.png'"
check "no Python traceback in the log" "! log | grep -q Traceback"
kill $W $OWN 2>/dev/null; W=

echo "self-reload"
mkdir -p "$T/rb"; cp "$repo/bin/imgwatch.py" "$T/rb/"
sleep 600 & OWN2=$!
IMGWATCH_NO_OPEN=1 TERM_PROGRAM=iTerm.app COLUMNS=100 "$py" "$T/rb/imgwatch.py" --session S2 --owner $OWN2 --html-mode link --no-poll > "$T/watch.log" 2>&1 & W=$!
check "second watcher starts" "waitfor '+ hook queue' 5"
printf 'S2\t%s\n' "$T/fix/single.png" >> "$q"; waitfor 'single.png' 6
printf '\n# changed\n' >> "$T/rb/imgwatch.py"
check "imgwatch.py changed: restarts in place (same pid), shows what it showed" "waitfor 'restarting with the new version' 8 && waitfor 'reloaded: now' 8 && kill -0 $W && [ \$(log | grep -c 'single.png') -ge 2 ]"
printf '\ndef broken(:\n' >> "$T/rb/imgwatch.py"
check "a version that doesn't compile is reported, the old one keeps running" "waitfor \"doesn't compile\" 8 && sleep 1 && kill -0 $W"
kill $W $OWN2 2>/dev/null; W=

printf '\n%d passed, %d failed, %d skipped\n' $pass $fail $skip
[ $fail -eq 0 ]

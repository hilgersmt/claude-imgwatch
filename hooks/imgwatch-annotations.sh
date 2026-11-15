#!/bin/bash
# UserPromptSubmit hook: hand marks the user made in the imgwatch annotate page (key `a` in the
# pane, or `imgwatch --annotate`) to Claude along with the message they just sent. The text this
# prints becomes context for that prompt. Records are moved aside first, so each is sent once.
# Marks older than IMGWATCH_MARK_MAX_MIN minutes (default 30) were probably made for an earlier
# message: they are listed in one line with their age instead of in full. Never blocks Claude.
set -a; [ -f ~/.config/imgwatch/config ] && . ~/.config/imgwatch/config; set +a
"${IMGWATCH_PYTHON:-python3}" -S -c '
import json, os, sys, time
d = json.load(sys.stdin)
sid = d.get("session_id") or ""
base = os.path.expanduser("~/.claude/imgwatch/annotations")
src = os.path.join(base, sid + ".jsonl")
if not sid or not os.path.exists(src):
    sys.exit(0)
done = os.path.join(base, "%s.sent-%s-%d.jsonl" % (sid, time.strftime("%Y%m%d-%H%M%S"), os.getpid()))
try:
    os.replace(src, done)
except OSError:
    sys.exit(0)
recs = []
for line in open(done):
    try:
        recs.append(json.loads(line))
    except ValueError:
        pass
if not recs:
    sys.exit(0)
try:
    max_min = float(os.environ.get("IMGWATCH_MARK_MAX_MIN") or 30)
except ValueError:
    max_min = 30.0
now = time.time()
def epoch(r):
    if r.get("epoch"):
        return float(r["epoch"])
    try:
        return time.mktime(time.strptime(r.get("time", ""), "%Y%m%d-%H%M%S"))
    except ValueError:
        return now
def ago(s):
    s = max(0, int(s))
    return "just now" if s < 60 else "%d min ago" % (s // 60) if s < 5400 else "%.1f h ago" % (s / 3600)
fresh = [r for r in recs if now - epoch(r) <= max_min * 60]
stale = [r for r in recs if r not in fresh]
out = []
if fresh:
    na = sum(r.get("kind") == "atoms" for r in fresh)
    ni = len(fresh) - na
    what = " and ".join(x for x in (
        ("an image" if ni == 1 else "%d images" % ni) if ni else "",
        ("picked atoms in a structure" if na == 1 else "picked atoms in %d structures" % na) if na else "") if x)
    out.append("[imgwatch] Before sending this message the user " + ("marked up " if ni else "") + what
               + " in the imgwatch pane."
               + (" Read the marked-up copy to see exactly what they point at." if ni else
                  " The atoms are exact (chain/residue/atom from the model); the view snapshot shows what they were looking at."))
for r in fresh:
    if r.get("kind") == "atoms":
        out.append("\nStructure: %s  (atoms picked in Mol*, %s)" % (r.get("orig"), ago(now - epoch(r))))
        if r.get("marked"):
            out.append("  View when sent: %s" % r["marked"])
        for i, m in enumerate(r.get("atoms", []), 1):
            out.append("  %d. %s  [CID %s | PyMOL %s | xyz %s]: %s" % (
                i, m.get("label"), m.get("cid"), m.get("pymol"), m.get("xyz"), m.get("note") or "(no note)"))
        if r.get("note"):
            out.append("  Overall note: %s" % r["note"])
        continue
    w, h = r.get("size", ["?", "?"])
    out.append("\nImage: %s  (marked %s)" % (r.get("orig"), ago(now - epoch(r))))
    if r.get("src") != r.get("orig"):
        out.append("  (marks are in the pixels of %s, %sx%s)" % (r.get("src"), w, h))
    else:
        out.append("  (%sx%s px)" % (w, h))
    out.append("  Marked-up copy: %s" % r.get("marked"))
    for i, m in enumerate(r.get("marks", []), 1):
        x, y = m.get("x", 0), m.get("y", 0)
        if m.get("kind") == "box":
            where = "box x %d-%d, y %d-%d" % (x, x + m.get("w", 0), y, y + m.get("h", 0))
        else:
            where = "point (%d, %d)" % (x, y)
        out.append("  %d. %s: %s" % (i, where, m.get("note") or "(no note)"))
    if r.get("note"):
        out.append("  Overall note: %s" % r["note"])
if stale:
    out.append(("\n" if out else "") + "[imgwatch] Also %d older mark-up%s, probably made for an earlier message "
               "(not attached in full; read only if this message refers to them): %s" % (
               len(stale), "" if len(stale) == 1 else "s",
               "; ".join("%s (%s, %s)" % (r.get("marked") or r.get("orig"), os.path.basename(str(r.get("orig"))), ago(now - epoch(r)))
                         for r in stale)))
print("\n".join(out))
' 2>/dev/null
exit 0

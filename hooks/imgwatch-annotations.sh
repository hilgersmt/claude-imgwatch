#!/bin/bash
# UserPromptSubmit hook: hand marks the user made in the imgwatch annotate page (key `a` in the
# pane, or `imgwatch --annotate`) to Claude along with the message they just sent. The text this
# prints becomes context for that prompt. Records are moved aside first, so each is sent once.
# Never blocks Claude.
[ -f ~/.config/imgwatch/config ] && . ~/.config/imgwatch/config
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
out = ["[imgwatch] Before sending this message the user marked up "
       + ("an image" if len(recs) == 1 else "%d images" % len(recs))
       + " in the imgwatch pane. Read the marked-up copy to see exactly what they point at."]
for r in recs:
    w, h = r.get("size", ["?", "?"])
    out.append("\nImage: %s" % r.get("orig"))
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
print("\n".join(out))
' 2>/dev/null
exit 0

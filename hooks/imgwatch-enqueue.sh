#!/bin/bash
# PostToolUse hook (all tools): append "session_id<TAB>media path" to the imgwatch queue.
#  - Read / Write / Edit: the tool's file_path, if it is a media file (always shown).
#  - Bash and MCP tools: media paths mentioned in the command or its output, if the file exists and
#    was written in the last 3 minutes (figures a script saved anywhere, PyMOL `png`, ...).
#    Paths may contain spaces: each candidate is grown leftwards until it names a real file.
# Keep EXTS in sync with bin/imgwatch.py (`imgwatch --doctor` checks it). Never blocks Claude.
[ -f ~/.config/imgwatch/config ] && . ~/.config/imgwatch/config
"${IMGWATCH_PYTHON:-python3}" -S -c '
import json, os, re, sys, time
EXTS = (".png",".jpg",".jpeg",".gif",".webp",".svg",".tif",".tiff",".bmp",
        ".mp4",".mov",".m4v",".webm",".avi",".mkv",".mpg",".mpeg",
        ".html",".htm",".pdf",
        ".pdb",".ent",".cif",".mmcif",".bcif",".sdf",".mol",".mol2",
        ".smi",".smiles",".pse",
        ".pptx",".ppt",".docx",".doc",".xlsx",".xls",".key",".pages",".numbers")
d = json.load(sys.stdin)
sid, tool, cwd = d.get("session_id", ""), d.get("tool_name", ""), d.get("cwd") or os.getcwd()
ti = d.get("tool_input") or {}
out = []
fp = ti.get("file_path", "") if isinstance(ti, dict) else ""
if fp and fp.lower().endswith(EXTS):
    out.append(fp)
if tool == "Bash" or tool.startswith("mcp__"):
    texts = []
    def walk(x):
        if isinstance(x, str): texts.append(x)
        elif isinstance(x, dict): [walk(v) for v in x.values()]
        elif isinstance(x, list): [walk(v) for v in x]
    walk(ti); walk(d.get("tool_response"))
    home, cache = os.path.expanduser("~"), os.path.expanduser("~/.claude/imgwatch")
    rx = re.compile(r"\.(" + "|".join(e[1:] for e in EXTS) + r")(?![\w])", re.I)
    now = time.time()
    for line in "\n".join(texts)[:400000].splitlines():
        for m in list(rx.finditer(line))[:50]:
            end, hit = m.end(), None
            # candidate starts: after each separator-ish char to the left (longest name first)
            for i in range(max(0, end - 400), end):
                if i == 0 or line[i - 1] in " \t\"\x27=:(,[<>`|;":
                    c = line[i:end].strip()
                    if not c or c[0] not in "/~." and not c[0].isalnum():
                        continue
                    c = os.path.expanduser(c)
                    c = c if os.path.isabs(c) else os.path.join(cwd, c)
                    if os.path.isfile(c):
                        hit = os.path.realpath(c); break
            if hit and not hit.startswith(cache) and now - os.path.getmtime(hit) < 180 and hit not in out:
                out.append(hit)
if out:
    q = os.path.expanduser("~/.claude/imgwatch/queue")
    os.makedirs(os.path.dirname(q), exist_ok=True)
    with open(q, "a") as f:
        f.write("".join(f"{sid}\t{p}\n" for p in out[:50]))
' 2>/dev/null
exit 0

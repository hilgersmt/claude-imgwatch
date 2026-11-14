#!/bin/bash
# PostToolUse hook (all tools): append "session_id<TAB>media path" to the imgwatch queue.
#  - Read / Write / Edit: the tool's file_path, if it is a media file (always shown).
#    Markdown only on Write/Edit (Claude reads notes constantly) and never under ~/.claude
#    (memory, plans, settings).
#  - Bash and MCP tools: media paths mentioned in the command or its output, if the file exists and
#    was written in the last 3 minutes (figures a script saved anywhere, PyMOL `png`, ...).
#    Paths may contain spaces: each candidate is grown leftwards until it names a real file.
#  - Any tool but Read: images returned inline in the result (Chrome screenshots, MCP tools that
#    hand back an image instead of a file) are saved to ~/.claude/imgwatch/inline and queued.
# Keep EXTS in sync with bin/imgwatch.py (`imgwatch --doctor` checks it). Never blocks Claude.
[ -f ~/.config/imgwatch/config ] && . ~/.config/imgwatch/config
"${IMGWATCH_PYTHON:-python3}" -S -c '
import base64, binascii, hashlib, json, os, re, sys, time
EXTS = (".png",".jpg",".jpeg",".gif",".webp",".svg",".tif",".tiff",".bmp",".heic",".heif",
        ".mp4",".mov",".m4v",".webm",".avi",".mkv",".mpg",".mpeg",
        ".html",".htm",".pdf",
        ".pdb",".ent",".cif",".mmcif",".bcif",".sdf",".mol",".mol2",
        ".smi",".smiles",".pse",
        ".pptx",".ppt",".docx",".doc",".xlsx",".xls",".key",".pages",".numbers",
        ".ipynb",".fasta",".fa",".faa",".fas",".aln",".clustal",".a3m",".sto",".stk",
        ".md",".markdown")
MD = (".md", ".markdown")
SCAN = tuple(e for e in EXTS if e not in MD)   # never pick notes out of Bash output
IMG = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
d = json.load(sys.stdin)
sid, tool, cwd = d.get("session_id", ""), d.get("tool_name", ""), d.get("cwd") or os.getcwd()
ti = d.get("tool_input") or {}
home = os.path.expanduser("~")
state = os.path.join(home, ".claude/imgwatch")
out = []
fp = ti.get("file_path", "") if isinstance(ti, dict) else ""
if fp and fp.lower().endswith(EXTS):
    if not fp.lower().endswith(MD):
        out.append(fp)
    elif tool in ("Write", "Edit", "MultiEdit") and not os.path.realpath(fp).startswith(os.path.join(home, ".claude") + "/"):
        out.append(fp)
if tool == "Bash" or tool.startswith("mcp__"):
    texts = []
    def walk(x):
        if isinstance(x, str): texts.append(x)
        elif isinstance(x, dict): [walk(v) for k, v in x.items() if k != "data"]
        elif isinstance(x, list): [walk(v) for v in x]
    walk(ti); walk(d.get("tool_response"))
    rx = re.compile(r"\.(" + "|".join(e[1:] for e in SCAN) + r")(?![\w])", re.I)
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
            if hit and not hit.startswith(state) and now - os.path.getmtime(hit) < 180 and hit not in out:
                out.append(hit)
if tool != "Read":
    # inline images: Anthropic blocks {"type":"image","source":{"media_type","data"}} and
    # MCP blocks {"type":"image","mimeType","data"}
    imgs = []
    def find(x):
        if isinstance(x, dict):
            if x.get("type") == "image":
                src = x.get("source") if isinstance(x.get("source"), dict) else x
                mt, data = src.get("media_type") or src.get("mimeType"), src.get("data")
                if mt in IMG and isinstance(data, str):
                    imgs.append((mt, data)); return
            [find(v) for v in x.values()]
        elif isinstance(x, list):
            [find(v) for v in x]
    find(d.get("tool_response"))
    if imgs:
        idir = os.path.join(state, "inline")
        os.makedirs(idir, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "-", tool.replace("mcp__", "").replace("claude-in-", ""))[:40].strip("-")
        stamp = time.strftime("%H%M%S")
        for n, (mt, data) in enumerate(imgs[:10]):
            try:
                raw = base64.b64decode(data)
            except (binascii.Error, ValueError):
                continue
            p = os.path.join(idir, f"{name}_{stamp}_{hashlib.sha1(raw).hexdigest()[:6]}{IMG[mt]}")
            with open(p, "wb") as f:
                f.write(raw)
            out.append(p)
if out:
    q = os.path.join(state, "queue")
    os.makedirs(state, exist_ok=True)
    with open(q, "a") as f:
        f.write("".join(f"{sid}\t{p}\n" for p in out[:50]))
' 2>/dev/null
exit 0

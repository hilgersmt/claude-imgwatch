#!/bin/bash
# PostToolUse hook (all tools): append "session_id<TAB>media path" to the imgwatch queue.
#  - Read / Write / Edit: the tool's file_path, if it is a media file (always shown).
#    Documents (Markdown, CSV/TSV, JSON) only on Write/Edit (Claude reads them constantly; set
#    IMGWATCH_DOCS_ON_READ=1 to show them on Read too) and never under ~/.claude (memory, plans,
#    settings).
#  - Bash and MCP tools: media paths mentioned in the command or its output, if the file exists and
#    was written in the last 3 minutes (figures a script saved anywhere, PyMOL `png`, ...).
#    Paths may contain spaces: each candidate is grown leftwards until it names a real file.
#    Never Markdown from here.
#  - .log files only if they are REFMAC or phenix.refine logs (shown as an R/Rfree summary).
#  - Any tool but Read: images returned inline in the result (Chrome screenshots, MCP tools that
#    hand back an image instead of a file) are saved to ~/.claude/imgwatch/inline and queued, unless
#    IMGWATCH_INLINE=off, the tool matches IMGWATCH_INLINE_SKIP_TOOLS, or the page it shows matches
#    IMGWATCH_INLINE_SKIP_SITES (comma-separated; "example.com" also covers its subdomains, globs ok).
# Keep EXTS in sync with bin/imgwatch.py (`imgwatch --doctor` checks it). Never blocks Claude.
set -a; [ -f ~/.config/imgwatch/config ] && . ~/.config/imgwatch/config; set +a
"${IMGWATCH_PYTHON:-python3}" -S -c '
import base64, binascii, fnmatch, hashlib, json, os, re, sys, time
EXTS = (".png",".jpg",".jpeg",".gif",".webp",".svg",".tif",".tiff",".bmp",".heic",".heif",
        ".mp4",".mov",".m4v",".webm",".avi",".mkv",".mpg",".mpeg",
        ".html",".htm",".pdf",
        ".pdb",".ent",".cif",".mmcif",".bcif",".sdf",".mol",".mol2",
        ".smi",".smiles",".pse",
        ".pptx",".ppt",".docx",".doc",".xlsx",".xls",".key",".pages",".numbers",
        ".ipynb",".fasta",".fa",".faa",".fas",".aln",".clustal",".a3m",".sto",".stk",
        ".md",".markdown",".csv",".tsv",".json",".log")
MD = (".md", ".markdown")
DOCS = MD + (".csv", ".tsv", ".json")
SCAN = tuple(e for e in EXTS if e not in MD)   # never pick notes out of Bash output
IMG = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
env = os.environ.get
def csv_env(k):
    return [x.strip().lower() for x in (env(k) or "").split(",") if x.strip()]
def refine_log(p):
    try:
        with open(p, "rb") as f:
            head = f.read(200000)
    except OSError:
        return False
    return b"REFMAC" in head or b"Refmac" in head or b"phenix.refine" in head
d = json.load(sys.stdin)
sid, tool, cwd = d.get("session_id", ""), d.get("tool_name", ""), d.get("cwd") or os.getcwd()
ti = d.get("tool_input") or {}
home = os.path.expanduser("~")
claude_dir = os.path.join(home, ".claude") + "/"
state = os.path.join(home, ".claude/imgwatch")
out = []
fp = ti.get("file_path", "") if isinstance(ti, dict) else ""
if fp and fp.lower().endswith(EXTS):
    low = fp.lower()
    if low.endswith(".log"):
        if refine_log(fp):
            out.append(fp)
    elif not low.endswith(DOCS):
        out.append(fp)
    elif (tool in ("Write", "Edit", "MultiEdit") or (tool == "Read" and env("IMGWATCH_DOCS_ON_READ") == "1")) \
            and not os.path.realpath(fp).startswith(claude_dir):
        out.append(fp)
cmd = ti.get("command", "") if isinstance(ti, dict) else ""
own = tool == "Bash" and isinstance(cmd, str) and re.match(r"\s*(\S*/)?imgwatch(\.py)?\s", cmd)
if (tool == "Bash" and not own) or tool.startswith("mcp__"):   # `imgwatch --annotate X`: X is already shown
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
                if hit.lower().endswith(".log") and not refine_log(hit):
                    continue
                if hit.lower().endswith(DOCS) and hit.startswith(claude_dir):
                    continue
                out.append(hit)
def page_hosts(resp, ti):
    """Hostnames of the page an image shows: the Chrome tab the action ran on (from the result text),
    else every tab listed, else a url in the tool input."""
    texts = []
    def walk(x):
        if isinstance(x, str): texts.append(x)
        elif isinstance(x, dict): [walk(v) for k, v in x.items() if k != "data"]
        elif isinstance(x, list): [walk(v) for v in x]
    walk(resp)
    t = "\n".join(texts)
    tabs = dict(re.findall(r"tabId (\d+): \"[^\n]*?\" \(\"([^\"]+)\"\)", t))
    ex = re.search(r"Executed on tabId: (\d+)", t)
    urls = [tabs[ex.group(1)]] if ex and ex.group(1) in tabs else list(tabs.values())
    if not urls and isinstance(ti, dict) and isinstance(ti.get("url"), str):
        urls = [ti["url"]]
    hosts = []
    for u in urls:
        m = re.match(r"^[a-z][a-z0-9+.-]*://([^/:?#]+)", u.strip(), re.I)
        if m:
            hosts.append(m.group(1).lower())
    return hosts
def skip_site(hosts, pats):
    for h in hosts:
        for p in pats:
            if h == p or h.endswith("." + p) or fnmatch.fnmatch(h, p):
                return True
    return False
if tool != "Read" and env("IMGWATCH_INLINE", "on").lower() not in ("off", "0", "no") \
        and not any(s in tool.lower() for s in csv_env("IMGWATCH_INLINE_SKIP_TOOLS")):
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
    sites = csv_env("IMGWATCH_INLINE_SKIP_SITES")
    if imgs and sites and skip_site(page_hosts(d.get("tool_response"), ti), sites):
        imgs = []
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

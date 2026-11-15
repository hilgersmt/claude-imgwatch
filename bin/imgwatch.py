#!/usr/bin/env python3
"""imgwatch: show new media inline as Claude reads or creates it.

Sources:
  1. queue file fed by the PostToolUse hook ~/.claude/hooks/imgwatch-enqueue.sh
     (files Claude opens with Read or writes with Write)
  2. watched dirs (FSEvents via `watchdog` if installed, else polling) for new/modified
     media files (matplotlib, PyMOL, RDKit output...)

Shows stills inline, videos as animated-GIF previews, PDFs as their first page, HTML in an
iTerm2 browser pane (or as a headless screenshot / link), structures (.pdb/.cif/.bcif/.sdf/.mol2)
in a Mol* viewer, and Markdown notes, notebooks and sequence alignments as generated pages.
Bursts of stills become one contact sheet; a file shown again right after itself replaces its entry.
Keys in the pane: ←/→ browse, p pin, a mark up (structures: pick atoms; reaches Claude with the next
prompt), b before/after, c copy, o open, r redraw, ? help. Restarts itself when imgwatch.py changes.

Run it in a split pane; `imgpane` opens the split for you.  `imgwatch --doctor` checks the setup.
"""
import argparse, base64, hashlib, html, json, os, re, secrets, select, shutil, signal, subprocess, sys
import threading, time, urllib.parse, urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMGCAT = "/Applications/iTerm.app/Contents/Resources/utilities/imgcat"
STATE = Path.home() / ".claude/imgwatch"
QUEUE = STATE / "queue"
CACHE = STATE / "cache"
VENDOR = STATE / "vendor"
BROWSER_STATE = STATE / "browser_session"
STILL_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff", ".bmp", ".heic", ".heif"}
# iTerm2's inline-image protocol animates GIFs, so a video is shown by
# transcoding a capped preview to an animated GIF and drawing that.
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".mpg", ".mpeg"}
# HTML can't be drawn inline at all. Three lanes, see --html-mode:
#   browser  push file:// to an iTerm2 Browser-profile pane (real JS, real interactivity)
#   shot     headless-Chrome screenshot, drawn inline like any other still
#   link     just print a clickable file:// hyperlink
HTML_EXTS = {".html", ".htm"}
PDF_EXTS = {".pdf"}
# Structures go through the HTML lanes as a generated Mol* page with the data embedded.
STRUCT_FORMATS = {".pdb": "pdb", ".ent": "pdb", ".cif": "mmcif", ".mmcif": "mmcif", ".bcif": "mmcif",
                  ".sdf": "sdf", ".mol": "mol", ".mol2": "mol2"}
STRUCT_EXTS = set(STRUCT_FORMATS)
# Compounds: SMILES lists always, and 2D or multi-molecule SDF/MOL files, become an RDKit grid
# (a 3D single molecule still goes to Mol*).
SMILES_EXTS = {".smi", ".smiles"}
PSE_EXTS = {".pse"}  # PyMOL sessions: ray-traced snapshot via headless PyMOL
# Office/iWork: Quick Look renders the first slide/page natively
OFFICE_EXTS = {".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".key", ".pages", ".numbers"}
# Generated pages (HTML lanes): Markdown notes, notebooks, alignments, tables, JSON, refinement logs.
# Markdown, tables and JSON arrive only through the hook (Write/Edit, Bash output for tables/JSON;
# never under ~/.claude); the folder watcher ignores them, or every README, data file and config
# touched in the working dir would pop up. Logs too, and only REFMAC / phenix.refine ones are shown.
NOTE_EXTS = {".md", ".markdown"}
NB_EXTS = {".ipynb"}
ALN_EXTS = {".fasta", ".fa", ".faa", ".fas", ".aln", ".clustal", ".a3m", ".sto", ".stk"}
TABLE_EXTS = {".csv", ".tsv"}
JSON_EXTS = {".json"}
LOG_EXTS = {".log"}
PAGE_EXTS = HTML_EXTS | STRUCT_EXTS | NOTE_EXTS | NB_EXTS | ALN_EXTS | TABLE_EXTS | JSON_EXTS | LOG_EXTS
EXTS = (STILL_EXTS | VIDEO_EXTS | HTML_EXTS | PDF_EXTS | STRUCT_EXTS | SMILES_EXTS | PSE_EXTS | OFFICE_EXTS
        | NOTE_EXTS | NB_EXTS | ALN_EXTS | TABLE_EXTS | JSON_EXTS | LOG_EXTS)
WATCH_EXTS = EXTS - NOTE_EXTS - TABLE_EXTS - JSON_EXTS - LOG_EXTS
INLINE = STATE / "inline"            # images the hook pulled out of tool results
ANNOT = STATE / "annotations"        # marks made in the annotate page, waiting for the next prompt
MARKED_VERSION = "12.0.2"
MERMAID_VERSION = "10.9.1"
FFMPEG = next((f for f in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
               if os.path.exists(f)), "ffmpeg")
MOLSTAR_VERSION = os.environ.get("IMGWATCH_MOLSTAR_VERSION", "5.10.1")
SKIP = {"node_modules", ".git", "__pycache__", "Library"}

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("dirs", nargs="*", help="dirs to watch (default: Claude scratch root + cwd)")
ap.add_argument("-w", "--width", default="100%", help="image width, e.g. 100%%, 80 (cells), 600px")
ap.add_argument("-i", "--interval", type=float, default=1.0, help="loop interval in s (default 1)")
ap.add_argument("-d", "--depth", type=int, default=5, help="max dir depth to watch (default 5)")
ap.add_argument("--no-poll", action="store_true", help="only show files from the hook queue")
ap.add_argument("--poll", action="store_true", help="poll dirs even if watchdog (FSEvents) is installed")
ap.add_argument("-k", "--keep", type=int, default=4, help="items to redraw after a pane resize (default 4)")
ap.add_argument("--owner", type=int, help="exit when this process (the Claude CLI) ends")
ap.add_argument("-s", "--session", help="Claude session id: show only that session's files and watch its scratch dir")
ap.add_argument("--burst", type=int, default=6, metavar="N",
                help="more than N stills arriving together become one contact sheet (default 6; 0 = never)")
ap.add_argument("--video-seconds", type=float, default=8.0, metavar="S",
                help="seconds of video to preview as an animated GIF (default 8; 0 = whole clip)")
ap.add_argument("--video-fps", type=float, default=10.0, metavar="N", help="GIF frame rate (default 10)")
ap.add_argument("--video-px", type=int, default=720, metavar="W", help="GIF pixel width (default 720)")
ap.add_argument("--no-video", action="store_true", help="ignore video files entirely")
ap.add_argument("--html-mode", choices=["auto", "browser", "shot", "link", "off"], default="auto",
                help="how to show .html and structures: browser pane, headless screenshot, clickable link, "
                     "or ignore (auto = browser if --browser-profile is set, else shot, else link)")
ap.add_argument("--browser-profile", metavar="NAME", default=os.environ.get("IMGWATCH_BROWSER_PROFILE"),
                help="name of an iTerm2 Browser-type profile; its pane is reused for every page "
                     "(env IMGWATCH_BROWSER_PROFILE)")
ap.add_argument("--keep-browser", action="store_true", help="leave the browser pane open when imgwatch exits")
ap.add_argument("--shot-wait", type=float, default=2.0, metavar="S",
                help="seconds to let JS render before screenshotting (default 2)")
ap.add_argument("--shot-px", type=int, default=1400, metavar="W", help="screenshot viewport width (default 1400)")
ap.add_argument("--html-max-mb", type=float, default=40.0, metavar="MB",
                help="skip .html / structure files bigger than this (default 40)")
ap.add_argument("--pymol", metavar="CMD", default=os.environ.get("IMGWATCH_PYMOL"),
                help="python that can 'import pymol', for .pse snapshots (env IMGWATCH_PYMOL; default: this python)")
ap.add_argument("--max-mols", type=int, default=48, metavar="N", help="molecules drawn per compound grid (default 48)")
ap.add_argument("--cache-days", type=float, default=7, metavar="D", help="prune cache files older than D days (default 7)")
ap.add_argument("--cache-mb", type=float, default=500, metavar="MB", help="cap the cache at MB, oldest first (default 500)")
ap.add_argument("--no-replace", action="store_true",
                help="always append; by default a file shown again right after itself replaces its entry")
ap.add_argument("--max-seqs", type=int, default=300, metavar="N", help="alignment rows drawn (default 300)")
ap.add_argument("--pin", metavar="PATH", help="(from a Claude session) pin PATH at the top of this session's pane")
ap.add_argument("--unpin", action="store_true", help="(from a Claude session) release the pin")
ap.add_argument("--annotate", nargs="?", const="", metavar="PATH",
                help="(from a Claude session) open PATH, or the latest image, for marking up")
ap.add_argument("--no-reload", action="store_true",
                help="don't restart in place when imgwatch.py changes on disk (git pull, an edit)")
ap.add_argument("--compare", nargs="*", metavar="PATH",
                help="(from a Claude session) before/after page: two images, or none for pinned/previous vs newest")
ap.add_argument("-V", "--version", action="store_true", help="print the version and exit")
ap.add_argument("--doctor", action="store_true", help="check the installation and exit")
a = ap.parse_args()
if a.session:  # one browser pane per Claude session, not one shared by all of them
    BROWSER_STATE = BROWSER_STATE.with_name(f"browser_session_{a.session}")


def version():
    """'v0.0.5 (0aa9345)' on a release tag, 'v0.0.5+3 (abc1234)' three commits after it,
    '0aa9345 2026-11-13' with no tags (e.g. a shallow copy), 'unknown' outside git."""
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(HERE), *args], capture_output=True, text=True,
                                  timeout=3).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    d = git("describe", "--tags", "--match", "v[0-9]*", "--long", "--dirty")
    m = re.match(r"^(v[\d.]+)-(\d+)-g([0-9a-f]+)(-dirty)?$", d)
    if m:
        tag, ahead, sha, dirty = m.groups()
        return f"{tag}{'+' + ahead if ahead != '0' else ''} ({sha}{', modified' if dirty else ''})"
    return git("log", "-1", "--format=%h %cs") or "unknown"


# ---------------------------------------------------------------- terminal backends
def terminal():
    """iterm | wezterm | kitty (kitty graphics protocol: kitty, Ghostty)."""
    tp = os.environ.get("TERM_PROGRAM", "")
    if tp == "iTerm.app":
        return "iterm"
    if tp == "WezTerm":
        return "wezterm"
    if tp == "ghostty" or os.environ.get("KITTY_WINDOW_ID") or os.environ.get("TERM") == "xterm-kitty":
        return "kitty"
    return "iterm"


TERM = terminal()


def width_cells():
    cols = shutil.get_terminal_size((80, 24)).columns
    w = a.width.strip()
    if w.endswith("%"):
        return max(1, int(cols * float(w[:-1]) / 100) - 1)
    if w.endswith("px"):
        return max(1, int(float(w[:-2]) / 8))
    try:
        return max(1, int(w))
    except ValueError:
        return cols - 1


MAX_ROWS = None  # while something is pinned: cap each image's height (cells) so pin + newest both fit


def draw_image(src):
    """Draw one image file inline in the current terminal."""
    if TERM == "iterm" and os.path.exists(IMGCAT):
        # -l: one escape sequence per image; the multipart protocol can garble if other output interleaves
        subprocess.run([IMGCAT, "-l", "-r", "-W", a.width] + (["-H", str(MAX_ROWS)] if MAX_ROWS else []) + [src])
    elif TERM in ("iterm", "wezterm"):
        # iTerm2 inline-image protocol, single sequence (WezTerm implements it too)
        data = base64.b64encode(Path(src).read_bytes()).decode()
        h = f"height={MAX_ROWS};" if MAX_ROWS else ""
        sys.stdout.write(f"\033]1337;File=inline=1;width={a.width};{h}preserveAspectRatio=1;"
                         f"size={os.path.getsize(src)}:{data}\a\n")
        sys.stdout.flush()
    else:
        # kitty graphics protocol takes PNG only (static: GIFs show their first frame)
        png = src
        if not src.lower().endswith(".png"):
            CACHE.mkdir(parents=True, exist_ok=True)
            png = str(CACHE / (hashlib.sha1(src.encode()).hexdigest()[:16] + "_k.png"))
            subprocess.run(["sips", "-s", "format", "png", src, "--out", png], capture_output=True)
        data = base64.b64encode(Path(png).read_bytes()).decode()
        chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)] or [""]
        for i, c in enumerate(chunks):
            head = f"f=100,a=T,c={width_cells()},q=2," if i == 0 else ""
            sys.stdout.write(f"\033_G{head}m={0 if i == len(chunks) - 1 else 1};{c}\033\\")
        sys.stdout.write("\n")
        sys.stdout.flush()


def clear_all():
    # clear screen + scrollback (iTerm2 keeps stale, mis-sized images in scrollback)
    seq = "\033]1337;ClearScrollback\a" if TERM == "iterm" else ""
    print(seq + "\033[2J\033[3J\033[H", end="", flush=True)


def link(url, text):
    """OSC 8 hyperlink: cmd-click opens it."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def file_url(p):
    return "file://localhost" + urllib.parse.quote(os.path.abspath(p))


# Pages that load their data from a web server (fetch("/api/...")) render empty as file://.
# IMGWATCH_URLMAP maps a file or folder to the URL that serves it, so the live page is shown
# instead: "PATH=URL" entries separated by ';' or newlines, e.g.
#   IMGWATCH_URLMAP="~/claude-admin/energy/dashboard.html=http://127.0.0.1:8790/"
# A folder maps every page under it (URL + relative path). Longest match wins.
def parse_urlmap(raw):
    out = []
    for ent in re.split(r"[;\n]+", raw or ""):
        path, eq, url = ent.strip().partition("=")
        if eq and path.strip() and re.match(r"https?://", url.strip()):
            out.append((os.path.realpath(os.path.expanduser(path.strip())), url.strip()))
    return sorted(out, key=lambda t: -len(t[0]))


URLMAP = parse_urlmap(os.environ.get("IMGWATCH_URLMAP"))
NEEDS_SERVER = re.compile(r"""fetch\(\s*[`'"]/(?!/)""")   # fetch("/api/...") = same-origin server call


def served_url(p):
    """The live URL for a page a local server serves (IMGWATCH_URLMAP), else None."""
    rp = os.path.realpath(p)
    for path, url in URLMAP:
        if rp == path:
            return url
        if rp.startswith(path.rstrip("/") + "/"):
            return url.rstrip("/") + "/" + urllib.parse.quote(os.path.relpath(rp, path))
    return None


def needs_server(p):
    try:
        with open(p, "r", errors="replace") as f:
            return bool(NEEDS_SERVER.search(f.read(2_000_000)))
    except OSError:
        return False


# ---------------------------------------------------------------- housekeeping
def prune():
    """Startup housekeeping: cap the cache, trim the queue, drop stale browser-pane state."""
    now = time.time()
    if CACHE.exists():
        files = [f for f in CACHE.iterdir() if f.is_file()]
        for f in files:
            try:
                if now - f.stat().st_mtime > a.cache_days * 86400:
                    f.unlink()
            except OSError:
                pass
        files = sorted((f for f in CACHE.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > a.cache_mb * 1e6:
            f = files.pop(0)
            total -= f.stat().st_size
            f.unlink(missing_ok=True)
    try:  # queue is append-only and shared; keep its last 64 kB once it passes 1 MB
        if QUEUE.stat().st_size > 1_000_000:
            with open(QUEUE, "r+b") as fh:
                fh.seek(-65536, 2)
                tail = fh.read()
                tail = tail[tail.find(b"\n") + 1:]
                fh.seek(0); fh.write(tail); fh.truncate()
    except OSError:
        pass
    for d in (INLINE, ANNOT):
        for f in d.glob("*") if d.exists() else []:
            try:
                if now - f.stat().st_mtime > a.cache_days * 86400:
                    f.unlink()
            except OSError:
                pass
    for f in STATE.glob("control_*"):
        try:
            if now - f.stat().st_mtime > 86400:
                f.unlink()
        except OSError:
            pass
    for pat in ("browser_session_*", "reload_*.json"):
        for f in STATE.glob(pat):
            try:
                if now - f.stat().st_mtime > 86400:
                    f.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- doctor
def live_profile_names(timeout=5):
    """Profile names iTerm2 has actually loaded (Python API), or None if it can't be asked."""
    if "IMGWATCH_DOCTOR_LIVE" in os.environ:  # tests: pretend iTerm2 loaded these (comma-separated)
        return {n for n in os.environ["IMGWATCH_DOCTOR_LIVE"].split(",") if n}
    if os.environ.get("IMGWATCH_DOCTOR_OFFLINE"):  # tests: never talk to the real iTerm2
        return None
    code = ("import iterm2\n"
            "async def m(c):\n"
            "    for p in await iterm2.PartialProfile.async_query(c): print(p.name)\n"
            "iterm2.run_until_complete(m)\n")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return set(r.stdout.splitlines()) if r.returncode == 0 and r.stdout.strip() else None


def doctor():
    rc = 0
    def out(level, msg):
        nonlocal rc
        col = {"ok": "32", "warn": "33", "FAIL": "31"}[level]
        print(f"  \033[{col}m{level:>4}\033[0m  {msg}")
        if level == "FAIL":
            rc = 1
    print(f"\033[1mimgwatch doctor\033[0m  ({version()}, {HERE})")
    print(f"  python  {sys.executable} {sys.version.split()[0]}")
    cfg = Path.home() / ".config/imgwatch/config"
    if cfg.exists():
        py = re.search(r"^IMGWATCH_PYTHON=(.*)$", cfg.read_text(), re.M)
        py = py.group(1).strip().strip('"\'') if py else ""
        out("ok" if py and os.access(py, os.X_OK) else "FAIL", f"config {cfg} (IMGWATCH_PYTHON={py or 'unset'})")
    else:
        out("warn", f"no {cfg}; defaults apply (python3, browser profile 'imgbrowser')")
    # terminal
    if Path("/Applications/iTerm.app").exists():
        v = subprocess.run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString",
                            "/Applications/iTerm.app/Contents/Info.plist"], capture_output=True, text=True).stdout.strip()
        ok37 = tuple(int(x) for x in re.findall(r"\d+", v)[:2]) >= (3, 7)
        out("ok" if ok37 else "warn", f"iTerm2 {v}" + ("" if ok37 else " (browser profiles need 3.7+)"))
        out("ok" if os.path.exists(IMGCAT) else "warn", f"imgcat {IMGCAT}")
    else:
        out("warn", "iTerm2 not found (WezTerm/kitty/Ghostty support is experimental)")
    # python modules
    for mod, why in (("iterm2", "browser lane"), ("selenium", "screenshot lane"),
                     ("watchdog", "FSEvents instead of polling"), ("PIL", "contact sheets")):
        try:
            __import__(mod); out("ok", f"module {mod}")
        except ImportError:
            out("warn", f"module {mod} missing ({why} off)")
    # tools
    out("ok" if shutil.which(FFMPEG) or os.path.exists(FFMPEG) else "warn", f"ffmpeg ({FFMPEG}) for video")
    al = find_aligner()
    out("ok" if al else "warn", f"{al[0]} ({al[1]}) aligns unaligned FASTA" if al else
        "no MAFFT / Clustal Omega: unaligned FASTA is listed, not aligned (optional: brew install mafft)")
    out("ok" if Path("/Applications/Google Chrome.app").exists() else "warn", "Google Chrome for the screenshot lane")
    mjs = VENDOR / f"molstar-{MOLSTAR_VERSION}.js"
    out("ok" if mjs.exists() else "warn", f"Mol* {MOLSTAR_VERSION} " + ("cached" if mjs.exists() else "not cached yet (fetched on first structure)"))
    # iTerm2 API + browser profile
    plist = Path.home() / "Library/Preferences/com.googlecode.iterm2.plist"
    prof = os.environ.get("IMGWATCH_BROWSER_PROFILE", "imgbrowser")
    profiles = []
    if plist.exists():
        import plistlib
        d = plistlib.load(open(plist, "rb"))
        out("ok" if d.get("EnableAPIServer") else "warn", "iTerm2 Python API enabled" if d.get("EnableAPIServer")
            else "iTerm2 Python API off (Settings > General > Magic > Enable Python API)")
        profiles = list(d.get("New Bookmarks", []))
    dyn = Path.home() / "Library/Application Support/iTerm2/DynamicProfiles"
    for f in dyn.glob("*") if dyn.exists() else []:
        try:
            profiles += json.loads(f.read_text()).get("Profiles", [])
        except (ValueError, OSError):
            pass
    if prof:
        hit = [p for p in profiles if p.get("Name") == prof]
        if not hit:
            out("warn", f"no iTerm2 profile '{prof}' (HTML falls back to screenshots)")
        elif hit[0].get("Custom Command") != "Browser":
            out("FAIL", f"profile '{prof}' exists but is not a Browser-type profile")
        else:
            # A Browser profile on disk is not enough: without the iTerm2 Browser Plugin, iTerm2 silently
            # drops every Browser-type profile and splits fail with INVALID_PROFILE_NAME. Ask iTerm2.
            live = live_profile_names()
            if live is None:
                out("ok", f"profile '{prof}' is a Browser profile (iTerm2 not reachable; not checked live)")
            elif prof in live:
                out("ok", f"profile '{prof}' is a Browser profile, loaded by iTerm2")
            else:
                out("FAIL", f"profile '{prof}' is defined but iTerm2 has not loaded it: install the iTerm2 "
                            f"Browser Plugin ({HERE / 'imgwatch-plugin'} install), then restart iTerm2")
    # hooks and links
    sp = Path.home() / ".claude/settings.json"
    try:
        hooks = json.loads(sp.read_text()).get("hooks", {})
    except (OSError, ValueError):
        hooks = {}
        out("FAIL", f"cannot read {sp}")
    for ev, name in (("PostToolUse", "imgwatch-enqueue.sh"), ("SessionStart", "imgpane-autostart.sh"),
                     ("UserPromptSubmit", "imgwatch-annotations.sh")):
        cmds = [h.get("command", "") for g in hooks.get(ev, []) for h in g.get("hooks", [])]
        hit = [c for c in cmds if c.endswith(name)]
        if not hit:
            out("FAIL", f"{ev} hook {name} not in settings.json"); continue
        real = Path(os.path.expanduser(hit[0])).resolve()
        out("ok" if real.exists() and os.access(real, os.X_OK) else "FAIL", f"{ev} hook -> {real}")
        mt = [g.get("matcher", "") for g in hooks.get(ev, []) for h in g.get("hooks", []) if h.get("command") == hit[0]]
        if ev == "PostToolUse" and mt and mt[0] not in ("", "*"):
            out("warn", f"  enqueue hook only runs for '{mt[0]}' (Bash/MCP figures not picked up; re-run install.sh)")
        if real.exists() and HERE.parent not in real.parents:
            out("warn", f"  {name} is not the copy in this repo ({HERE.parent})")
    for b in ("imgwatch", "imgpane"):
        p = Path.home() / "bin" / b
        out("ok" if p.resolve().parent == HERE else "warn", f"~/bin/{b} -> {p.resolve() if p.exists() else 'missing'}")
    # the enqueue hook must know every extension imgwatch handles
    hook = HERE.parent / "hooks/imgwatch-enqueue.sh"
    hext = set(re.findall(r'"(\.[a-z0-9]+)"', hook.read_text())) if hook.exists() else set()
    missing = EXTS - hext
    out("ok" if not missing else "warn", "enqueue hook extensions match" if not missing
        else f"enqueue hook lacks {' '.join(sorted(missing))}")
    # the autostart hook's pgrep pattern must match how imgwatch really runs
    auto = HERE.parent / "hooks/imgpane-autostart.sh"
    m = re.search(r'^w="(.*)"', auto.read_text(), re.M) if auto.exists() else None
    if m:
        own = subprocess.Popen(["sleep", "30"])
        w = subprocess.Popen([str(HERE / "imgwatch"), "--owner", str(own.pid), "--no-poll", "--session", "doctor"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        pat = m.group(1).replace("$owner", str(own.pid)) + "( |$)"
        hit = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True).stdout.split()
        out("ok" if str(w.pid) in hit else "FAIL", "autostart pgrep pattern matches a running watcher"
            if str(w.pid) in hit else f"autostart pgrep pattern does not match the watcher ({pat})")
        own.kill(); w.kill()
    return rc


# ---------------------------------------------------------------- dirs
tmproot = Path(f"/private/tmp/claude-{os.getuid()}")
if a.dirs:
    dirs = [Path(d).expanduser().resolve() for d in a.dirs]
elif a.session:  # this session's scratch dir(s) appear lazily, so re-glob each loop (see session_dirs)
    dirs = [Path.cwd()] if Path.cwd() != Path.home() else []  # never watch all of ~
else:
    dirs = [tmproot, Path.cwd()]


def session_dirs():
    return dirs + (sorted(tmproot.glob(f"*/{a.session}")) if a.session and not a.dirs else [])


def wanted(path, root):
    """Media file within --depth of root, not under a skipped or hidden dir."""
    if os.path.splitext(path)[1].lower() not in WATCH_EXTS:
        return False
    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        return False
    return len(parts) - 1 <= a.depth and not any(p in SKIP or p.startswith(".") for p in parts[:-1])


def scan(root, depth):
    try:
        with os.scandir(root) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    if depth > 0 and e.name not in SKIP and not e.name.startswith("."):
                        yield from scan(e.path, depth - 1)
                elif e.is_file() and os.path.splitext(e.name)[1].lower() in WATCH_EXTS:
                    yield e.path
    except (PermissionError, FileNotFoundError, NotADirectoryError):
        pass


class FSWatch:
    """FSEvents via watchdog: no rescanning big trees every second. Falls back to polling."""
    def __init__(self):
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        self.pending, self.lock, self.watched = set(), threading.Lock(), {}
        self.obs = Observer()
        outer = self

        class H(FileSystemEventHandler):
            def __init__(self, root):
                self.root = root
            def on_any_event(self, ev):
                if ev.is_directory:
                    return
                p = getattr(ev, "dest_path", None) or ev.src_path
                if isinstance(p, bytes):
                    p = p.decode()
                if wanted(p, self.root):
                    with outer.lock:
                        outer.pending.add(p)
        self.H = H
        self.obs.start()

    def sync(self, roots):
        for r in roots:
            r = str(r)
            if r not in self.watched and os.path.isdir(r):
                self.watched[r] = self.obs.schedule(self.H(r), r, recursive=True)

    def drain(self):
        with self.lock:
            out, self.pending = self.pending, set()
        return sorted(out)

    def stop(self):
        self.obs.stop()


# ---------------------------------------------------------------- converters
def cache_key(p, *extra):
    try:
        k = f"{os.path.abspath(p)}|{os.stat(p).st_mtime_ns}|" + "|".join(map(str, extra))
    except FileNotFoundError:
        return None
    return hashlib.sha1(k.encode()).hexdigest()[:16]


def warn(msg):
    print(f"\033[33m  ({msg})\033[0m", flush=True)


def video_to_gif(p):
    """Transcode a capped preview of a video to an animated GIF (cached by path+mtime).
    iTerm2 animates GIFs inline, so this is what 'playing' in the pane means here."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = cache_key(p, a.video_seconds, a.video_fps, a.video_px)
    if not key:
        return None
    gif = CACHE / (key + ".gif")
    if gif.exists() and gif.stat().st_size > 0:
        return str(gif)
    vf = f"fps={a.video_fps:g},scale={a.video_px}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
    cmd = [FFMPEG, "-nostdin", "-y", "-loglevel", "error"]
    if a.video_seconds > 0:
        cmd += ["-t", str(a.video_seconds)]
    cmd += ["-i", p, "-vf", vf, "-loop", "0", str(gif)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
    except FileNotFoundError:
        warn("ffmpeg not found — cannot preview video"); return None
    except subprocess.TimeoutExpired:
        warn("ffmpeg timed out — video preview skipped"); return None
    if r.returncode != 0 or not gif.exists() or gif.stat().st_size == 0:
        err = (r.stderr or b"").decode(errors="replace").strip().splitlines()
        warn(f"video preview failed: {err[-1] if err else 'ffmpeg error'}")
        return None
    return str(gif)


def ql_png(p, px=1200):
    """Quick Look thumbnail (SVG, PDF first page, Office first slide/page) as a PNG in the cache."""
    key = cache_key(p, "ql", px)
    if not key:
        return None
    out = CACHE / (key + ".png")
    if not out.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = CACHE / f"ql_{key}"
        tmp.mkdir(exist_ok=True)
        subprocess.run(["qlmanage", "-t", "-s", str(px), "-o", str(tmp), p], capture_output=True)
        got = tmp / (os.path.basename(p) + ".png")
        if got.exists():
            if p.lower().endswith(".svg"):  # only SVGs get square padding; never crop a page's margins
                trim(got)
            got.replace(out)
        shutil.rmtree(tmp, ignore_errors=True)
    return str(out) if out.exists() else None


def heic_jpg(p):
    """iPhone HEIC/HEIF -> JPEG in the cache (sips; imgcat and Pillow can't read HEIC everywhere)."""
    key = cache_key(p, "heic")
    if not key:
        return None
    out = CACHE / (key + ".jpg")
    if not out.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = CACHE / (key + ".part.jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", p, "--out", str(tmp)], capture_output=True)
        if tmp.exists():
            tmp.replace(out)
    return str(out) if out.exists() else None


def trim(png):
    """Quick Look pads thumbnails to a square; crop the padding (colour of the bottom-right pixel)."""
    try:
        from PIL import Image, ImageChops
        im = Image.open(png).convert("RGB")
        bg = Image.new("RGB", im.size, im.getpixel((im.width - 1, im.height - 1)))
        box = ImageChops.difference(im, bg).getbbox()
        if box and box != (0, 0, im.width, im.height):
            im.crop(box).save(png)
    except Exception:
        pass


def pdf_pages(p):
    try:
        r = subprocess.run(["mdls", "-raw", "-name", "kMDItemNumberOfPages", p], capture_output=True, text=True, timeout=3)
        return int(r.stdout.strip())
    except (ValueError, OSError, subprocess.TimeoutExpired):
        try:  # not indexed (e.g. /tmp): count page objects
            return len(re.findall(rb"/Type\s*/Page[^s]", Path(p).read_bytes())) or None
        except OSError:
            return None


def read_mols(p):
    """Molecules + names from a SMILES list or an SDF/MOL file; None if RDKit is missing."""
    try:
        from rdkit import Chem, RDLogger
    except ImportError:
        return None
    RDLogger.DisableLog("rdApp.*")
    mols, names, bad = [], [], 0
    ext = os.path.splitext(p)[1].lower()
    if ext in SMILES_EXTS:
        for i, line in enumerate(Path(p).read_text(errors="replace").splitlines()):
            parts = line.replace(",", " ").replace("\t", " ").split()
            if not parts or parts[0].startswith("#") or parts[0].lower() in ("smiles", "smi"):
                continue
            m = Chem.MolFromSmiles(parts[0].split(" |")[0])
            if m is None:
                bad += 1; continue
            mols.append(m); names.append(" ".join(parts[1:])[:40] or f"#{i + 1}")
    else:
        sup = Chem.SDMolSupplier(p) if ext == ".sdf" else [Chem.MolFromMolFile(p)]
        for i, m in enumerate(sup):
            if m is None:
                bad += 1; continue
            mols.append(m); names.append((m.GetProp("_Name") if m.HasProp("_Name") else "")[:40] or f"#{i + 1}")
    return mols, names, bad


def is_3d_single(p):
    """A single molecule with real 3D coordinates (goes to Mol*); anything else gets a 2D grid."""
    got = read_mols(p)
    if not got or len(got[0]) != 1:
        return False
    m = got[0][0]
    if not m.GetNumConformers():
        return False
    conf = m.GetConformer()
    return conf.Is3D() and any(abs(z) > 1e-3 for _, _, z in conf.GetPositions().tolist())


def compound_grid(p):
    """RDKit grid of a compound file in the pane's dark style (transparent, white carbons)."""
    got = read_mols(p)
    if got is None:
        warn("RDKit not installed — cannot draw compounds"); return None, 0, 0
    mols, names, bad = got
    if not mols:
        return None, 0, bad
    key = cache_key(p, "grid", a.max_mols)
    out = CACHE / f"grid_{key}.png"
    if not out.exists():
        from rdkit.Chem import AllChem
        from rdkit.Chem.Draw import rdMolDraw2D
        shown = mols[:a.max_mols]
        for m in shown:
            AllChem.Compute2DCoords(m)
        cols = min(len(shown), 4)
        rows = (len(shown) + cols - 1) // cols
        w, h = 320, 240
        d = rdMolDraw2D.MolDraw2DCairo(cols * w, rows * h, w, h)
        o = d.drawOptions()
        o.setBackgroundColour((0, 0, 0, 0)); o.updateAtomPalette({6: (1, 1, 1, 1)})
        o.setLegendColour((0.85, 0.85, 0.85, 1)); o.legendFontSize = 16
        d.DrawMolecules(shown, legends=names[:a.max_mols])
        d.FinishDrawing()
        CACHE.mkdir(parents=True, exist_ok=True)
        out.write_bytes(d.GetDrawingText())
    return str(out), len(mols), bad


def pse_png(p):
    """Ray-traced snapshot of a PyMOL session from its saved view, via headless PyMOL."""
    key = cache_key(p, "pse")
    if not key:
        return None
    out = CACHE / f"pse_{key}.png"
    if out.exists():
        return str(out)
    CACHE.mkdir(parents=True, exist_ok=True)
    script = ("import sys, pymol; pymol.finish_launching(['pymol', '-qc']); from pymol import cmd; "
              "cmd.load(sys.argv[1]); cmd.set('ray_opaque_background', 1); "
              "cmd.png(sys.argv[2], width=1200, height=900, dpi=150, ray=1)")
    py = a.pymol or sys.executable
    try:
        r = subprocess.run([py, "-c", script, p, str(out)], capture_output=True, timeout=120,
                           cwd=str(CACHE))  # never write next to the session (read-only folders)
    except FileNotFoundError:
        warn(f"PyMOL python not found: {py}"); return None
    except subprocess.TimeoutExpired:
        warn("PyMOL took > 120 s — snapshot skipped"); return None
    if not out.exists():
        err = (r.stderr or b"").decode(errors="replace").strip().splitlines()
        warn(f"PyMOL could not render: {err[-1][:80] if err else 'no output (is pymol importable?)'}")
        return None
    return str(out)


def contact_sheet(paths):
    """One PNG tiling a burst of stills, labelled with file names."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    key = hashlib.sha1("|".join(f"{p}|{cache_key(p)}" for p in paths).encode()).hexdigest()[:16]
    out = CACHE / f"sheet_{key}.png"
    if out.exists():
        return str(out)
    n = len(paths)
    cols = 3 if n <= 9 else 4 if n <= 20 else 5
    tw, th, lab = 360, 270, 26
    rows = (n + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + lab)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except OSError:
        font = ImageFont.load_default()
    for i, p in enumerate(paths):
        lo = p.lower()
        src = ql_png(p, 600) if lo.endswith(".svg") else heic_jpg(p) if lo.endswith((".heic", ".heif")) else p
        x, y = (i % cols) * tw, (i // cols) * (th + lab)
        try:
            im = Image.open(src); im.seek(0); im = im.convert("RGB")
            im.thumbnail((tw - 12, th - 12))
            sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
        except Exception:
            d.text((x + 10, y + th // 2), "(unreadable)", fill=(200, 80, 80), font=font)
        name = os.path.basename(p)
        name = name if len(name) <= 38 else name[:17] + "…" + name[-18:]
        d.text((x + tw // 2, y + th + 4), name, fill=(200, 200, 200), font=font, anchor="ma")
    CACHE.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return str(out)


def vendor(fname, url, label):
    """A pinned JS/CSS library, fetched once into the vendor dir; its text, or None offline."""
    VENDOR.mkdir(parents=True, exist_ok=True)
    f = VENDOR / fname
    if not f.exists():
        try:
            print(f"\033[2m  fetching {label} (once)\033[0m", flush=True)
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
            tmp = f.with_suffix(f.suffix + ".part")
            tmp.write_bytes(data); tmp.replace(f)
        except OSError as e:
            warn(f"cannot fetch {label}: {e}"); return None
    return f.read_text(errors="replace")


def molstar_assets():
    got = []
    for ext in ("js", "css"):
        t = vendor(f"molstar-{MOLSTAR_VERSION}.{ext}",
                   f"https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer/molstar.{ext}",
                   f"Mol* {MOLSTAR_VERSION} {ext}")
        if t is None:
            return None
        got.append(t)
    return got


# Atom picking (mark up a structure): a panel over the Mol* page. A click on an atom adds it, with
# chain / residue / atom / altloc / xyz / B read through Mol*'s StructureProperties (not parsed from
# a display label); Send posts them, plus a snapshot of the view, to the annotate server.
PICK_CSS = """
#pick{position:absolute;top:8px;right:52px;width:320px;max-height:calc(100% - 16px);overflow:auto;z-index:10;
  background:#181818ee;border:1px solid #333;border-radius:8px;padding:10px;color:#ddd;font:13px -apple-system,sans-serif}
#pick b{color:#fff}#pick .hint{color:#888;margin:4px 0 8px}#pick ol{padding-left:20px;margin:6px 0}#pick li{margin:6px 0}
#pick .at{font:12px ui-monospace,Menlo,monospace;color:#ffd400}#pick .x{color:#888;cursor:pointer;float:right}
#pick input,#pick textarea{width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #333;border-radius:4px;padding:4px 6px;font:inherit;margin-top:3px}
#pick textarea{height:46px}#pick button{background:#2d6cdf;color:#fff;border:0;border-radius:5px;padding:5px 12px;font:inherit;cursor:pointer;margin:6px 6px 0 0}
#pick button.alt{background:#333}#pick #pmsg{color:#7ee787;margin-top:6px}
#pick .bar{display:flex;justify-content:space-between;align-items:center}
#pick.min{width:auto;padding:5px 10px;cursor:pointer}#pick.min .body{display:none}#pick.min:hover{border-color:#2d6cdf}
"""
PICK_HTML = """<div id="pick" class="min"><div class="bar"><b id="ptitle">✎ Pick atoms</b><span class="x" id="pmin"></span></div>
<div class="body"><div class="hint">click atoms in the view (drag still rotates) · add a note per atom · Send · Claude gets them with your next message</div>
<ol id="plist"></ol><textarea id="pnote" placeholder="Overall note (optional)"></textarea>
<button id="psend">Send to Claude</button><button class="alt" id="pundo">Undo</button><button class="alt" id="pclear">Clear</button>
<div id="pmsg"></div></div></div>"""
PICK_JS = """
function setupPick(v){
  var S=molstar.lib.structure, SE=S.StructureElement, SP=S.StructureProperties, picks=[];
  function list(){var ol=document.getElementById('plist');ol.innerHTML='';picks.forEach(function(a,i){
    var li=document.createElement('li'),inp=document.createElement('input'),x=document.createElement('span');
    li.innerHTML='<span class="at"></span>';li.firstChild.textContent=a.label;
    x.className='x';x.textContent='✕';x.onclick=function(){picks.splice(i,1);list();};li.appendChild(x);
    inp.placeholder='what about it?';inp.value=a.note||'';inp.oninput=function(){a.note=inp.value;};li.appendChild(inp);ol.appendChild(li);});}
  v.plugin.behaviors.interaction.click.subscribe(function(e){
    if(document.getElementById('pick').classList.contains('min')) return;   // panel closed: a click is just a click
    var l=e.current&&e.current.loci; if(!l||l.kind!=='element-loci') return;
    var loc=SE.Loci.getFirstLocation(l); if(!loc) return;
    var a={chain:SP.chain.auth_asym_id(loc), resn:SP.atom.auth_comp_id(loc), resi:SP.residue.auth_seq_id(loc),
      ins:SP.residue.pdbx_PDB_ins_code(loc)||'', atom:SP.atom.auth_atom_id(loc), alt:SP.atom.label_alt_id(loc)||'',
      element:SP.atom.type_symbol(loc), x:SP.atom.x(loc), y:SP.atom.y(loc), z:SP.atom.z(loc), b:SP.atom.B_iso_or_equiv(loc),
      model:SP.unit.model_num(loc), note:''};
    a.label=a.chain+'/'+a.resn+' '+a.resi+a.ins+'/'+a.atom+(a.alt?':'+a.alt:'');
    var last=picks[picks.length-1]; if(last&&last.label===a.label) return;
    picks.push(a);list();var ins=document.querySelectorAll('#plist input');if(ins.length)ins[ins.length-1].focus();});
  document.getElementById('pundo').onclick=function(){picks.pop();list();};
  document.getElementById('pclear').onclick=function(){picks=[];list();};
  // collapsed to a button until opened (?open=1 = opened from the a key / --annotate); picking only while open
  var box=document.getElementById('pick'), pmin=document.getElementById('pmin');
  function setOpen(o){box.classList.toggle('min',!o);pmin.textContent=o?'hide':'';}
  box.addEventListener('click',function(){if(box.classList.contains('min'))setOpen(true);});
  pmin.onclick=function(e){e.stopPropagation();setOpen(false);};
  setOpen(/[?&]open=1/.test(location.search));
  document.getElementById('psend').onclick=function(){
    var note=document.getElementById('pnote').value, msg=document.getElementById('pmsg');
    if(!picks.length&&!note.trim()){msg.textContent='Nothing picked yet.';return;}
    msg.textContent='Sending…';
    Promise.resolve(v.plugin.helpers.viewportScreenshot.getImageDataUri()).catch(function(){return '';}).then(function(shot){
      return fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({kind:'atoms',atoms:picks,note:note,shot:shot||''})});})
    .then(function(r){return r.json();}).then(function(j){
      if(j.ok){picks=[];document.getElementById('pnote').value='';list();}
      msg.textContent=j.ok?'Saved and cleared. It goes to Claude with your next message.':'Failed: '+j.error;})
    .catch(function(e){msg.textContent='Failed: '+e;});};
}
"""


def molstar_page(p, pick=False):
    """Self-contained Mol* page with the structure embedded (no file:// fetches needed);
    pick=True adds the atom-picking panel (served by the annotate server, which takes its Send)."""
    ext = os.path.splitext(p)[1].lower()
    assets = molstar_assets()
    if not assets:
        return None
    js, css = assets
    raw = Path(p).read_bytes()
    if ext == ".bcif":
        data = f'Uint8Array.from(atob("{base64.b64encode(raw).decode()}"), c => c.charCodeAt(0))'
    else:
        data = json.dumps(raw.decode(errors="replace"))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(os.path.basename(p))}</title>
<style>{css}</style><style>html,body{{margin:0;height:100%;background:#111}}#app{{position:absolute;inset:0}}{PICK_CSS if pick else ''}</style>
</head><body><div id="app"></div>{PICK_HTML if pick else ''}<script>{js}</script><script>{PICK_JS if pick else ''}
molstar.Viewer.create('app', {{layoutIsExpanded: false, layoutShowControls: false, layoutShowSequence: true,
  layoutShowLog: false, layoutShowLeftPanel: false, viewportShowExpand: false, viewportShowSelectionMode: false,
  viewportShowAnimation: false, viewportBackgroundColor: '#111111'}}).then(function (v) {{
  window.molstarViewer = v;{' setupPick(v);' if pick else ''}
  return v.loadStructureFromData({data}, {json.dumps(STRUCT_FORMATS[ext])}, {{dataLabel: {json.dumps(os.path.basename(p))}}});
}});
</script></body></html>"""


def structure_html(p):
    """The Mol* page for a structure, cached by path + mtime."""
    key = cache_key(p, "molstar2", MOLSTAR_VERSION)
    if not key:
        return None
    out = CACHE / (key + "_" + os.path.basename(p).replace(" ", "_") + ".html")
    if out.exists():
        return str(out)
    page = molstar_page(p)
    if page is None:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return str(out)


# ---------------------------------------------------------------- generated pages: notes, notebooks, alignments
PAGE_CSS = """
:root{color-scheme:dark}
html,body{margin:0;background:#111;color:#ddd;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif}
main{max-width:920px;margin:0 auto;padding:18px 20px 60px}
h1,h2,h3,h4{color:#fff;line-height:1.25;margin:1.3em 0 .5em}h1{font-size:1.7em}h2{font-size:1.35em;border-bottom:1px solid #333;padding-bottom:.2em}
a{color:#6cb6ff}code,pre{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
:not(pre)>code{background:#262626;padding:.1em .35em;border-radius:4px}
pre{background:#1b1b1b;border:1px solid #2c2c2c;border-radius:6px;padding:10px 12px;overflow:auto}
blockquote{margin:0;padding:0 1em;color:#aaa;border-left:3px solid #444}
table{border-collapse:collapse;margin:1em 0;display:block;overflow:auto}th,td{border:1px solid #3a3a3a;padding:4px 9px}th{background:#1e1e1e}
img{max-width:100%}hr{border:0;border-top:1px solid #333}
.meta{color:#888;font-size:13px;border-bottom:1px solid #2a2a2a;padding-bottom:8px;margin-bottom:8px}
.fm{font-size:12.5px;color:#9a9a9a;background:#161616;border:1px solid #262626;border-radius:6px;padding:6px 10px;white-space:pre-wrap}
.callout{border-left:3px solid #6cb6ff;background:#141a22;padding:6px 12px;border-radius:4px;margin:1em 0}
"""


def _md_prep(text):
    """Obsidian-isms marked.js doesn't know: frontmatter, ![[embeds]], [[wikilinks]]."""
    fm = ""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if m:
        fm, text = m.group(1), text[m.end():]
    text = re.sub(r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]", lambda m: f"![{m.group(1)}](<{m.group(1)}>)", text)
    text = re.sub(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]", lambda m: f"*{m.group(2) or m.group(1)}*", text)
    return fm, text


MD_JS = """<script>
(function(){
  marked.use({gfm:true, breaks:false});
  document.querySelectorAll('[data-md]').forEach(function(el){
    el.innerHTML = marked.parse(JSON.parse(el.getAttribute('data-md')));
  });
  document.querySelectorAll('blockquote').forEach(function(b){
    var p = b.querySelector('p'), m = p && p.innerHTML.match(/^\\s*\\[!(\\w+)\\][+-]?\\s*/);
    if (m) { b.className = 'callout';
      p.innerHTML = '<b>' + m[1].charAt(0).toUpperCase() + m[1].slice(1).toLowerCase() + '</b> ' + p.innerHTML.slice(m[0].length); }
  });
  if (window.mermaid) {
    document.querySelectorAll('code.language-mermaid').forEach(function(c){
      var d = document.createElement('div'); d.className = 'mermaid'; d.textContent = c.textContent;
      c.parentNode.replaceWith(d);
    });
    mermaid.initialize({startOnLoad:false, theme:'dark'}); mermaid.run();
  }
})();
</script>"""


def page_shell(title, body, base_dir, need_mermaid=False):
    """A generated page: dark style, marked.js (and mermaid when used) embedded, relative links
    and images resolved against the source file's folder. None if the libraries can't be had."""
    marked = vendor(f"marked-{MARKED_VERSION}.js",
                    f"https://cdn.jsdelivr.net/npm/marked@{MARKED_VERSION}/marked.min.js", f"marked {MARKED_VERSION}")
    if marked is None:
        return None
    mer = ""
    if need_mermaid:
        mer = vendor(f"mermaid-{MERMAID_VERSION}.js",
                     f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js",
                     f"mermaid {MERMAID_VERSION}") or ""
    base = "file://localhost" + urllib.parse.quote(str(base_dir)) + "/"
    return (f'<!doctype html><html><head><meta charset="utf-8"><base href="{base}"><title>{html.escape(title)}</title>'
            f'<style>{PAGE_CSS}</style><script>{marked}</script>' + (f"<script>{mer}</script>" if mer else "") +
            f'</head><body><main>{body}</main>{MD_JS}</body></html>')


def _page_out(p, tag):
    key = cache_key(p, tag)
    return (CACHE / f"{tag}_{key}_{os.path.basename(p).replace(' ', '_')}.html") if key else None


def md_html(p):
    out = _page_out(p, "md2")
    if out is None:
        return None
    if not out.exists():
        fm, text = _md_prep(Path(p).read_text(errors="replace"))
        body = (f'<div class="fm">{html.escape(fm)}</div>' if fm else "") + \
               f'<div data-md="{html.escape(json.dumps(text))}"></div>'
        page = page_shell(os.path.basename(p), body, Path(p).resolve().parent, "```mermaid" in text)
        if page is None:
            return None
        CACHE.mkdir(parents=True, exist_ok=True)
        out.write_text(page)
    return str(out)


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _nb_text(x):
    return "".join(x) if isinstance(x, list) else (x or "")


NB_CSS = """
.cell{margin:14px 0}.in{display:flex;gap:8px}.prompt{color:#5f87af;font:12px ui-monospace,Menlo,monospace;min-width:52px;text-align:right;padding-top:11px}
.in pre{flex:1;margin:0}.out{margin:6px 0 0 60px}.out pre{background:none;border:0;padding:2px 0;margin:0}
.out img{background:#fff;border-radius:4px}.err{color:#ff7b72}.stderr{color:#e3b341}.md{margin-left:60px}
.out table{font-size:13px}
"""


def nb_html(p):
    """A Jupyter notebook as a static page: markdown cells, code, and saved outputs (no kernel)."""
    out = _page_out(p, "nb1")
    if out is None:
        return None
    if out.exists():
        return str(out)
    try:
        nb = json.loads(Path(p).read_text(errors="replace"))
    except ValueError as e:
        warn(f"not a readable notebook: {e}"); return None
    lang = (nb.get("metadata", {}).get("kernelspec") or {}).get("display_name") or \
           (nb.get("metadata", {}).get("language_info") or {}).get("name", "")
    cells = nb.get("cells") or [c for ws in nb.get("worksheets", []) for c in ws.get("cells", [])]
    parts, mermaid = [], False
    for c in cells:
        src = _nb_text(c.get("source"))
        if c.get("cell_type") == "markdown":
            for name, att in (c.get("attachments") or {}).items():
                for mt, data in att.items():
                    src = src.replace(f"attachment:{name}", f"data:{mt};base64,{_nb_text(data)}")
            mermaid |= "```mermaid" in src
            parts.append(f'<div class="cell md" data-md="{html.escape(json.dumps(_md_prep(src)[1]))}"></div>')
            continue
        if c.get("cell_type") != "code":
            parts.append(f'<div class="cell"><pre>{html.escape(src)}</pre></div>'); continue
        n = c.get("execution_count")
        cell = [f'<div class="cell"><div class="in"><span class="prompt">[{n if n is not None else " "}]</span>'
                f'<pre><code>{html.escape(src)}</code></pre></div>']
        for o in c.get("outputs", []):
            ot = o.get("output_type")
            if ot == "stream":
                cls = "stderr" if o.get("name") == "stderr" else ""
                cell.append(f'<div class="out"><pre class="{cls}">{html.escape(ANSI.sub("", _nb_text(o.get("text"))))}</pre></div>')
            elif ot == "error":
                tb = ANSI.sub("", "\n".join(o.get("traceback") or [f"{o.get('ename')}: {o.get('evalue')}"]))
                cell.append(f'<div class="out"><pre class="err">{html.escape(tb)}</pre></div>')
            elif ot in ("execute_result", "display_data", "pyout"):
                d = o.get("data") or {}
                if "image/png" in d or "image/jpeg" in d:
                    mt = "image/png" if "image/png" in d else "image/jpeg"
                    cell.append(f'<div class="out"><img src="data:{mt};base64,{_nb_text(d[mt]).strip()}"></div>')
                elif "image/svg+xml" in d:
                    cell.append(f'<div class="out">{_nb_text(d["image/svg+xml"])}</div>')
                elif "text/html" in d:
                    cell.append(f'<div class="out">{_nb_text(d["text/html"])}</div>')
                elif "text/markdown" in d:
                    cell.append(f'<div class="out" data-md="{html.escape(json.dumps(_nb_text(d["text/markdown"])))}"></div>')
                elif "text/plain" in d:
                    cell.append(f'<div class="out"><pre>{html.escape(ANSI.sub("", _nb_text(d["text/plain"])))}</pre></div>')
        parts.append("".join(cell) + "</div>")
    meta = f'<div class="meta">{html.escape(os.path.basename(p))} · {len(cells)} cells' + \
           (f" · {html.escape(str(lang))}" if lang else "") + " · saved outputs, not re-run</div>"
    page = page_shell(os.path.basename(p), meta + "".join(parts), Path(p).resolve().parent, mermaid)
    if page is None:
        return None
    page = page.replace("</style>", NB_CSS + "</style>", 1)
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return str(out)


def read_alignment(p):
    """[(name, seq)] from FASTA, A3M (insertions dropped), Clustal or Stockholm."""
    text = Path(p).read_text(errors="replace")
    ext = os.path.splitext(p)[1].lower()
    head = text.lstrip()[:20].upper()
    seqs = {}
    if ext in (".sto", ".stk") or head.startswith("# STOCKHOLM"):
        for line in text.splitlines():
            if line.strip() and not line.startswith(("#", "//")):
                name, _, seq = line.strip().partition(" ")
                seqs[name] = seqs.get(name, "") + seq.strip().replace(".", "-")
        return list(seqs.items())
    if ext in (".aln", ".clustal") or head.startswith(("CLUSTAL", "MUSCLE", "PROBCONS")):
        for line in text.splitlines()[1:]:
            if line and not line[0].isspace():
                f = line.split()
                if len(f) >= 2:
                    seqs[f[0]] = seqs.get(f[0], "") + f[1]
        return list(seqs.items())
    out, name, buf = [], None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if name is not None:
                out.append((name, "".join(buf)))
            name, buf = line[1:].strip() or f"#{len(out) + 1}", []
        elif name is not None and not line.startswith("#"):
            buf.append(line.strip())
    if name is not None:
        out.append((name, "".join(buf)))
    if ext == ".a3m" or (len({len(s) for _, s in out}) > 1 and any(c.islower() for _, s in out[1:3] for c in s)):
        out = [(n, re.sub(r"[a-z.]", "", s)) for n, s in out]   # a3m: lowercase = insertions
    return out


# Clustal-X-like residue classes; nucleotides get their own four colours
AA_CLASS = {**{c: "h" for c in "AILMFWV"}, **{c: "p" for c in "KR"}, **{c: "n" for c in "DE"},
            **{c: "s" for c in "NQST"}, "G": "g", "P": "y", "H": "a", "Y": "a", "C": "c"}
NT_CLASS = {"A": "na", "C": "nc", "G": "ng", "T": "nt", "U": "nt"}
ALN_CSS = """
.wrap{overflow:auto;border:1px solid #2a2a2a;border-radius:6px;padding:6px 0 0 6px}
table.aln{border-collapse:collapse;font:12px/1.3 ui-monospace,Menlo,monospace;white-space:pre;display:table;margin:0}
.aln td{padding:0 6px 0 0;border:0}.aln td.n{position:sticky;left:0;background:#111;color:#9ab;padding-right:10px;max-width:220px;overflow:hidden;text-overflow:ellipsis;z-index:1}
.aln tr.ruler td{color:#666}.aln tr.cons td{color:#7a9}.aln tr.gap td{line-height:.8}
.h{background:#2c4f86}.p{background:#8a2c2c}.n{background:#7a2c78}.s{background:#2c6e3b}.g{background:#8a5a1c}
.y{background:#7a7a1c}.a{background:#1c6e74}.c{background:#8a4a6a}
.na{background:#2c6e3b}.nc{background:#2c4f86}.ng{background:#8a5a1c}.nt{background:#8a2c2c}.d{opacity:.38}
"""


def find_aligner():
    """(name, path) of MAFFT or Clustal Omega if installed (Homebrew dirs too: the pane's PATH may lack them)."""
    for name in ("mafft", "clustalo"):
        exe = shutil.which(name) or next((f"{d}/{name}" for d in ("/opt/homebrew/bin", "/usr/local/bin")
                                          if os.access(f"{d}/{name}", os.X_OK)), None)
        if exe:
            return name, exe
    return None


def align(seqs, aligner):
    """Align unaligned sequences (gaps stripped first); [(name, aligned seq)] or None.
    Names go to the aligner as s0, s1, ... so odd characters in the originals can't upset it."""
    name, exe = aligner
    if len(seqs) > 500 or sum(len(s) for _, s in seqs) > 2_000_000:
        warn(f"too many sequences to align here ({len(seqs)})"); return None
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / f"align_{os.getpid()}.fa"
    tmp.write_text("".join(f">s{i}\n{re.sub(r'[-.]', '', s)}\n" for i, (_, s) in enumerate(seqs)))
    cmd = [exe, "--auto", "--quiet", "--preservecase", str(tmp)] if name == "mafft" else \
          [exe, "-i", str(tmp), "--outfmt=fa", "--force"]
    print(f"\033[2m  unaligned → aligning {len(seqs)} sequences with {name}…\033[0m", flush=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        warn(f"{name} failed: {e}"); return None
    finally:
        tmp.unlink(missing_ok=True)
    got, cur = {}, None
    for line in r.stdout.splitlines():
        if line.startswith(">"):
            cur = line[1:].strip(); got[cur] = []
        elif cur is not None:
            got[cur].append(line.strip())
    res = [(nm, "".join(got.get(f"s{i}", []))) for i, (nm, _) in enumerate(seqs)]
    if r.returncode or not all(s for _, s in res) or len({len(s) for _, s in res}) != 1:
        warn(f"{name} gave no usable alignment" + (f": {r.stderr.strip()[:80]}" if r.stderr.strip() else "")); return None
    return res


def aln_html(p):
    aligner = find_aligner()
    out = _page_out(p, f"aln4_{a.max_seqs}_{aligner[0] if aligner else 'none'}")
    if out is None:
        return None, ""
    sfile = Path(str(out) + ".summary")
    if out.exists() and sfile.exists():   # a redraw: don't re-read (or re-align) the file
        return str(out), sfile.read_text()
    seqs = read_alignment(p)
    if not seqs:
        return None, "no sequences found"
    aligned = len(seqs) > 1 and len({len(s) for _, s in seqs}) == 1
    how = ""
    if len(seqs) > 1 and not aligned:
        res = align(seqs, aligner) if aligner else None
        if res:
            seqs, aligned, how = res, True, f" · aligned here with {aligner[0]} (the file is unaligned)"
        else:
            how = " · unaligned (lengths differ)" + ("" if aligner else ": install mafft or clustalo to align them here")
    n, L = len(seqs), max(len(s) for _, s in seqs)
    nt = all(set(s.upper()) <= set("ACGTUN-") for _, s in seqs[:20])
    rows = seqs[:max(1, min(a.max_seqs, 400000 // max(L, 1)))]
    cons, ident = [], None
    if aligned:   # per column: consensus residue and how conserved it is (gaps count against)
        from collections import Counter
        q = seqs[0][1]
        same = [sum(x == y and x != "-" for x, y in zip(q, s)) / max(1, sum(x != "-" and y != "-" for x, y in zip(q, s)))
                for _, s in seqs[1:200]]
        ident = 100 * sum(same) / len(same) if same else None
        for col in range(L):
            cnt = Counter(s[col].upper() for _, s in seqs[:2000] if s[col] not in "-.")
            top, k = cnt.most_common(1)[0] if cnt else ("-", 0)
            cons.append((top, k / min(n, 2000)))
    summary = f"{n} sequence{'s' if n != 1 else ''}" + (f" × {L} columns" if aligned else f", longest {L}") + \
              (f", {ident:.0f}% mean identity to the first" if ident is not None else "") + \
              ("" if len(rows) == n else f", showing {len(rows)}") + how
    if not out.exists():
        def row(seq, off=0):
            buf, prev, run = [], None, []
            for i, ch in enumerate(seq, off):
                u = ch.upper()
                cls = (NT_CLASS if nt else AA_CLASS).get(u, "")
                if cls and cons and cons[i][0] != u:
                    cls += " d"
                if cls != prev and run:
                    buf.append(f'<span class="{prev}">{html.escape("".join(run))}</span>' if prev else html.escape("".join(run)))
                    run = []
                prev = cls; run.append(ch)
            if run:
                buf.append(f'<span class="{prev}">{html.escape("".join(run))}</span>' if prev else html.escape("".join(run)))
            return "".join(buf)
        W = 60   # residues per line, Clustal-style blocks: readable in a narrow pane and in screenshots
        blocks = " ▁▂▃▄▅▆▇█"
        body = [f'<div class="meta">{html.escape(os.path.basename(p))} · {html.escape(summary)}</div><div class="wrap"><table class="aln">']

        def block(group, b0, with_cons):
            seg = range(b0, min(b0 + W, max(len(s) for _, s in group)))
            ruler = "".join(f"{i + 1:<10}" for i in seg if i % 10 == 0)
            body.append(f'<tr class="ruler"><td class="n"></td><td>{ruler}</td></tr>')
            if with_cons:
                body.append('<tr class="cons"><td class="n">conservation</td><td>' +
                            "".join(blocks[round(cons[i][1] * 8)] for i in seg) + "</td></tr>")
            for name, sq in group:
                body.append(f'<tr><td class="n" title="{html.escape(name)}">{html.escape(name[:28])}</td>'
                            f'<td>{row(sq[b0:b0 + W], b0)}</td></tr>')
            body.append('<tr class="gap"><td colspan="2">&nbsp;</td></tr>')

        if aligned:
            for b0 in range(0, L, W):
                block(rows, b0, bool(cons))
        else:
            for name, sq in rows:   # name on the first line of each sequence only
                for b0 in range(0, max(len(sq), 1), W):
                    block([(name if b0 == 0 else "", sq)], b0, False)
        body.append("</table></div>")
        page = (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(os.path.basename(p))}</title>'
                f'<style>{PAGE_CSS}{ALN_CSS}main{{max-width:none}}</style></head><body><main>{"".join(body)}</main></body></html>')
        CACHE.mkdir(parents=True, exist_ok=True)
        out.write_text(page)
    sfile.write_text(summary)
    return str(out), summary


# ---------------------------------------------------------------- tables, JSON, refinement logs
def plain_page(title, body, css="", js=""):
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f'<style>{PAGE_CSS}{css}</style></head><body><main>{body}</main>'
            + (f"<script>{js}</script>" if js else "") + "</body></html>")


TABLE_CSS = """
main{max-width:none}.tools{margin:6px 0 10px}.tools input{background:#1b1b1b;color:#eee;border:1px solid #333;border-radius:4px;padding:4px 8px;font:inherit;width:min(360px,80vw)}
.tw{overflow:auto;max-height:calc(100vh - 110px);border:1px solid #2a2a2a;border-radius:6px}
table.t{display:table;margin:0;font:12.5px/1.35 ui-monospace,Menlo,monospace;border-collapse:separate;border-spacing:0}
.t th{position:sticky;top:0;background:#1e1e1e;cursor:pointer;white-space:nowrap;z-index:1}.t th:hover{color:#fff}
.t td,.t th{border:0;border-bottom:1px solid #262626;border-right:1px solid #262626;padding:3px 8px}
.t td.num{text-align:right}.t tr:hover td{background:#1a2230}.t th .ar{color:#6cb6ff}.t td.rn{color:#666;text-align:right}
"""
TABLE_JS = """
(function(){var t=document.querySelector('table.t'),tb=t.tBodies[0],rows=[].slice.call(tb.rows),dir={};
t.tHead.rows[0].querySelectorAll('th').forEach(function(th,i){if(!i)return;th.onclick=function(){
  var num=th.dataset.num==='1',d=dir[i]=-(dir[i]||-1);
  rows.sort(function(a,b){var x=a.cells[i].textContent,y=b.cells[i].textContent;
    if(num){x=parseFloat(x);y=parseFloat(y);x=isNaN(x)?-Infinity:x;y=isNaN(y)?-Infinity:y;return (x-y)*d;}
    return x.localeCompare(y,undefined,{numeric:true})*d;});
  rows.forEach(function(r){tb.appendChild(r);});
  t.querySelectorAll('th .ar').forEach(function(s){s.textContent='';});th.querySelector('.ar').textContent=d>0?' ▲':' ▼';};});
var f=document.getElementById('filter');f.oninput=function(){var q=f.value.toLowerCase();
  rows.forEach(function(r){r.style.display=!q||r.textContent.toLowerCase().indexOf(q)>=0?'':'none';});};})();
"""
TABLE_MAX_ROWS = 5000


def table_html(p):
    """CSV/TSV as a sortable, filterable table (header = first row; numeric columns sort as numbers)."""
    import csv
    out = _page_out(p, "tab1")
    if out is None:
        return None, ""
    sfile = Path(str(out) + ".summary")
    if out.exists() and sfile.exists():
        return str(out), sfile.read_text()
    text = Path(p).read_text(errors="replace")
    if os.path.splitext(p)[1].lower() == ".tsv":
        dialect = "excel-tab"
    else:
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = "excel"
    rows = list(csv.reader(text.splitlines(), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return None, "empty table"
    head, data = rows[0], rows[1:]
    ncol = max(len(r) for r in rows)
    head += [f"col{i + 1}" for i in range(len(head), ncol)]
    shown = data[:TABLE_MAX_ROWS]

    def isnum(x):
        try:
            float(x.replace(",", "") if x.count(",") and "." in x else x); return True
        except ValueError:
            return False
    num = []
    for j in range(ncol):
        vals = [r[j].strip() for r in shown[:500] if j < len(r) and r[j].strip()]
        num.append(bool(vals) and sum(isnum(v) for v in vals) >= 0.9 * len(vals))
    summary = f"{len(data)} rows × {ncol} columns" + ("" if len(shown) == len(data) else f", showing the first {len(shown)}")
    th = '<th>#</th>' + "".join(f'<th data-num="{int(num[j])}">{html.escape(h)}<span class="ar"></span></th>'
                                for j, h in enumerate(head))
    trs = []
    for i, r in enumerate(shown, 1):
        r = r + [""] * (ncol - len(r))
        trs.append(f'<tr><td class="rn">{i}</td>' + "".join(
            f'<td class="num">{html.escape(c)}</td>' if num[j] else f'<td>{html.escape(c)}</td>' for j, c in enumerate(r)) + "</tr>")
    body = (f'<div class="meta">{html.escape(os.path.basename(p))} · {html.escape(summary)} · click a header to sort</div>'
            f'<div class="tools"><input id="filter" placeholder="filter rows…"></div>'
            f'<div class="tw"><table class="t"><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>')
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_text(plain_page(os.path.basename(p), body, TABLE_CSS, TABLE_JS))
    sfile.write_text(summary)
    return str(out), summary


JSON_CSS = """
.j{font:12.5px/1.5 ui-monospace,Menlo,monospace}.j details{margin-left:16px}.j summary{cursor:pointer;list-style:none}
.j summary::-webkit-details-marker{display:none}.j summary:before{content:'▸ ';color:#666}.j details[open]>summary:before{content:'▾ '}
.j .k{color:#9cdcfe}.j .s{color:#ce9178}.j .n{color:#b5cea8}.j .b{color:#569cd6}.j .z{color:#888}.j .c{color:#666}.j .leaf{margin-left:30px}
.err{color:#ff7b72}
"""
JSON_JS = """
(function(){var data=JSON.parse(document.getElementById('jd').textContent),root=document.getElementById('jroot');
function esc(s){return String(s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
function val(v){if(v===null)return '<span class="z">null</span>';if(typeof v==='string')return '<span class="s">"'+esc(v.length>2000?v.slice(0,2000)+'…':v)+'"</span>';
  if(typeof v==='number')return '<span class="n">'+v+'</span>';if(typeof v==='boolean')return '<span class="b">'+v+'</span>';return '';}
function node(k,v,depth){var key=k===null?'':'<span class="k">'+esc(k)+'</span>: ';
  if(v===null||typeof v!=='object'){var d=document.createElement('div');d.className='leaf';d.innerHTML=key+val(v);return d;}
  var arr=Array.isArray(v),ks=Object.keys(v),det=document.createElement('details'),sum=document.createElement('summary');
  sum.innerHTML=key+(arr?'[':'{')+'<span class="c"> '+ks.length+(arr?' items':' keys')+' </span>'+(arr?']':'}');det.appendChild(sum);
  var built=false;function build(){if(built)return;built=true;var lim=Math.min(ks.length,2000);
    for(var i=0;i<lim;i++)det.appendChild(node(arr?'['+ks[i]+']':ks[i],v[ks[i]],depth+1));
    if(ks.length>lim){var m=document.createElement('div');m.className='leaf c';m.textContent='… '+(ks.length-lim)+' more';det.appendChild(m);}}
  det.addEventListener('toggle',function(){if(det.open)build();});if(depth<2){det.open=true;build();}return det;}
root.appendChild(node(null,data,0));})();
"""


def json_html(p):
    """JSON as a collapsible tree (two levels open; deeper levels built when opened)."""
    out = _page_out(p, "json1")
    if out is None:
        return None, ""
    sfile = Path(str(out) + ".summary")
    if out.exists() and sfile.exists():
        return str(out), sfile.read_text()
    text = Path(p).read_text(errors="replace")
    name = os.path.basename(p)
    try:
        data = json.loads(text)
    except ValueError as e:
        summary = f"not valid JSON: {e}"
        body = (f'<div class="meta">{html.escape(name)}</div><p class="err">{html.escape(summary)}</p>'
                f'<pre>{html.escape(text[:200000])}</pre>')
        js = ""
    else:
        kind = (f"object, {len(data)} keys" if isinstance(data, dict) else
                f"array, {len(data)} items" if isinstance(data, list) else type(data).__name__)
        summary = f"JSON {kind}, {len(text) / 1e3:.1f} kB"
        blob = json.dumps(data).replace("</", "<\\/")
        body = (f'<div class="meta">{html.escape(name)} · {html.escape(summary)}</div>'
                f'<script type="application/json" id="jd">{blob}</script><div class="j" id="jroot"></div>')
        js = JSON_JS
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_text(plain_page(name, body, JSON_CSS, js))
    sfile.write_text(summary)
    return str(out), summary


def is_refine_log(p):
    try:
        with open(p, "rb") as f:
            head = f.read(200_000)
    except OSError:
        return False
    return b"REFMAC" in head or b"Refmac" in head or b"phenix.refine" in head


def parse_refine_log(p):
    """{"program", "cols", "rows", "final", "running"} from a REFMAC5 or phenix.refine log.
    REFMAC: the 'stats vs cycle' table (Ncyc Rfact Rfree FOM -LL -LLfree rmsBOND zBOND rmsANGL ...);
    while it is still running, the per-cycle 'Overall R factor' / 'Free R factor' lines instead.
    Phenix: the stage table (stage r-work r-free bonds angles ...), else every 'R-work = .., R-free = ..'."""
    text = Path(p).read_text(errors="replace")
    if "REFMAC" in text[:200_000] or "Refmac" in text[:200_000]:
        res = {"program": "REFMAC5", "cols": [], "rows": [], "final": {}, "running": False}
        i = text.find("$TABLE: Rfactor analysis, stats vs cycle")
        if i >= 0:
            lines = text[i:].splitlines()
            hdr = next((k for k, l in enumerate(lines) if "Ncyc" in l and "Rfree" in l), None)
            if hdr is not None:
                res["cols"] = lines[hdr].replace("$$", "").split()
                for l in lines[hdr + 1:]:
                    if l.strip() == "$$" and res["rows"]:
                        break
                    f = l.split()
                    if f and f[0].isdigit() and len(f) >= 3:
                        try:
                            res["rows"].append([float(x) for x in f[:len(res["cols"])]])
                            res.setdefault("raw", []).append(f[:len(res["cols"])])
                        except ValueError:
                            break
            m = re.search(r"R factor\s+([\d.]+)\s+([\d.]+)\s*\n\s*R free\s+([\d.]+)\s+([\d.]+)", text[i:])
            if m:
                res["final"] = {"R": (float(m.group(1)), float(m.group(2))), "Rfree": (float(m.group(3)), float(m.group(4)))}
            for key, lab in (("bond", r"Rms BondLength"), ("angle", r"Rms BondAngle")):
                m = re.search(lab + r"\s+([\d.]+)\s+([\d.]+)", text[i:])
                if m:
                    res["final"][key] = (float(m.group(1)), float(m.group(2)))
        if not res["rows"]:   # still running: the cycles so far
            rs = re.findall(r"Overall R factor\s*=\s*([\d.]+)\s*\n\s*Free R factor\s*=\s*([\d.]+)", text)
            res["cols"] = ["Ncyc", "Rfact", "Rfree"]
            res["rows"] = [[float(k), float(r), float(f)] for k, (r, f) in enumerate(rs)]
            res["running"] = True
        return res
    if "phenix.refine" in text[:200_000]:
        res = {"program": "phenix.refine", "cols": [], "rows": [], "final": {}, "running": False, "stages": []}
        hdr = re.search(r"^\s*stage\s+r-work\s+r-free\s+bonds\s+angles.*$", text, re.M)
        if hdr:
            for l in text[hdr.end():].splitlines()[1:]:
                m = re.match(r"^\s*(\S+)\s*:\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", l)
                if not m:
                    if res["rows"]:
                        break
                    continue
                res["stages"].append(m.group(1))
                res["rows"].append([float(len(res["rows"])), float(m.group(2)), float(m.group(3)),
                                    float(m.group(4)), float(m.group(5))])
                res.setdefault("raw", []).append([str(len(res["rows"]) - 1), m.group(2), m.group(3), m.group(4), m.group(5)])
            res["cols"] = ["step", "Rwork", "Rfree", "rmsBOND", "rmsANGL"]
        if not res["rows"]:
            pairs = re.findall(r"R-work\s*=\s*([\d.]+),\s*R-free\s*=\s*([\d.]+)", text)
            dedup = [pr for k, pr in enumerate(pairs) if k == 0 or pr != pairs[k - 1]]
            res["cols"] = ["step", "Rwork", "Rfree"]
            res["rows"] = [[float(k), float(r), float(f)] for k, (r, f) in enumerate(dedup)]
        st = re.search(r"Start R-work\s*=\s*([\d.]+),\s*R-free\s*=\s*([\d.]+)", text)
        fi = re.search(r"Final R-work\s*=\s*([\d.]+),\s*R-free\s*=\s*([\d.]+)", text)
        if st and fi:
            res["final"] = {"R": (float(st.group(1)), float(fi.group(1))), "Rfree": (float(st.group(2)), float(fi.group(2)))}
        res["running"] = not fi
        return res
    return None


def r_chart(xs, series, w=640, h=260, fmt="{:.3f}", floor=None):
    """Small inline SVG line chart: series = [(label, colour, values)] on one shared axis, x = cycle.
    floor: the axis never goes below it (rms deviations can't be negative)."""
    vals = [v for _, _, ys in series for v in ys]
    if not xs or not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.15, abs(hi) * 0.02, 1e-4)
    lo, hi = lo - pad, hi + pad
    if floor is not None:
        lo = max(lo, floor)
    L, R, T, B = 52, 12, 14, 30
    X = lambda x: L + (x - xs[0]) / max(xs[-1] - xs[0], 1) * (w - L - R)
    Y = lambda y: T + (hi - y) / (hi - lo) * (h - T - B)
    g = [f'<svg viewBox="0 0 {w} {h}" width="{w}" style="max-width:100%;background:#151515;border:1px solid #2a2a2a;border-radius:6px" '
         f'font-family="-apple-system,sans-serif" font-size="11">']
    for k in range(5):
        y = lo + (hi - lo) * k / 4
        g.append(f'<line x1="{L}" x2="{w - R}" y1="{Y(y):.1f}" y2="{Y(y):.1f}" stroke="#2a2a2a"/>'
                 f'<text x="{L - 6}" y="{Y(y) + 4:.1f}" fill="#888" text-anchor="end">{fmt.format(y)}</text>')
    step = max(1, round(len(xs) / 10))
    for x in xs[::step]:
        g.append(f'<text x="{X(x):.1f}" y="{h - 10}" fill="#888" text-anchor="middle">{x:g}</text>')
    for k, (lab, col, ys) in enumerate(series):
        pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(xs, ys))
        g.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
        g += [f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.5" fill="{col}"/>' for x, y in zip(xs, ys)]
        g.append(f'<text x="{w - R - 4}" y="{T + 12 + 14 * k}" fill="{col}" text-anchor="end" font-weight="600">{lab}</text>')
    g.append("</svg>")
    return "".join(g)


def refine_html(p):
    """A REFMAC5 / phenix.refine log as R and Rfree per cycle (chart + table) and final statistics."""
    out = _page_out(p, "ref2")
    if out is None:
        return None, ""
    sfile = Path(str(out) + ".summary")
    if out.exists() and sfile.exists():
        return str(out), sfile.read_text()
    res = parse_refine_log(p)
    if not res or not res["rows"]:
        return None, "no R-factor table found in this log"
    cols, rows = res["cols"], res["rows"]
    ir, ifree = 1, 2
    xs = [r[0] for r in rows]
    first, last = rows[0], rows[-1]
    rname = cols[ir]
    summary = (f"{res['program']} · {len(rows) - 1 if res['program'] == 'REFMAC5' and not res['running'] else len(rows)} "
               f"{'cycles' if res['program'] == 'REFMAC5' else 'steps'} · {rname} {first[ir]:.4f}→{last[ir]:.4f}, "
               f"Rfree {first[ifree]:.4f}→{last[ifree]:.4f} (gap {last[ifree] - last[ir]:.4f})"
               + (" · still running" if res["running"] else ""))
    chart = r_chart(xs, [(rname, "#6cb6ff", [r[ir] for r in rows]), ("Rfree", "#f0883e", [r[ifree] for r in rows])])
    # bond (Å, ~0.01) and angle (°, ~1-2) deviations differ 100-fold: one small chart each
    chart2 = "".join(
        f'<div style="display:inline-block;margin:0 12px 8px 0">' + r_chart(
            xs, [(lab, col, [r[cols.index(c)] for r in rows])], w=400, h=170, fmt=fm, floor=0) + "</div>"
        for c, lab, col, fm in (("rmsBOND", "rms bond (Å)", "#7ee787", "{:.4f}"), ("rmsANGL", "rms angle (°)", "#d2a8ff", "{:.2f}"))
        if c in cols)
    fin = res["final"]
    stats = []
    for key, lab in (("R", rname), ("Rfree", "Rfree"), ("bond", "rms bond (Å)"), ("angle", "rms angle (°)")):
        if key in fin:
            stats.append(f"<tr><td>{lab}</td><td class='num'>{fin[key][0]:.4f}</td><td class='num'>{fin[key][1]:.4f}</td></tr>")
    stages = res.get("stages") or []
    th = "".join(f"<th>{html.escape(c)}</th>" for c in cols) + ("<th>stage</th>" if stages else "")
    raw = res.get("raw") or [[f"{v:g}" for v in r] for r in rows]   # the log's own number text
    trs = "".join("<tr>" + "".join(f"<td class='num'>{html.escape(v)}</td>" for v in raw[k]) +
                  (f"<td>{html.escape(stages[k])}</td>" if stages else "") + "</tr>" for k in range(len(rows)))
    body = (f'<div class="meta">{html.escape(os.path.basename(p))} · {html.escape(summary)}</div>'
            f'<h2>R and Rfree per {"cycle" if res["program"] == "REFMAC5" else "step"}</h2>{chart}'
            + (f"<h2>Geometry</h2>{chart2}" if chart2 else "")
            + (f"<h2>Initial → final</h2><table class='t'><tr><th></th><th>initial</th><th>final</th></tr>{''.join(stats)}</table>" if stats else "")
            + f"<h2>All {'cycles' if res['program'] == 'REFMAC5' else 'steps'}</h2><table class='t'><tr>{th}</tr>{trs}</table>")
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_text(plain_page(os.path.basename(p), body, ".t td.num{text-align:right;font:12.5px ui-monospace,Menlo,monospace}"))
    sfile.write_text(summary)
    return str(out), summary


# ---------------------------------------------------------------- HTML lanes
_html_mode = None   # resolved lazily by html_mode()
_browser_id = None  # iTerm2 session id of the browser pane
_driver = None      # headless Chrome, reused across screenshots


def html_mode():
    """Resolve --html-mode auto once we know what is actually available."""
    global _html_mode
    if _html_mode:
        return _html_mode
    m = a.html_mode
    if m == "auto":
        if a.browser_profile and TERM == "iterm":
            m = "browser"
        else:
            try:
                import selenium  # noqa: F401
                m = "shot"
            except ImportError:
                m = "link"
    _html_mode = m
    return m


def it2_call(fn):
    """Run one async iTerm2 API call. Returns fn's result, or None if the API is unreachable."""
    try:
        import iterm2
    except ImportError:
        warn("python module 'iterm2' not installed — pip install iterm2")
        return None
    out = {}

    async def main(connection):
        try:  # an exception escaping here makes the iterm2 library exit the whole program
            app = await iterm2.async_get_app(connection)
            out["r"] = await fn(app, connection, iterm2)
        except Exception as e:
            out["e"] = e

    try:
        iterm2.run_until_complete(main)
    except (Exception, SystemExit) as e:  # API disabled, permission refused, no connection...
        out["e"] = e
    if "e" in out:
        warn(f"iTerm2 API: {type(out['e']).__name__}: {str(out['e'])[:80]}")
        return None
    return out.get("r")


def same_tab(app, sid, mine):
    """Is session sid open in the same iTerm2 tab as session mine? (Never touch another tab's pane.)"""
    s = app.get_session_by_id(sid) if sid else None
    if s is None or mine is None:
        return False
    t1, t2 = app.get_window_and_tab_for_session(s)[1], app.get_window_and_tab_for_session(mine)[1]
    return bool(t1 and t2 and t1.tab_id == t2.tab_id)


def browser_pane():
    """Session id of the browser pane, creating it by splitting this one on first use."""
    global _browser_id
    if _browser_id:
        return _browser_id
    if BROWSER_STATE.exists():
        _browser_id = BROWSER_STATE.read_text().strip() or None

    async def get(app, conn, iterm2):
        me = os.environ.get("ITERM_SESSION_ID", "").partition(":")[2] or None
        mine = app.get_session_by_id(me) if me else None
        if mine is None:
            return None
        if same_tab(app, _browser_id, mine):
            return _browser_id                      # pane from an earlier run, still open in this tab
        new = await mine.async_split_pane(vertical=False, profile=a.browser_profile)
        return new.session_id

    sid = it2_call(get)
    if sid:
        _browser_id = sid
        BROWSER_STATE.parent.mkdir(parents=True, exist_ok=True)
        BROWSER_STATE.write_text(sid)
    return sid


def close_browser_pane():
    """On exit: close this session's browser pane (it would otherwise be orphaned)."""
    sid = _browser_id or (BROWSER_STATE.read_text().strip() if BROWSER_STATE.exists() else None)
    if sid and not a.keep_browser:
        async def close(app, conn, iterm2):
            me = os.environ.get("ITERM_SESSION_ID", "").partition(":")[2] or None
            mine = app.get_session_by_id(me) if me else None
            if same_tab(app, sid, mine):
                await app.get_session_by_id(sid).async_close(force=True)
            return True
        it2_call(close)
    if not a.keep_browser:
        BROWSER_STATE.unlink(missing_ok=True)


# Injected into the browser-pane copy of a page: refit plotly figures to the pane on load,
# a couple of times while it settles, and on every pane resize. Figures with a fixed
# width wider than the pane are shrunk to fit.
FIT_JS = """<script>(function(){
  function fit(){
    if (!window.Plotly) return;
    document.querySelectorAll('.js-plotly-plot').forEach(function(d){
      try {
        var w = d._fullLayout && d._fullLayout.width, vw = document.documentElement.clientWidth;
        if (d.layout && d.layout.width && w > vw) { d.style.width = '100%'; Plotly.relayout(d, {width: vw - 8}); }
        else Plotly.Plots.resize(d);
      } catch(e) {}
    });
  }
  var t; function soon(){ clearTimeout(t); t = setTimeout(fit, 100); }
  window.addEventListener('load', function(){
    fit(); setTimeout(fit, 300); setTimeout(fit, 1000);
    if (window.ResizeObserver) new ResizeObserver(soon).observe(document.documentElement);
  });
})();</script>"""


def fit_copy(p):
    """Copy of a page for the browser pane: <base> keeps relative links pointing at the
    original folder, FIT_JS sizes figures to the pane. The original file is not touched."""
    try:
        key = f"{os.path.abspath(p)}|{os.stat(p).st_mtime_ns}|fit1"
        src = Path(p).read_text(errors="replace")
    except (FileNotFoundError, OSError):
        return p
    out = CACHE / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".html")
    if not out.exists():
        base = '<base href="file://localhost' + urllib.parse.quote(os.path.dirname(os.path.abspath(p))) + '/">'
        low = src.lower()
        h = low.find("<head")
        h = low.find(">", h) + 1 if h >= 0 else 0
        src = src[:h] + base + src[h:]
        b = src.lower().rfind("</body>")
        src = src[:b] + FIT_JS + src[b:] if b >= 0 else src + FIT_JS
        CACHE.mkdir(parents=True, exist_ok=True)
        out.write_text(src)
    return str(out)


def browser_load(url):
    sid = browser_pane()
    if not sid:
        return False

    async def load(app, conn, iterm2):
        sess = app.get_session_by_id(sid)
        if sess is None:
            return False
        await sess.async_load_url(url)
        return True

    ok = bool(it2_call(load))
    if not ok:  # pane was closed; forget it so the next page re-splits
        globals()["_browser_id"] = None
        BROWSER_STATE.unlink(missing_ok=True)
    return ok


def html_to_png(p, url=None):
    """Screenshot a local page with headless Chrome (cached by path+mtime+params).
    url: a live page served for p (IMGWATCH_URLMAP); its content changes, so it gets a fresh shot each time."""
    global _driver
    CACHE.mkdir(parents=True, exist_ok=True)
    key = cache_key(p, a.shot_px, a.shot_wait, url or "", time.time() if url else 0)
    if not key:
        return None
    png = CACHE / (key + ".png")
    if png.exists() and png.stat().st_size > 0:
        return str(png)
    try:
        if _driver is None:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            o = Options()
            # SwiftShader gives headless Chrome software WebGL — without it plotly 3D,
            # Mol* and anything else GL-backed screenshots as "WebGL is not supported".
            for f in ("--headless=new", "--hide-scrollbars", "--use-gl=angle",
                      "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                      "--force-device-scale-factor=2", f"--window-size={a.shot_px},900"):
                o.add_argument(f)
            _driver = webdriver.Chrome(options=o)
            _driver.set_page_load_timeout(60)
        _driver.get(url or "file://" + urllib.parse.quote(os.path.abspath(p)))
        time.sleep(a.shot_wait)
        h = _driver.execute_script(
            "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)") or 900
        _driver.set_window_size(a.shot_px, min(max(int(h) + 40, 900), 4000))
        time.sleep(0.3)
        _driver.save_screenshot(str(png))
    except Exception as e:
        warn(f"screenshot failed: {type(e).__name__}: {str(e)[:80]}")
        try:
            if _driver:
                _driver.quit()
        except Exception:
            pass
        _driver = None
        return None
    return str(png) if png.exists() and png.stat().st_size else None


def show_html(p, orig=None):
    """p: page to show; orig: the file it stands for (structures), used for the link fallback."""
    mode = html_mode()
    if mode == "off":
        return None
    url = served_url(p) if orig is None else None
    if url:
        print(f"\033[2m  served page → {url}\033[0m", flush=True)
    elif orig is None and needs_server(p):
        warn("this page loads data from its web server (fetch(\"/...\")), so as a file it may look empty; "
             "map it to its URL with IMGWATCH_URLMAP in ~/.config/imgwatch/config")
    if mode == "browser":
        print("\033[2m  → browser pane\033[0m", flush=True)
        # iTerm2's browser rejects file:///path as "Invalid URL" but takes file://localhost/path
        if browser_load(url or file_url(fit_copy(p) if orig is None else p)):
            return None
        warn("no browser pane — falling back to a link")
        mode = "link"
    if mode == "shot":
        print(f"\033[2m  → headless-Chrome screenshot ({a.shot_px}px, {a.shot_wait:g}s settle)\033[0m", flush=True)
        png = html_to_png(p, url)
        if png:
            return png
        mode = "link"
    # link: OSC 8 hyperlink — cmd-click opens the page (for structures: the Mol* page) in the default browser
    print("  " + link(url or file_url(p), f"\033[4;36m{os.path.basename(orig or p)}\033[0m"), flush=True)
    return None


# ---------------------------------------------------------------- rendering
seen = {}  # path -> mtime already shown (or present at start)
history = deque(maxlen=max(a.keep, 50))  # (item, time shown) of recent items: redrawn on resize/replace, ←/→ browse them
view = None       # index into history while browsing it with ←/→ (None = live: newest items as they come)
tool_page = False  # a mark-up / pick / compare page is in the browser pane: redraws must not load over it
resized_at = None  # set by SIGWINCH; inline images are sized when drawn, so redraw after a resize
pinned = None      # (item, ts) kept at the top of the pane until unpinned
stills = {}        # item -> the image file actually drawn for it (what the annotate page shows)
pages = {}         # item -> the page shown for it in the HTML lanes (generated or the file itself)
updates = {}       # item -> times it was replaced in place
SID = a.session or "nosession"


def settle(paths):
    """Wait (max 3 s) until none of the files is still growing; return those that exist."""
    sizes = {}
    for _ in range(10):
        now = {}
        for p in paths:
            try:
                now[p] = os.path.getsize(p)
            except FileNotFoundError:
                pass
        if now and now == sizes and all(now.values()):
            break
        sizes = now
        time.sleep(0.3)
    out = []
    for p in paths:
        try:
            seen[p] = os.stat(p).st_mtime
            out.append(p)
        except FileNotFoundError:
            pass
    return out


def is_page(item):
    """Shown in the browser pane (a reload replaces it there; nothing to redraw in the terminal)."""
    return (len(item) == 1 and os.path.splitext(item[0])[1].lower() in PAGE_EXTS
            and html_mode() == "browser")


def show(paths):
    """Show a batch of new files: a burst of stills becomes one contact sheet. A file shown again
    right after itself (a figure being re-rendered, Write then Read) replaces its own entry."""
    paths = settle(list(dict.fromkeys(paths)))
    paths = [p for p in paths if os.path.splitext(p)[1].lower() not in LOG_EXTS or is_refine_log(p)]
    stills_ = [p for p in paths if os.path.splitext(p)[1].lower() in STILL_EXTS]
    if a.burst and len(stills_) > a.burst:
        rest = [p for p in paths if p not in stills_]
        items = [tuple(stills_)] + [(p,) for p in rest]
    else:
        items = [(p,) for p in paths]
    for it in items:
        globals()["tool_page"] = False   # something new: it may take the browser pane back
        ts = f"{datetime.now():%H:%M:%S}"
        if not a.no_replace and history and history[-1][0] == it:
            updates[it] = updates.get(it, 1) + 1
            history[-1] = (it, ts)
            stills.pop(it, None)
            if is_page(it):
                print(f"\033[2m  ↻ updated {ts}\033[0m", flush=True)
                render(it, ts, head=False)
            else:
                redraw()
            continue
        history.append((it, ts))
        if pinned or view is not None:
            globals()["view"] = None   # something new arrived: back to live
            redraw()
        else:
            render(it, ts)


def header(p, ts, label=None, mark=""):
    d = os.path.dirname(os.path.abspath(p))
    name = link(file_url(p), label or os.path.basename(p))
    print(f"\n\033[1;36m── {mark}{name}\033[0;2m  {ts}  {link(file_url(d), d)}\033[0m", flush=True)


def render(item, ts, load=True, head=True, mark=""):
    """Draw one item. load=False: a page is only listed, not pushed to the browser pane again."""
    n = updates.get(item)
    mark = mark + (f"↻{n} " if n else "")
    if len(item) > 1:  # burst
        live = [p for p in item if os.path.exists(p)]
        if not live:
            return
        d = os.path.dirname(os.path.abspath(live[0]))
        print(f"\n\033[1;36m── {mark}{len(live)} images\033[0;2m  {ts}  {link(file_url(d), d)}\033[0m", flush=True)
        sheet = contact_sheet(live)
        if sheet:
            stills[item] = sheet
            draw_image(sheet)
        else:  # no PIL: draw the last few, list the rest
            for p in live[-3:]:
                draw_image(p)
            print("\033[2m  + " + ", ".join(os.path.basename(p) for p in live[:-3]) + "\033[0m", flush=True)
        return
    p = item[0]
    if not os.path.exists(p):
        return
    if head:
        header(p, ts, mark=mark)
    ext = os.path.splitext(p)[1].lower()
    src = p
    if ext in PAGE_EXTS:
        mb = os.path.getsize(p) / 1e6
        if mb > a.html_max_mb:
            warn(f"skipped: {mb:.0f} MB > --html-max-mb {a.html_max_mb:g}"); return
        if ext in (".sdf", ".mol") and not is_3d_single(p):
            return show_grid(p, item)
        if html_mode() == "off":
            return
        page = None
        if ext in STRUCT_EXTS:
            print(f"\033[2m  structure → Mol* {MOLSTAR_VERSION}\033[0m", flush=True)
            page = structure_html(p)
        elif ext in NOTE_EXTS:
            print("\033[2m  markdown → page\033[0m", flush=True)
            page = md_html(p)
        elif ext in NB_EXTS:
            print("\033[2m  notebook → page (saved outputs)\033[0m", flush=True)
            page = nb_html(p)
        elif ext in ALN_EXTS:
            page, summary = aln_html(p)
            print(f"\033[2m  sequences: {summary}\033[0m", flush=True)
        elif ext in TABLE_EXTS:
            page, summary = table_html(p)
            print(f"\033[2m  table: {summary}\033[0m", flush=True)
        elif ext in JSON_EXTS:
            page, summary = json_html(p)
            print(f"\033[2m  {summary}\033[0m", flush=True)
        elif ext in LOG_EXTS:
            page, summary = refine_html(p)
            print(f"\033[2m  {summary}\033[0m", flush=True)
        if ext not in HTML_EXTS and not page:
            print("  " + link(file_url(p), f"\033[4;36m{os.path.basename(p)}\033[0m"), flush=True); return
        pages[item] = page or p
        if not load and html_mode() == "browser":
            print("\033[2m  (in the browser pane)\033[0m", flush=True); return
        if ext in STRUCT_EXTS and page and html_mode() == "browser":
            # served with the pick panel built in (collapsed): atoms can be sent any time, no key needed
            print("\033[2m  → browser pane · ✎ Pick atoms (top right) sends me atoms\033[0m", flush=True)
            if browser_load(struct_url(p)):
                return
        src = show_html(page, orig=p) if page else show_html(p)
        if src is None:
            return
    elif ext in VIDEO_EXTS:
        if a.no_video:
            return
        dur = "whole clip" if a.video_seconds <= 0 else f"first {a.video_seconds:g}s"
        print(f"\033[2m  video → animated GIF ({dur} @ {a.video_fps:g} fps)\033[0m", flush=True)
        src = video_to_gif(p)
        if src is None:
            return
        print(f"\033[2m  preview {os.path.getsize(src)/1e6:.1f} MB\033[0m", flush=True)
    elif ext in PDF_EXTS:
        n = pdf_pages(p)
        print(f"\033[2m  page 1{f' of {n}' if n else ''} · cmd-click the name to open\033[0m", flush=True)
        src = ql_png(p)
        if src is None:
            warn("Quick Look could not render this PDF"); return
    elif ext == ".svg":  # imgcat can't render SVG; rasterise with Quick Look
        src = ql_png(p)
        if src is None:
            return
    elif ext in (".heic", ".heif"):  # iPhone photos: convert with sips
        src = heic_jpg(p)
        if src is None:
            warn("sips could not convert this HEIC"); return
    elif ext in SMILES_EXTS:
        return show_grid(p, item)
    elif ext in PSE_EXTS:
        print("\033[2m  PyMOL session → ray-traced snapshot (saved view)\033[0m", flush=True)
        src = pse_png(p)
        if src is None:
            return
    elif ext in OFFICE_EXTS:
        print("\033[2m  first slide/page (Quick Look) · cmd-click the name to open\033[0m", flush=True)
        src = ql_png(p)
        if src is None:
            warn("Quick Look could not render this file"); return
    stills[item] = src
    draw_image(src)


def show_grid(p, item=None):
    src, n, bad = compound_grid(p)
    more = f", showing {a.max_mols}" if n > a.max_mols else ""
    print(f"\033[2m  {n} molecule{'s' if n != 1 else ''}{more}" + (f", {bad} unreadable" if bad else "") + "\033[0m", flush=True)
    if src:
        if item:
            stills[item] = src
        draw_image(src)


KEYS = False  # single-key commands typed into the pane (set up in main when stdin is our tty)


def banner():
    watch = "FSEvents" if fsw else "polling"
    print(f"\033[1mimgwatch\033[0m {version()}  {watch}: {polling}  + hook queue   (Ctrl-C to stop)", flush=True)
    if KEYS:
        print("\033[2m  keys: ←/→ browse · p pin · a mark up · b before/after · c copy · o open · r redraw · ? help\033[0m", flush=True)


def open_tool_page(url):
    """Mark-up, pick and compare pages: into the browser pane (kept there until a new item arrives),
    else the default browser."""
    global tool_page
    if html_mode() == "browser" and browser_load(url):
        tool_page = True
    elif not os.environ.get("IMGWATCH_NO_OPEN"):
        subprocess.run(["open", url], capture_output=True)


def redraw():
    global MAX_ROWS
    if view is not None and view < len(history):   # browsing with ←/→: keep showing that item
        return step(0)
    clear_all()
    banner()
    items = list(history)
    if pinned:
        # pin on top, newest below, each capped at half the pane so both stay in view
        MAX_ROWS = max(4, (shutil.get_terminal_size((80, 24)).lines - (10 if KEYS else 9)) // 2)
        print("\033[2m  pinned (p or `imgwatch --unpin` releases it); newest item below\033[0m", flush=True)
        render(*pinned, load=False, mark="📌 ")
        items = [x for x in items if x[0] != pinned[0]][-1:]
    else:
        MAX_ROWS = None
        items = items[-a.keep:] if a.keep > 0 else []
    last_page = None if tool_page else max((i for i, x in enumerate(items) if is_page(x[0])), default=None)
    for i, x in enumerate(items):
        render(*x, load=(i == last_page))


def on_winch(*_):
    global resized_at
    resized_at = time.time()


def on_term(*_):
    raise SystemExit(0)  # run the finally block (close the browser pane) on kill / pane close


# ---------------------------------------------------------------- pin, mark up, control channel
def latest():
    return history[-1] if history else None


def current():
    """The item the keys act on: the one being browsed with ←/→, else the newest."""
    if view is not None and 0 <= view < len(history):
        return history[view]
    return latest()


def step(d):
    """←/→: show one earlier/later item on its own; → past the newest returns to the live view."""
    global view
    if not history:
        return
    i = (len(history) - 1 if view is None else view) + d
    if i >= len(history):
        if view is not None:
            view = None; redraw()
        return
    view = max(0, i)
    clear_all()
    banner()
    print(f"\033[1;35m  ◀ {view + 1}/{len(history)} ▶\033[0;2m  ← older · → newer (past the newest: back to live)\033[0m", flush=True)
    it, ts = history[view]
    render(it, ts, load=True)


def item_image(it):
    """An image file standing for an item (what was drawn for it), or None for pages and the like."""
    src = stills.get(it)
    if src is None and len(it) == 1 and os.path.splitext(it[0])[1].lower() in STILL_EXTS and os.path.exists(it[0]):
        src = it[0]
    if src and os.path.splitext(src)[1].lower() in (".svg",):
        src = ql_png(src)
    elif src and os.path.splitext(src)[1].lower() in (".heic", ".heif", ".tif", ".tiff", ".bmp", ".webp"):
        src = heic_jpg(src)   # sips -> jpeg, works for all of these
    return src


def copy_item(entry):
    """c: the item's image onto the clipboard (paste into Slack, Keynote, Mail), else its path as text."""
    if entry is None:
        warn("nothing shown yet"); return
    it = entry[0]
    src = item_image(it)
    if src and os.path.splitext(src)[1].lower() == ".gif":
        src = None   # the clipboard would take only the first frame; copy the path instead
    if src:
        cls = "PNGf" if src.lower().endswith(".png") else "JPEG"
        r = subprocess.run(["osascript", "-e", "on run argv",
                            "-e", f"set the clipboard to (read (POSIX file (item 1 of argv)) as «class {cls}»)",
                            "-e", "end run", src], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"\033[2m  ⧉ image copied to the clipboard ({os.path.basename(it[0]) if len(it) == 1 else 'contact sheet'})\033[0m", flush=True)
            return
    text = "\n".join(it)
    subprocess.run(["pbcopy"], input=text.encode())
    print(f"\033[2m  ⧉ path copied to the clipboard: {text[:120]}\033[0m", flush=True)


def open_item(entry):
    """o: open the item's file(s) in their default app (images: Preview)."""
    if entry is None:
        warn("nothing shown yet"); return
    files = [p for p in entry[0] if os.path.exists(p)]
    if not files:
        warn("file no longer exists"); return
    print(f"\033[2m  ↗ opening {os.path.basename(files[0])}{f' + {len(files) - 1} more' if len(files) > 1 else ''}\033[0m", flush=True)
    if not os.environ.get("IMGWATCH_NO_OPEN"):
        subprocess.run(["open", *files], capture_output=True)


def set_pin(entry):
    global pinned, view
    pinned, view = entry, None
    redraw()


def item_for(path):
    """The history entry showing path, or a fresh one."""
    rp = os.path.realpath(path)
    for it, ts in reversed(history):
        if len(it) == 1 and os.path.realpath(it[0]) == rp:
            return (it, ts)
    return ((path,), f"{datetime.now():%H:%M:%S}")


ANN_TOKEN = secrets.token_urlsafe(12)  # in every annotate URL: other local pages can't post marks
_ann = {"srv": None, "port": None, "items": {}, "n": 0, "done": deque(), "paths": {}}

ANN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Mark up: __NAME__</title><style>
:root{color-scheme:dark}body{margin:0;background:#111;color:#ddd;font:14px -apple-system,sans-serif}
#top{position:sticky;top:0;background:#181818;border-bottom:1px solid #2a2a2a;padding:8px 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;z-index:3}
#top b{color:#fff}button{background:#2d6cdf;color:#fff;border:0;border-radius:5px;padding:5px 12px;font:inherit;cursor:pointer}
button.alt{background:#333}#stage{position:relative;display:inline-block;margin:10px;cursor:crosshair;user-select:none;touch-action:none}
#stage img{display:block;max-width:calc(100vw - 20px);height:auto}#ov{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
#side{padding:0 12px 30px}ol{padding-left:22px}li{margin:5px 0}li input{width:min(560px,80vw);background:#1b1b1b;color:#eee;border:1px solid #333;border-radius:4px;padding:4px 6px;font:inherit}
textarea{width:min(600px,92vw);height:56px;background:#1b1b1b;color:#eee;border:1px solid #333;border-radius:4px;padding:6px;font:inherit}
#msg{color:#7ee787;margin-top:8px}.hint{color:#888}
</style></head><body>
<div id="top"><b>__NAME__</b><span class="hint">click = point · drag = box · then type a note per mark</span>
<button id="send">Send to Claude</button><button class="alt" id="undo">Undo</button><button class="alt" id="clear">Clear</button></div>
<div id="stage"><img id="im" src="__IMG__" draggable="false"><svg id="ov"></svg></div>
<div id="side"><ol id="list"></ol><textarea id="note" placeholder="Overall note (optional)"></textarea><div id="msg"></div></div>
<script>
var im=document.getElementById('im'), ov=document.getElementById('ov'), st=document.getElementById('stage');
var marks=[], drag=null, NS='http://www.w3.org/2000/svg';
function pt(e){var r=im.getBoundingClientRect(), k=im.naturalWidth/r.width;
  return [Math.round(Math.min(Math.max(e.clientX-r.left,0),r.width)*k), Math.round(Math.min(Math.max(e.clientY-r.top,0),r.height)*k)];}
function el(t,a){var e=document.createElementNS(NS,t);for(var k in a)e.setAttribute(k,a[k]);return e;}
function draw(tmp){ov.setAttribute('viewBox','0 0 '+im.naturalWidth+' '+im.naturalHeight);ov.innerHTML='';
  var lw=Math.max(2,im.naturalWidth/350), fs=Math.max(14,im.naturalWidth/45);
  marks.concat(tmp?[tmp]:[]).forEach(function(m,i){
    var c=m===tmp?'#fff':'#ffd400';
    if(m.kind=='box') ov.appendChild(el('rect',{x:m.x,y:m.y,width:m.w,height:m.h,fill:'none',stroke:c,'stroke-width':lw}));
    else ov.appendChild(el('circle',{cx:m.x,cy:m.y,r:fs*0.6,fill:'none',stroke:c,'stroke-width':lw}));
    if(m!==tmp){var s=String(i+1),q=lpos(m,s.length*fs*0.62,fs,lw,fs*0.6);
      var t=el('text',{x:q[0],y:q[1]+fs*0.85,fill:'#ffd400','font-size':fs,'font-weight':'bold',stroke:'#000','stroke-width':lw*0.6,'paint-order':'stroke'});t.textContent=s;ov.appendChild(t);}
  });}
// label outside the mark so it never covers what is marked (same rule as save_annotation's label_pos)
function lpos(m,tw,th,lw,r){var W=im.naturalWidth,H=im.naturalHeight,x,y;
  if(m.kind=='box'){x=m.x;y=m.y-lw-th-1;if(y<0){y=m.y+m.h+lw+1;if(y+th>H){x=m.x+lw*2;y=m.y+lw;}}}
  else{y=m.y-th/2;x=m.x-r-3-tw;if(x<0)x=m.x+r+3;}
  return [Math.min(Math.max(x,0),W-tw),Math.min(Math.max(y,0),H-th)];}
function list(){var ol=document.getElementById('list');ol.innerHTML='';marks.forEach(function(m,i){
  var li=document.createElement('li'),inp=document.createElement('input');
  inp.placeholder=(m.kind=='box'?'box':'point')+' — what about it?';inp.value=m.note||'';inp.oninput=function(){m.note=inp.value;};
  li.appendChild(inp);ol.appendChild(li);});}
st.addEventListener('pointerdown',function(e){drag={s:pt(e)};st.setPointerCapture(e.pointerId);});
st.addEventListener('pointermove',function(e){if(!drag)return;var p=pt(e),s=drag.s;
  draw({kind:'box',x:Math.min(s[0],p[0]),y:Math.min(s[1],p[1]),w:Math.abs(p[0]-s[0]),h:Math.abs(p[1]-s[1])});});
st.addEventListener('pointerup',function(e){if(!drag)return;var p=pt(e),s=drag.s;drag=null;
  var k=im.naturalWidth/im.getBoundingClientRect().width;
  if(Math.abs(p[0]-s[0])+Math.abs(p[1]-s[1])<8*k) marks.push({kind:'point',x:s[0],y:s[1],note:''});
  else marks.push({kind:'box',x:Math.min(s[0],p[0]),y:Math.min(s[1],p[1]),w:Math.abs(p[0]-s[0]),h:Math.abs(p[1]-s[1]),note:''});
  draw();list();var ins=document.querySelectorAll('li input');if(ins.length)ins[ins.length-1].focus();});
document.getElementById('undo').onclick=function(){marks.pop();draw();list();};
document.getElementById('clear').onclick=function(){marks=[];draw();list();};
document.getElementById('send').onclick=function(){
  var note=document.getElementById('note').value;
  if(!marks.length&&!note.trim()){document.getElementById('msg').textContent='Nothing marked yet.';return;}
  fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({marks:marks,note:note})})
   .then(function(r){return r.json();}).then(function(j){
     if(j.ok){marks=[];document.getElementById('note').value='';draw();list();}  // sent once; a later Send carries only new marks
     document.getElementById('msg').textContent=j.ok?
     'Saved and cleared. It goes to Claude with your next message (type it in the Claude pane).':'Failed: '+j.error;})
   .catch(function(e){document.getElementById('msg').textContent='Failed: '+e;});};
im.onload=function(){draw();};if(im.complete)draw();
</script></body></html>"""


def ann_server():
    """Tiny localhost server for the annotate page (started on first use)."""
    if _ann["srv"]:
        return _ann["port"]
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _item(self, kind):
            m = re.match(rf"^/{re.escape(ANN_TOKEN)}/{kind}/(\d+)$", self.path.split("?")[0])
            return _ann["items"].get(m.group(1)) if m else None

        def do_GET(self):
            it = self._item("s")
            if it and it.get("struct"):
                page = molstar_page(it["orig"], pick=True)
                if page is None:
                    return self._send(500, b"Mol* not available (offline?)", "text/plain")
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            it = self._item("a")
            if it:
                img = f"/{ANN_TOKEN}/img/{it['id']}"
                page = ANN_PAGE.replace("__NAME__", html.escape(it["name"])).replace("__IMG__", img)
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            it = self._item("c")
            if it and it.get("src2"):
                page = CMP_PAGE.replace("__A__", html.escape(it["name"])).replace("__B__", html.escape(it["name2"]))
                page = page.replace("__IMGA__", f"/{ANN_TOKEN}/img/{it['id']}").replace("__IMGB__", f"/{ANN_TOKEN}/imgb/{it['id']}")
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            for kind, key in (("img", "src"), ("imgb", "src2")):
                it = self._item(kind)
                if it and it.get(key):
                    ext = os.path.splitext(it[key])[1].lower()
                    ct = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")
                    return self._send(200, Path(it[key]).read_bytes(), ct)
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            it = self._item("a") or self._item("s")
            if not it:
                return self._send(404, b'{"ok":false,"error":"unknown page"}', "application/json")
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(min(n, 20_000_000)))   # atoms + a view snapshot
                rec = save_atoms(it, data) if it.get("struct") else save_annotation(it, data)
                _ann["done"].append(rec)
                self._send(200, b'{"ok":true}', "application/json")
            except Exception as e:
                self._send(200, json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}).encode(),
                           "application/json")

    class Srv(ThreadingHTTPServer):
        daemon_threads = True

        def server_bind(self):  # HTTPServer's version reverse-resolves the host (getfqdn), which can hang
            import socketserver
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    srv = Srv(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _ann["srv"], _ann["port"] = srv, srv.server_address[1]
    return _ann["port"]


def label_pos(m, tw, th, W, H, lw, r):
    """Top-left of a mark's number, outside the mark so it never covers what is marked:
    boxes above their top-left corner (below if no room, inside as a last resort),
    points left of the circle (right if no room). Kept in the image. Mirrors lpos() in ANN_PAGE."""
    if m["kind"] == "box":
        x, y = m["x"], m["y"] - lw - th - 1
        if y < 0:
            y = m["y"] + m.get("h", 0) + lw + 1
            if y + th > H:
                x, y = m["x"] + lw * 2, m["y"] + lw
    else:
        x, y = m["x"] - r - 3 - tw, m["y"] - th // 2
        if x < 0:
            x = m["x"] + r + 3
    return (min(max(x, 0), W - tw), min(max(y, 0), H - th))


def save_annotation(it, data):
    """Burn the marks into a copy of the image and queue a record for the UserPromptSubmit hook."""
    from PIL import Image, ImageDraw, ImageFont
    marks = []
    for m in (data.get("marks") or [])[:50]:
        mk = {"kind": "box" if m.get("kind") == "box" else "point", "note": str(m.get("note") or "")[:500]}
        for k in ("x", "y", "w", "h"):
            if k in m:
                mk[k] = int(float(m[k]))
        marks.append(mk)
    im = Image.open(it["src"]); im.seek(0); im = im.convert("RGB")
    d = ImageDraw.Draw(im)
    lw, fs = max(2, im.width // 350), max(14, im.width // 45)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", fs)
    except OSError:
        font = ImageFont.load_default()
    sw, r = max(1, lw // 2), int(fs * 0.6)
    for m in marks:  # shapes first, so no outline is drawn over another mark's number
        if m["kind"] == "box":
            d.rectangle([m["x"], m["y"], m["x"] + m.get("w", 0), m["y"] + m.get("h", 0)], outline=(255, 212, 0), width=lw)
        else:
            d.ellipse([m["x"] - r, m["y"] - r, m["x"] + r, m["y"] + r], outline=(255, 212, 0), width=lw)
    for i, m in enumerate(marks, 1):
        b = d.textbbox((0, 0), str(i), font=font, anchor="lt", stroke_width=sw)
        at = label_pos(m, b[2] - b[0], b[3] - b[1], im.width, im.height, lw, r)
        d.text(at, str(i), fill=(255, 212, 0), font=font, anchor="lt", stroke_width=sw, stroke_fill=(0, 0, 0))
    ANNOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    comp = ANNOT / f"{SID[:8]}_{stamp}_marked.png"
    im.save(comp)
    rec = {"orig": it["orig"], "src": it["src"], "size": [im.width, im.height], "marks": marks,
           "note": str(data.get("note") or "")[:2000], "marked": str(comp), "time": stamp, "epoch": time.time()}
    with open(ANNOT / f"{SID}.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def _num(v, nd=3):
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def save_atoms(it, data):
    """Atoms picked on a structure: queue a record (labels, Coot/Moorhen CID, PyMOL selection, xyz)
    for the UserPromptSubmit hook, with the view snapshot the page sent."""
    clean = lambda v, n=8: re.sub(r"[^A-Za-z0-9_'\-+*]", "", str(v or ""))[:n]
    atoms = []
    for m in (data.get("atoms") or [])[:100]:
        ch, resn, atom, alt, ins = clean(m.get("chain"), 4), clean(m.get("resn"), 5), clean(m.get("atom"), 6), \
            clean(m.get("alt"), 2), clean(m.get("ins"), 2)
        try:
            resi = int(m.get("resi"))
        except (TypeError, ValueError):
            continue
        xyz = [_num(m.get(k)) for k in ("x", "y", "z")]
        atoms.append({
            "label": f"{ch}/{resn} {resi}{ins}/{atom}" + (f":{alt}" if alt else ""),
            "chain": ch, "resn": resn, "resi": resi, "ins": ins, "atom": atom, "alt": alt,
            "element": clean(m.get("element"), 3), "b": _num(m.get("b"), 2), "model": m.get("model"),
            # Coot/Moorhen short-form CID (what addColourRule and friends match), PyMOL selection
            "cid": f"//{ch}/{resi}{'.' + ins if ins else ''}/{atom}" + (f":{alt}" if alt else ""),
            "pymol": f"chain {ch} and resi {resi}{ins} and name {atom}" + (f" and alt {alt}" if alt else ""),
            "xyz": "(%s)" % ", ".join("%.3f" % v if v is not None else "?" for v in xyz),
            "note": str(m.get("note") or "")[:500]})
    ANNOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    view_png = None
    shot = str(data.get("shot") or "")
    if shot.startswith("data:image/png;base64,"):
        try:
            view_png = ANNOT / f"{SID[:8]}_{stamp}_view.png"
            view_png.write_bytes(base64.b64decode(shot.split(",", 1)[1]))
        except (ValueError, OSError):
            view_png = None
    rec = {"kind": "atoms", "orig": it["orig"], "atoms": atoms, "note": str(data.get("note") or "")[:2000],
           "marked": str(view_png) if view_png else "", "time": stamp, "epoch": time.time()}
    with open(ANNOT / f"{SID}.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def struct_url(p, opened=False):
    """The structure's Mol* page served by the annotate server, pick panel built in (one id per file;
    the page is built from the file on every load, so it is always current)."""
    port = ann_server()
    rp = os.path.realpath(p)
    aid = _ann["paths"].get(rp)
    if aid is None:
        _ann["n"] += 1
        aid = _ann["paths"][rp] = str(_ann["n"])
        _ann["items"][aid] = {"id": aid, "struct": True, "orig": p, "name": os.path.basename(p)}
    return f"http://127.0.0.1:{port}/{ANN_TOKEN}/s/{aid}" + ("?open=1" if opened else "")


def pick_atoms(it):
    """Mark up a structure: its Mol* view with the atom-picking panel open."""
    if molstar_assets() is None:
        warn("Mol* not available (offline?)"); return
    url = struct_url(it[0], opened=True)
    print(f"\n\033[1;33m── ✎ pick atoms in {os.path.basename(it[0])}\033[0;2m  click atoms, add notes, Send · "
          f"{link(url, 'open')}\033[0m", flush=True)
    open_tool_page(url)


def annotate(entry):
    """Open the mark-up page for a history entry: atom picking for structures, else marks on the
    image (pages via a headless screenshot)."""
    if entry is None:
        warn("nothing shown yet to mark up"); return
    it = entry[0]
    if len(it) == 1 and os.path.splitext(it[0])[1].lower() in STRUCT_EXTS and os.path.exists(it[0]) \
            and (os.path.splitext(it[0])[1].lower() not in (".sdf", ".mol") or is_3d_single(it[0])):
        return pick_atoms(it)
    src = stills.get(it)
    if src is None and len(it) == 1 and os.path.exists(it[0]):
        ext = os.path.splitext(it[0])[1].lower()
        if ext in PAGE_EXTS:
            print("\033[2m  taking a screenshot of the page to mark up…\033[0m", flush=True)
            src = html_to_png(pages.get(it) or it[0])
        elif ext in STILL_EXTS:
            src = it[0]
    if src and os.path.splitext(src)[1].lower() in (".tif", ".tiff", ".bmp", ".heic", ".heif", ".svg"):
        src = ql_png(src) if src.lower().endswith(".svg") else heic_jpg(src)  # sips -> jpeg works for all
    if not src or not os.path.exists(src):
        warn("cannot mark this item up (no image of it)"); return
    try:
        import PIL  # noqa: F401
    except ImportError:
        warn("marking up needs Pillow in imgwatch's python"); return
    port = ann_server()
    _ann["n"] += 1
    aid = str(_ann["n"])
    name = os.path.basename(it[0]) if len(it) == 1 else f"{len(it)} images"
    _ann["items"][aid] = {"id": aid, "src": src, "name": name,
                          "orig": it[0] if len(it) == 1 else ", ".join(it)}
    url = f"http://127.0.0.1:{port}/{ANN_TOKEN}/a/{aid}"
    print(f"\n\033[1;33m── ✎ mark up {name}\033[0;2m  click = point, drag = box, then Send · {link(url, 'open')}\033[0m", flush=True)
    open_tool_page(url)   # no browser pane: default browser (tests: never)


CMP_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Compare: __A__ vs __B__</title><style>
:root{color-scheme:dark}body{margin:0;background:#111;color:#ddd;font:14px -apple-system,sans-serif}
#top{position:sticky;top:0;background:#181818;border-bottom:1px solid #2a2a2a;padding:8px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;z-index:3}
button{background:#333;color:#fff;border:0;border-radius:5px;padding:5px 11px;font:inherit;cursor:pointer}button.on{background:#2d6cdf}
.lab{color:#9ab}.lab b{color:#ffd400}.hint{color:#777}
#stage{position:relative;margin:10px auto;max-width:calc(100vw - 20px);user-select:none;touch-action:none}
#stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:block}
#stage img#b{clip-path:inset(0 0 0 50%)}#bar{position:absolute;top:0;bottom:0;width:2px;background:#ffd400;left:50%;pointer-events:none}
#tag{position:absolute;top:6px;left:8px;background:#000a;color:#ffd400;padding:2px 7px;border-radius:4px;font-weight:600;display:none}
#side{display:none;gap:10px;padding:10px}#side figure{margin:0;flex:1;min-width:0}#side img{width:100%}#side figcaption{color:#9ab;margin-top:4px}
</style></head><body>
<div id="top"><span class="lab"><b>A</b> __A__ &nbsp;→&nbsp; <b>B</b> __B__</span>
<button data-m="slide" class="on">Slider</button><button data-m="blink">Blink</button><button data-m="side">Side by side</button><button data-m="diff">Difference</button>
<span class="hint">drag across the image · space flips A/B · 1–4 modes</span></div>
<div id="stage"><img id="a" src="__IMGA__"><img id="b" src="__IMGB__"><div id="bar"></div><div id="tag"></div></div>
<div id="side"><figure><img src="__IMGA__"><figcaption>A · __A__</figcaption></figure><figure><img src="__IMGB__"><figcaption>B · __B__</figcaption></figure></div>
<script>
var A=document.getElementById('a'),B=document.getElementById('b'),st=document.getElementById('stage'),bar=document.getElementById('bar'),tag=document.getElementById('tag');
var mode='slide',pos=50,showB=true,timer=null,down=false;
function size(){var w=Math.max(A.naturalWidth,B.naturalWidth)||800,h=Math.max(A.naturalHeight,B.naturalHeight)||600;
  var maxw=Math.min(window.innerWidth-20,w),k=Math.min(1,(window.innerHeight-70)/(h*maxw/w));st.style.width=(maxw*k)+'px';st.style.height=(h*maxw/w*k)+'px';}
function apply(){B.style.mixBlendMode='normal';B.style.clipPath='none';B.style.opacity=1;bar.style.display='none';tag.style.display='none';
  st.style.display=mode=='side'?'none':'block';document.getElementById('side').style.display=mode=='side'?'flex':'none';
  if(mode=='slide'){B.style.clipPath='inset(0 0 0 '+pos+'%)';bar.style.display='block';bar.style.left=pos+'%';}
  if(mode=='blink'){B.style.opacity=showB?1:0;tag.style.display='block';tag.textContent=showB?'B':'A';}
  if(mode=='diff'){B.style.mixBlendMode='difference';tag.style.display='block';tag.textContent='A − B (black = identical)';}
  document.querySelectorAll('button').forEach(function(b){b.className=b.dataset.m==mode?'on':'';});}
function setMode(m){mode=m;clearInterval(timer);timer=null;if(m=='blink'){showB=true;timer=setInterval(function(){showB=!showB;apply();},700);}apply();}
document.querySelectorAll('button').forEach(function(b){b.onclick=function(){setMode(b.dataset.m);};});
function at(e){var r=st.getBoundingClientRect();pos=Math.min(100,Math.max(0,(e.clientX-r.left)/r.width*100));apply();}
st.addEventListener('pointerdown',function(e){if(mode=='slide'){down=true;st.setPointerCapture(e.pointerId);at(e);}});
st.addEventListener('pointermove',function(e){if(mode=='slide'&&(down||e.pointerType=='mouse'))at(e);});
st.addEventListener('pointerup',function(){down=false;});
document.addEventListener('keydown',function(e){if(e.key==' '){e.preventDefault();if(mode!='blink')setMode('blink');clearInterval(timer);timer=null;showB=!showB;apply();}
  var m={'1':'slide','2':'blink','3':'side','4':'diff'}[e.key];if(m)setMode(m);});
A.onload=B.onload=function(){size();apply();};window.onresize=size;if(A.complete&&B.complete){size();apply();}
</script></body></html>"""


def compare(arg=""):
    """b / --compare: before (pinned item, else the previous image) vs after (the current item),
    or two given files, on a page with a slider, blink, side by side and a difference view."""
    if arg:
        pa, _, pb = arg.partition("\t")
        ea, eb = item_for(pa), item_for(pb)
    else:
        eb = current()
        if eb is None:
            warn("nothing shown yet"); return
        if pinned and pinned[0] != eb[0]:
            ea = pinned
        else:
            idx = list(history).index(eb) if eb in history else len(history) - 1
            ea = next((h for h in reversed(list(history)[:idx]) if item_image(h[0])), None)
        if ea is None:
            warn("no earlier image to compare with (pin one with p, or pass two files)"); return
    sa, sb = item_image(ea[0]), item_image(eb[0])
    if not sa or not sb:
        warn("compare works on images (stills, PDFs, slides, snapshots), not pages"); return
    port = ann_server()
    _ann["n"] += 1
    cid = str(_ann["n"])
    nm = lambda it: os.path.basename(it[0]) if len(it) == 1 else f"{len(it)} images"
    _ann["items"][cid] = {"id": cid, "src": sa, "src2": sb, "name": nm(ea[0]), "name2": nm(eb[0])}
    url = f"http://127.0.0.1:{port}/{ANN_TOKEN}/c/{cid}"
    print(f"\n\033[1;33m── ⇆ compare {nm(ea[0])} → {nm(eb[0])}\033[0;2m  slider · blink · side by side · difference · "
          f"{link(url, 'open')}\033[0m", flush=True)
    open_tool_page(url)


def ann_finished():
    while _ann["done"]:
        rec = _ann["done"].popleft()
        if rec.get("kind") == "atoms":
            n = len(rec["atoms"])
            print(f"\n\033[1;33m── ✎ {n} atom{'s' if n != 1 else ''} picked\033[0;2m  "
                  f"they go to Claude with your next message\033[0m", flush=True)
            for m in rec["atoms"][:12]:
                print(f"\033[2m    {m['label']}" + (f"  — {m['note']}" if m["note"] else "") + "\033[0m", flush=True)
            if rec.get("marked"):
                draw_image(rec["marked"])
            continue
        n = len(rec["marks"])
        print(f"\n\033[1;33m── ✎ {n} mark{'s' if n != 1 else ''} saved\033[0;2m  "
              f"they go to Claude with your next message\033[0m", flush=True)
        draw_image(rec["marked"])


def handle(cmd, arg=""):
    if cmd == "pin":
        if arg:
            set_pin(item_for(arg))
        else:
            set_pin(None if pinned else current())
    elif cmd == "unpin":
        set_pin(None)
    elif cmd == "annotate":
        annotate(item_for(arg) if arg else current())
    elif cmd == "redraw":
        globals()["view"] = None
        redraw()
    elif cmd in ("prev", "next"):
        step(-1 if cmd == "prev" else 1)
    elif cmd == "compare":
        compare(arg)
    elif cmd == "copy":
        copy_item(current())
    elif cmd == "open":
        open_item(current())
    elif cmd == "help":
        print("\033[2m  ←/→  browse earlier items one at a time (→ past the newest: back to live)\n"
              "  p  pin the item at the top, newest below (again: unpin)\n"
              "  a  mark up the item: click/drag on it (structures: click atoms), add notes, Send;\n"
              "     Claude gets it with your next message\n"
              "  b  before/after: pinned (or previous) image vs this one, slider / blink / side by side\n"
              "  c  copy the image to the clipboard (pages: the path)   o  open in its default app\n"
              "  r  redraw\n"
              "  from Claude: imgwatch --pin PATH | --unpin | --annotate [PATH] | --compare [A B]\033[0m", flush=True)


def read_control():
    """Commands from `imgwatch --pin/--unpin/--annotate` run inside this Claude session."""
    if not CTRL or not CTRL.exists():
        return
    tmp = CTRL.with_name(CTRL.name + ".reading")
    try:
        CTRL.replace(tmp)
        lines = tmp.read_text().splitlines()
        tmp.unlink()
    except OSError:
        return
    for line in lines:
        cmd, _, arg = line.partition("\t")
        handle(cmd, arg)


def claude_ancestor():
    pid = os.getppid()
    while pid and pid > 1:
        try:
            comm = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True).stdout
            ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        except OSError:
            return None
        if "claude" in comm:
            return pid
        pid = int(ppid or 0)
    return None


def send_control(cmd, arg=""):
    """Run from inside a Claude session: hand a command to the watcher owned by that session."""
    owner = claude_ancestor()
    if not owner:
        print("imgwatch: not running inside a Claude session", file=sys.stderr); return 1
    if not subprocess.run(["pgrep", "-f", rf"imgwatch\.py --owner {owner}( |$)"], capture_output=True).stdout:
        print(f"imgwatch: no imgwatch pane for this Claude session (pid {owner})", file=sys.stderr); return 1
    if arg:
        parts = [os.path.abspath(os.path.expanduser(x)) for x in arg.split("\t")]
        for x in parts:
            if not os.path.exists(x):
                print(f"imgwatch: no such file: {x}", file=sys.stderr); return 1
        arg = "\t".join(parts)
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / f"control_{owner}", "a") as f:
        f.write(f"{cmd}\t{arg}\n")
    print({"pin": "pinned in the imgwatch pane", "unpin": "unpinned",
           "annotate": "mark-up page opened; the user's marks arrive with their next message",
           "compare": "compare page opened in the imgwatch browser pane"}[cmd])
    return 0


# ---------------------------------------------------------------- self-reload
# A `git pull` or an edit of this file restarts the pane in place (os.execv: same pid, same pane,
# same command line, so the autostart hook's pgrep still matches), keeping what it was showing.
SELF = Path(__file__).resolve()
RELOAD = STATE / f"reload_{a.owner or os.getpid()}.json"


def _self_mtime():
    try:
        return SELF.stat().st_mtime_ns
    except OSError:
        return None


_self = {"seen": _self_mtime(), "changed": None, "bad": None}


def maybe_reload():
    """Called when idle. Restart once the file has been stable for 1 s and compiles; a version
    that doesn't compile is reported once and the old one keeps running."""
    if a.no_reload:
        return
    m = _self_mtime()
    if m is None or m in (_self["seen"], _self["bad"]):
        return
    if not _self["changed"] or _self["changed"][0] != m:
        _self["changed"] = (m, time.time()); return
    if time.time() - _self["changed"][1] < 1.0:
        return
    try:
        compile(SELF.read_text(), str(SELF), "exec")
    except (SyntaxError, ValueError, OSError) as e:
        _self["bad"] = m
        warn(f"imgwatch.py changed but doesn't compile ({type(e).__name__}: {e}); still running the old version")
        return
    state = {"history": [[list(it), ts] for it, ts in history],
             "pinned": [list(pinned[0]), pinned[1]] if pinned else None,
             "updates": [[list(k), v] for k, v in updates.items()], "time": time.time()}
    try:
        RELOAD.write_text(json.dumps(state))
    except OSError:
        pass
    print("\n\033[1;33m── ↻ imgwatch.py changed: restarting with the new version\033[0m", flush=True)
    cleanup(close_browser=False)
    os.execv(sys.executable, [sys.executable, str(SELF)] + sys.argv[1:])


def restore_after_reload():
    """Pick up what the previous version of this watcher was showing (written just before execv)."""
    global pinned
    try:
        st = json.loads(RELOAD.read_text())
        RELOAD.unlink()
    except (OSError, ValueError):
        return False
    if time.time() - st.get("time", 0) > 120:
        return False
    for it, ts in st.get("history", []):
        history.append((tuple(it), ts))
    for k, v in st.get("updates", []):
        updates[tuple(k)] = v
    if st.get("pinned"):
        pinned = (tuple(st["pinned"][0]), st["pinned"][1])
    return True


def cleanup(close_browser=True):
    """Undo what the watcher set up: tty mode, FSEvents thread, headless Chrome, browser pane."""
    global _driver
    if _tty_saved is not None:
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _tty_saved)
        except Exception:
            pass
    if fsw:
        try:
            fsw.stop()
        except Exception:
            pass
    if close_browser and (_browser_id or BROWSER_STATE.exists()) and html_mode() == "browser":
        close_browser_pane()
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


# ---------------------------------------------------------------- main
if a.version:
    print(f"imgwatch {version()}"); sys.exit(0)
if a.doctor:
    sys.exit(doctor())
if a.pin is not None:
    sys.exit(send_control("pin", a.pin))
if a.unpin:
    sys.exit(send_control("unpin"))
if a.annotate is not None:
    sys.exit(send_control("annotate", a.annotate))
if a.compare is not None:
    if len(a.compare) not in (0, 2):
        print("imgwatch: --compare takes two files, or none", file=sys.stderr); sys.exit(2)
    sys.exit(send_control("compare", "\t".join(a.compare)))

STATE.mkdir(parents=True, exist_ok=True)
prune()
QUEUE.touch()
qpos = QUEUE.stat().st_size
CTRL = STATE / f"control_{a.owner}" if a.owner else None
if CTRL:
    CTRL.unlink(missing_ok=True)  # stale commands from an earlier watcher of the same process

fsw = None
if not a.no_poll and not a.poll:
    try:
        fsw = FSWatch()
    except Exception:  # watchdog missing or FSEvents unavailable
        fsw = None
if not a.no_poll and not fsw:  # baseline for polling: don't replay files that already exist
    for d in session_dirs():
        for p in scan(d, a.depth):
            try:
                seen[p] = os.stat(p).st_mtime
            except FileNotFoundError:
                pass

# single keys typed into the pane; only when we own the foreground of a real tty (never in tests
# or when backgrounded, where switching the tty mode would stop the process)
_tty_saved = None
try:
    import termios, tty
    fd = sys.stdin.fileno()
    if sys.stdin.isatty() and os.tcgetpgrp(fd) == os.getpgrp():
        _tty_saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        KEYS = True
except (ImportError, OSError, ValueError):
    pass
KEYMAP = {"p": "pin", "a": "annotate", "b": "compare", "c": "copy", "o": "open", "r": "redraw",
          "?": "help", "h": "help", ",": "prev", ".": "next", "\x1b[D": "prev", "\x1b[C": "next",
          "\x1bOD": "prev", "\x1bOC": "next"}


def keys_in(buf):
    """Split what one read returned into known keys (arrows arrive as escape sequences)."""
    out, i = [], 0
    while i < len(buf):
        seq = next((k for k in ("\x1b[D", "\x1b[C", "\x1bOD", "\x1bOC") if buf.startswith(k, i)), None)
        if seq:
            out.append(seq); i += len(seq); continue
        if buf[i] == "\x1b":   # some other escape sequence: skip it whole
            m = re.match(r"\x1b(\[[0-9;]*[A-Za-z~]|O.)?", buf[i:])
            i += len(m.group(0)); continue
        out.append(buf[i].lower()); i += 1
    return [k for k in out if k in KEYMAP]

signal.signal(signal.SIGWINCH, on_winch)
signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGHUP, on_term)
polling = "(off)" if a.no_poll else ", ".join(map(str, dirs)) + (f" + scratch of session {a.session[:8]}" if a.session and not a.dirs else "")
if restore_after_reload():
    redraw()
    print(f"\033[2m  ↻ reloaded: now {version()}\033[0m", flush=True)
else:
    banner()
batch, first_new, last_new = [], 0.0, 0.0
try:
    while True:
        new = []
        size = QUEUE.stat().st_size if QUEUE.exists() else 0
        if size < qpos:  # queue was trimmed by another watcher: continue from its new end
            qpos = size
        if size > qpos:
            with open(QUEUE) as f:
                f.seek(qpos); lines = f.read().splitlines(); qpos = f.tell()
            for line in filter(None, map(str.strip, lines)):
                sid, _, path = line.rpartition("\t")  # "session_id<TAB>path" (or bare path)
                if a.session and sid and sid != a.session:
                    continue
                new.append(path)  # explicit Read/Write: always show, even if seen before
        if not a.no_poll:
            roots = session_dirs()
            if fsw:
                fsw.sync(roots)
                cand = fsw.drain()
            else:
                cand = sorted(p for d in roots for p in scan(d, a.depth))
            for p in cand:
                try:
                    if seen.get(p) != os.stat(p).st_mtime and p not in new:
                        new.append(p)
                except FileNotFoundError:
                    pass
        now = time.time()
        if new:
            if not batch:
                first_new = now
            batch += [p for p in new if p not in batch]
            last_new = now
        # files may still be arriving (a burst): draw once 0.5 s pass without new ones, or after 3 s
        if batch and (not a.burst or now - last_new > 0.5 or now - first_new > 3):
            show(batch); batch = []
        if not batch:
            read_control()
        if not batch and not new:
            maybe_reload()
        if a.owner:
            try:
                os.kill(a.owner, 0)
            except ProcessLookupError:
                break
        t_end = time.time() + (0.2 if batch else a.interval)
        while time.time() < t_end:  # short naps so a resize / key / finished mark-up is handled promptly
            if resized_at and time.time() - resized_at > 0.4:  # debounce: wait until dragging stops
                resized_at = None
                redraw()
            ann_finished()
            if KEYS:
                r, _, _ = select.select([sys.stdin], [], [], 0.1)
                if r:
                    for k in keys_in(os.read(sys.stdin.fileno(), 64).decode(errors="ignore")):
                        handle(KEYMAP[k])
            else:
                time.sleep(0.1)
except (KeyboardInterrupt, SystemExit):
    pass
finally:
    cleanup()

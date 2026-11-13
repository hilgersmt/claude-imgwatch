#!/usr/bin/env python3
"""imgwatch: show new media inline as Claude reads or creates it.

Sources:
  1. queue file fed by the PostToolUse hook ~/.claude/hooks/imgwatch-enqueue.sh
     (files Claude opens with Read or writes with Write)
  2. watched dirs (FSEvents via `watchdog` if installed, else polling) for new/modified
     media files (matplotlib, PyMOL, RDKit output...)

Shows stills inline, videos as animated-GIF previews, PDFs as their first page, HTML in an
iTerm2 browser pane (or as a headless screenshot / link), and structures (.pdb/.cif/.bcif/
.sdf/.mol2) in a Mol* viewer. Bursts of stills become one contact sheet.

Run it in a split pane; `imgpane` opens the split for you.  `imgwatch --doctor` checks the setup.
"""
import argparse, base64, hashlib, json, os, re, shutil, signal, subprocess, sys, threading, time
import urllib.parse, urllib.request
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
STILL_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff", ".bmp"}
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
EXTS = STILL_EXTS | VIDEO_EXTS | HTML_EXTS | PDF_EXTS | STRUCT_EXTS | SMILES_EXTS | PSE_EXTS | OFFICE_EXTS
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
ap.add_argument("-V", "--version", action="store_true", help="print the version and exit")
ap.add_argument("--doctor", action="store_true", help="check the installation and exit")
a = ap.parse_args()
if a.session:  # one browser pane per Claude session, not one shared by all of them
    BROWSER_STATE = BROWSER_STATE.with_name(f"browser_session_{a.session}")


def version():
    try:
        out = subprocess.run(["git", "-C", str(HERE), "log", "-1", "--format=%h %cs"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    return out or "unknown"


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


def draw_image(src):
    """Draw one image file inline in the current terminal."""
    if TERM == "iterm" and os.path.exists(IMGCAT):
        # -l: one escape sequence per image; the multipart protocol can garble if other output interleaves
        subprocess.run([IMGCAT, "-l", "-W", a.width, src])
    elif TERM in ("iterm", "wezterm"):
        # iTerm2 inline-image protocol, single sequence (WezTerm implements it too)
        data = base64.b64encode(Path(src).read_bytes()).decode()
        sys.stdout.write(f"\033]1337;File=inline=1;width={a.width};preserveAspectRatio=1;"
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
    for f in STATE.glob("browser_session_*"):
        try:
            if now - f.stat().st_mtime > 86400:
                f.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- doctor
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
        else:
            out("ok" if hit[0].get("Custom Command") == "Browser" else "FAIL",
                f"profile '{prof}' " + ("is a Browser profile" if hit[0].get("Custom Command") == "Browser"
                                        else "exists but is not a Browser-type profile"))
    # hooks and links
    sp = Path.home() / ".claude/settings.json"
    try:
        hooks = json.loads(sp.read_text()).get("hooks", {})
    except (OSError, ValueError):
        hooks = {}
        out("FAIL", f"cannot read {sp}")
    for ev, name in (("PostToolUse", "imgwatch-enqueue.sh"), ("SessionStart", "imgpane-autostart.sh")):
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
    if os.path.splitext(path)[1].lower() not in EXTS:
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
                elif e.is_file() and os.path.splitext(e.name)[1].lower() in EXTS:
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
        src = ql_png(p, 600) if p.lower().endswith(".svg") else p
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


def molstar_assets():
    VENDOR.mkdir(parents=True, exist_ok=True)
    got = []
    for ext in ("js", "css"):
        f = VENDOR / f"molstar-{MOLSTAR_VERSION}.{ext}"
        if not f.exists():
            url = f"https://cdn.jsdelivr.net/npm/molstar@{MOLSTAR_VERSION}/build/viewer/molstar.{ext}"
            try:
                print(f"\033[2m  fetching Mol* {MOLSTAR_VERSION} {ext} (once)\033[0m", flush=True)
                with urllib.request.urlopen(url, timeout=60) as r:
                    f.write_bytes(r.read())
            except OSError as e:
                warn(f"cannot fetch Mol*: {e}"); return None
        got.append(f.read_text(errors="replace"))
    return got


def structure_html(p):
    """Self-contained Mol* page with the structure embedded (no file:// fetches needed)."""
    ext = os.path.splitext(p)[1].lower()
    key = cache_key(p, "molstar2", MOLSTAR_VERSION)
    if not key:
        return None
    out = CACHE / (key + "_" + os.path.basename(p).replace(" ", "_") + ".html")
    if out.exists():
        return str(out)
    assets = molstar_assets()
    if not assets:
        return None
    js, css = assets
    raw = Path(p).read_bytes()
    if ext == ".bcif":
        data = f'Uint8Array.from(atob("{base64.b64encode(raw).decode()}"), c => c.charCodeAt(0))'
    else:
        data = json.dumps(raw.decode(errors="replace"))
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{os.path.basename(p)}</title>
<style>{css}</style><style>html,body{{margin:0;height:100%;background:#111}}#app{{position:absolute;inset:0}}</style>
</head><body><div id="app"></div><script>{js}</script><script>
molstar.Viewer.create('app', {{layoutIsExpanded: false, layoutShowControls: false, layoutShowSequence: true,
  layoutShowLog: false, layoutShowLeftPanel: false, viewportShowExpand: false, viewportShowSelectionMode: false,
  viewportShowAnimation: false, viewportBackgroundColor: '#111111'}}).then(function (v) {{
  return v.loadStructureFromData({data}, {json.dumps(STRUCT_FORMATS[ext])}, {{dataLabel: {json.dumps(os.path.basename(p))}}});
}});
</script></body></html>"""
    CACHE.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return str(out)


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


def html_to_png(p):
    """Screenshot a local page with headless Chrome (cached by path+mtime+params)."""
    global _driver
    CACHE.mkdir(parents=True, exist_ok=True)
    key = cache_key(p, a.shot_px, a.shot_wait)
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
        _driver.get("file://" + urllib.parse.quote(os.path.abspath(p)))
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
    if mode == "browser":
        print("\033[2m  → browser pane\033[0m", flush=True)
        # iTerm2's browser rejects file:///path as "Invalid URL" but takes file://localhost/path
        if browser_load(file_url(fit_copy(p) if orig is None else p)):
            return None
        warn("no browser pane — falling back to a link")
        mode = "link"
    if mode == "shot":
        print(f"\033[2m  → headless-Chrome screenshot ({a.shot_px}px, {a.shot_wait:g}s settle)\033[0m", flush=True)
        png = html_to_png(p)
        if png:
            return png
        mode = "link"
    # link: OSC 8 hyperlink — cmd-click opens the page (for structures: the Mol* page) in the default browser
    print("  " + link(file_url(p), f"\033[4;36m{os.path.basename(orig or p)}\033[0m"), flush=True)
    return None


# ---------------------------------------------------------------- rendering
seen = {}  # path -> mtime already shown (or present at start)
history = deque(maxlen=max(a.keep, 1))  # (paths, time shown) of recent items, redrawn on resize
resized_at = None  # set by SIGWINCH; inline images are sized when drawn, so redraw after a resize


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


def show(paths):
    """Show a batch of new files: a burst of stills becomes one contact sheet."""
    paths = settle(list(dict.fromkeys(paths)))
    stills = [p for p in paths if os.path.splitext(p)[1].lower() in STILL_EXTS]
    if a.burst and len(stills) > a.burst:
        rest = [p for p in paths if p not in stills]
        items = [tuple(stills)] + [(p,) for p in rest]
    else:
        items = [(p,) for p in paths]
    for it in items:
        history.append((it, f"{datetime.now():%H:%M:%S}"))
        render(*history[-1])


def header(p, ts, label=None):
    d = os.path.dirname(os.path.abspath(p))
    name = link(file_url(p), label or os.path.basename(p))
    print(f"\n\033[1;36m── {name}\033[0;2m  {ts}  {link(file_url(d), d)}\033[0m", flush=True)


def render(item, ts):
    if len(item) > 1:  # burst
        live = [p for p in item if os.path.exists(p)]
        if not live:
            return
        d = os.path.dirname(os.path.abspath(live[0]))
        print(f"\n\033[1;36m── {len(live)} images\033[0;2m  {ts}  {link(file_url(d), d)}\033[0m", flush=True)
        sheet = contact_sheet(live)
        if sheet:
            draw_image(sheet)
        else:  # no PIL: draw the last few, list the rest
            for p in live[-3:]:
                draw_image(p)
            print("\033[2m  + " + ", ".join(os.path.basename(p) for p in live[:-3]) + "\033[0m", flush=True)
        return
    p = item[0]
    if not os.path.exists(p):
        return
    header(p, ts)
    ext = os.path.splitext(p)[1].lower()
    src = p
    if ext in HTML_EXTS or ext in STRUCT_EXTS:
        mb = os.path.getsize(p) / 1e6
        if mb > a.html_max_mb:
            warn(f"skipped: {mb:.0f} MB > --html-max-mb {a.html_max_mb:g}"); return
        if ext in (".sdf", ".mol") and not is_3d_single(p):
            return show_grid(p)
        if ext in STRUCT_EXTS:
            if html_mode() == "off":
                return
            print(f"\033[2m  structure → Mol* {MOLSTAR_VERSION}\033[0m", flush=True)
            page = structure_html(p)
            if not page:
                print("  " + link(file_url(p), f"\033[4;36m{os.path.basename(p)}\033[0m"), flush=True); return
            src = show_html(page, orig=p)
        else:
            src = show_html(p)
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
    elif ext in SMILES_EXTS:
        return show_grid(p)
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
    draw_image(src)


def show_grid(p):
    src, n, bad = compound_grid(p)
    more = f", showing {a.max_mols}" if n > a.max_mols else ""
    print(f"\033[2m  {n} molecule{'s' if n != 1 else ''}{more}" + (f", {bad} unreadable" if bad else "") + "\033[0m", flush=True)
    if src:
        draw_image(src)


def banner():
    watch = "FSEvents" if fsw else "polling"
    print(f"\033[1mimgwatch\033[0m {version()}  {watch}: {polling}  + hook queue   (Ctrl-C to stop)", flush=True)


def redraw():
    clear_all()
    banner()
    for item in list(history)[-a.keep:] if a.keep > 0 else []:
        render(*item)


def on_winch(*_):
    global resized_at
    resized_at = time.time()


def on_term(*_):
    raise SystemExit(0)  # run the finally block (close the browser pane) on kill / pane close


# ---------------------------------------------------------------- main
if a.version:
    print(f"imgwatch {version()}"); sys.exit(0)
if a.doctor:
    sys.exit(doctor())

STATE.mkdir(parents=True, exist_ok=True)
prune()
QUEUE.touch()
qpos = QUEUE.stat().st_size

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

signal.signal(signal.SIGWINCH, on_winch)
signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGHUP, on_term)
polling = "(off)" if a.no_poll else ", ".join(map(str, dirs)) + (f" + scratch of session {a.session[:8]}" if a.session and not a.dirs else "")
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
        if a.owner:
            try:
                os.kill(a.owner, 0)
            except ProcessLookupError:
                break
        t_end = time.time() + (0.2 if batch else a.interval)
        while time.time() < t_end:  # short naps so a resize is handled promptly
            if resized_at and time.time() - resized_at > 0.4:  # debounce: wait until dragging stops
                resized_at = None
                redraw()
            time.sleep(0.1)
except (KeyboardInterrupt, SystemExit):
    pass
finally:
    if fsw:
        try:
            fsw.stop()
        except Exception:
            pass
    if (_browser_id or BROWSER_STATE.exists()) and html_mode() == "browser":
        close_browser_pane()
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass

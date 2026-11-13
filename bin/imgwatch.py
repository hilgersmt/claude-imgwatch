#!/usr/bin/env python3
"""imgwatch: show new images inline (iTerm2 imgcat) as Claude reads or creates them.

Sources:
  1. queue file fed by the PostToolUse hook ~/.claude/hooks/imgwatch-enqueue.sh
     (images Claude opens with Read or writes with Write)
  2. polling of watched dirs for new/modified image files (matplotlib, PyMOL, RDKit output...)

Run it in a second iTerm2 pane; `imgpane` opens the split for you.
"""
import argparse, hashlib, os, signal, subprocess, time, urllib.parse
from collections import deque
from datetime import datetime
from pathlib import Path

IMGCAT = "/Applications/iTerm.app/Contents/Resources/utilities/imgcat"
QUEUE = Path.home() / ".claude/imgwatch/queue"
CACHE = Path.home() / ".claude/imgwatch/cache"
BROWSER_STATE = Path.home() / ".claude/imgwatch/browser_session"
STILL_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".tif", ".tiff", ".bmp"}
# iTerm2's inline-image protocol animates GIFs, so a video is shown by
# transcoding a capped preview to an animated GIF and imgcat-ing that.
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".mpg", ".mpeg"}
# HTML can't be drawn inline at all. Three lanes, see --html-mode:
#   browser  push file:// to an iTerm2 Browser-profile pane (real JS, real interactivity)
#   shot     headless-Chrome screenshot, drawn inline like any other still
#   link     just print a clickable file:// hyperlink
HTML_EXTS = {".html", ".htm"}
EXTS = STILL_EXTS | VIDEO_EXTS | HTML_EXTS
FFMPEG = next((f for f in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")
               if os.path.exists(f)), "ffmpeg")
SKIP = {"node_modules", ".git", "__pycache__", "Library"}

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("dirs", nargs="*", help="dirs to poll (default: Claude scratch root + cwd)")
ap.add_argument("-w", "--width", default="100%", help="imgcat width, e.g. 100%%, 80 (cells), 600px")
ap.add_argument("-i", "--interval", type=float, default=1.0, help="poll interval in s (default 1)")
ap.add_argument("-d", "--depth", type=int, default=5, help="max dir depth to scan (default 5)")
ap.add_argument("--no-poll", action="store_true", help="only show images from the hook queue")
ap.add_argument("-k", "--keep", type=int, default=4, help="images to redraw after a pane resize (default 4)")
ap.add_argument("--owner", type=int, help="exit when this process (the Claude CLI) ends")
ap.add_argument("-s", "--session", help="Claude session id: show only that session's images and poll its scratch dir")
ap.add_argument("--video-seconds", type=float, default=8.0, metavar="S",
                help="seconds of video to preview as an animated GIF (default 8; 0 = whole clip)")
ap.add_argument("--video-fps", type=float, default=10.0, metavar="N", help="GIF frame rate (default 10)")
ap.add_argument("--video-px", type=int, default=720, metavar="W", help="GIF pixel width (default 720)")
ap.add_argument("--no-video", action="store_true", help="ignore video files entirely")
ap.add_argument("--html-mode", choices=["auto", "browser", "shot", "link", "off"], default="auto",
                help="how to show .html: browser pane, headless screenshot, clickable link, or ignore "
                     "(auto = browser if --browser-profile is set, else shot, else link)")
ap.add_argument("--browser-profile", metavar="NAME", default=os.environ.get("IMGWATCH_BROWSER_PROFILE"),
                help="name of an iTerm2 Browser-type profile; its pane is reused for every page "
                     "(env IMGWATCH_BROWSER_PROFILE)")
ap.add_argument("--shot-wait", type=float, default=2.0, metavar="S",
                help="seconds to let JS render before screenshotting (default 2)")
ap.add_argument("--shot-px", type=int, default=1400, metavar="W", help="screenshot viewport width (default 1400)")
ap.add_argument("--html-max-mb", type=float, default=40.0, metavar="MB",
                help="skip .html bigger than this (default 40)")
a = ap.parse_args()

tmproot = Path(f"/private/tmp/claude-{os.getuid()}")
if a.dirs:
    dirs = [Path(d).expanduser().resolve() for d in a.dirs]
elif a.session:  # this session's scratch dir(s) appear lazily, so re-glob each poll (see session_dirs)
    dirs = [Path.cwd()] if Path.cwd() != Path.home() else []  # never deep-scan all of ~
else:
    dirs = [tmproot, Path.cwd()]


def session_dirs():
    return dirs + (sorted(tmproot.glob(f"*/{a.session}")) if a.session and not a.dirs else [])


_html_mode = None   # resolved lazily by html_mode()
_browser_id = None  # iTerm2 session id of the browser pane
_driver = None      # headless Chrome, reused across screenshots

seen = {}  # path -> mtime already shown (or present at start)
history = deque(maxlen=max(a.keep, 1))  # (path, time shown) of recent images, redrawn on resize
resized_at = None  # set by SIGWINCH; inline images are sized when drawn, so redraw after a resize


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


def show(p):
    try:
        for _ in range(10):  # wait until the file stops growing
            s1 = os.path.getsize(p); time.sleep(0.3)
            if os.path.getsize(p) == s1 and s1 > 0:
                break
        seen[p] = os.stat(p).st_mtime
    except FileNotFoundError:
        return
    history.append((p, f"{datetime.now():%H:%M:%S}"))
    render(*history[-1])


def video_to_gif(p):
    """Transcode a capped preview of a video to an animated GIF (cached by path+mtime).
    iTerm2 animates GIFs inline, so this is what 'playing' in the pane means here."""
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        key = f"{os.path.abspath(p)}|{os.stat(p).st_mtime_ns}|{a.video_seconds}|{a.video_fps}|{a.video_px}"
    except FileNotFoundError:
        return None
    gif = CACHE / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".gif")
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
        print("\033[33m  (ffmpeg not found — cannot preview video)\033[0m", flush=True); return None
    except subprocess.TimeoutExpired:
        print("\033[33m  (ffmpeg timed out — video preview skipped)\033[0m", flush=True); return None
    if r.returncode != 0 or not gif.exists() or gif.stat().st_size == 0:
        err = (r.stderr or b"").decode(errors="replace").strip().splitlines()
        print(f"\033[33m  (video preview failed: {err[-1] if err else 'ffmpeg error'})\033[0m", flush=True)
        return None
    return str(gif)


def html_mode():
    """Resolve --html-mode auto once we know what is actually available."""
    global _html_mode
    if _html_mode:
        return _html_mode
    m = a.html_mode
    if m == "auto":
        if a.browser_profile:
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
        print("\033[33m  (python module 'iterm2' not installed — pip install iterm2)\033[0m", flush=True)
        return None
    out = {}

    async def main(connection):
        app = await iterm2.async_get_app(connection)
        out["r"] = await fn(app, connection, iterm2)

    try:
        iterm2.run_until_complete(main)
    except Exception as e:  # API disabled, permission refused, no connection...
        print(f"\033[33m  (iTerm2 API: {type(e).__name__}: {str(e)[:80]})\033[0m", flush=True)
        return None
    return out.get("r")


def browser_pane():
    """Session id of the browser pane, creating it by splitting this one on first use."""
    global _browser_id
    if _browser_id:
        return _browser_id
    if BROWSER_STATE.exists():
        _browser_id = BROWSER_STATE.read_text().strip() or None

    async def get(app, conn, iterm2):
        if _browser_id and app.get_session_by_id(_browser_id):
            return _browser_id                      # pane from an earlier run still open
        me = os.environ.get("ITERM_SESSION_ID", "").partition(":")[2] or None
        mine = app.get_session_by_id(me) if me else None
        if mine is None:
            return None
        new = await mine.async_split_pane(vertical=False, profile=a.browser_profile)
        return new.session_id

    sid = it2_call(get)
    if sid:
        _browser_id = sid
        BROWSER_STATE.parent.mkdir(parents=True, exist_ok=True)
        BROWSER_STATE.write_text(sid)
    return sid


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
    try:
        key = f"{os.path.abspath(p)}|{os.stat(p).st_mtime_ns}|{a.shot_px}|{a.shot_wait}"
    except FileNotFoundError:
        return None
    png = CACHE / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".png")
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
        _driver.set_window_size(a.shot_px, min(int(h) + 40, 4000))
        time.sleep(0.3)
        _driver.save_screenshot(str(png))
    except Exception as e:
        print(f"\033[33m  (screenshot failed: {type(e).__name__}: {str(e)[:80]})\033[0m", flush=True)
        try:
            if _driver:
                _driver.quit()
        except Exception:
            pass
        _driver = None
        return None
    return str(png) if png.exists() and png.stat().st_size else None


def show_html(p):
    mode = html_mode()
    if mode == "off":
        return None
    try:
        mb = os.path.getsize(p) / 1e6
    except FileNotFoundError:
        return None
    if mb > a.html_max_mb:
        print(f"\033[33m  (skipped: {mb:.0f} MB > --html-max-mb {a.html_max_mb:g})\033[0m", flush=True)
        return None
    url = "file://" + urllib.parse.quote(os.path.abspath(p))
    if mode == "browser":
        print(f"\033[2m  → iTerm2 browser pane\033[0m", flush=True)
        # iTerm2's browser rejects file:///path as "Invalid URL" but takes file://localhost/path
        if browser_load("file://localhost" + urllib.parse.quote(fit_copy(p))):
            return None
        print("\033[33m  (no browser pane — falling back to a link)\033[0m", flush=True)
        mode = "link"
    if mode == "shot":
        print(f"\033[2m  html → headless-Chrome screenshot ({a.shot_px}px, {a.shot_wait:g}s settle)\033[0m", flush=True)
        png = html_to_png(p)
        if png:
            return png
        mode = "link"
    # link: OSC 8 hyperlink — cmd-click opens it in the default browser
    print(f"\033]8;;{url}\033\\\033[4;36m{os.path.basename(p)}\033[0m\033]8;;\033\\", flush=True)
    return None


def render(p, ts):
    if not os.path.exists(p):
        return
    print(f"\n\033[1;36m── {os.path.basename(p)}\033[0;2m  {ts}  {os.path.dirname(p)}\033[0m", flush=True)
    src = p
    low = p.lower()
    ext = os.path.splitext(low)[1]
    if ext in HTML_EXTS:
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
    if low.endswith(".svg"):  # imgcat can't render SVG; rasterise with Quick Look
        out = Path(os.environ.get("TMPDIR", "/tmp"))
        subprocess.run(["qlmanage", "-t", "-s", "1200", "-o", str(out), p], capture_output=True)
        src = str(out / (os.path.basename(p) + ".png"))
    # -l: one escape sequence per image; the multipart protocol can garble if other output interleaves
    subprocess.run([IMGCAT, "-l", "-W", a.width, src])


QUEUE.parent.mkdir(parents=True, exist_ok=True)
QUEUE.touch()
qpos = QUEUE.stat().st_size
if not a.no_poll:  # baseline: don't replay images that already exist
    for d in session_dirs():
        for p in scan(d, a.depth):
            try:
                seen[p] = os.stat(p).st_mtime
            except FileNotFoundError:
                pass

def banner():
    print(f"\033[1mimgwatch\033[0m  polling: {polling}  + hook queue   (Ctrl-C to stop)", flush=True)


def redraw():
    # clear screen + scrollback (iTerm2 keeps stale, mis-sized images in scrollback), then redraw recent images
    print("\033]1337;ClearScrollback\a\033[2J\033[3J\033[H", end="", flush=True)
    banner()
    for item in list(history)[-a.keep:] if a.keep > 0 else []:
        render(*item)


def on_winch(*_):
    global resized_at
    resized_at = time.time()


signal.signal(signal.SIGWINCH, on_winch)
polling = "(off)" if a.no_poll else ", ".join(map(str, dirs)) + (f" + scratch of session {a.session[:8]}" if a.session and not a.dirs else "")
banner()
try:
    while True:
        size = QUEUE.stat().st_size
        if size < qpos:  # queue was truncated
            qpos = 0
        if size > qpos:
            with open(QUEUE) as f:
                f.seek(qpos); lines = f.read().splitlines(); qpos = f.tell()
            for line in filter(None, map(str.strip, lines)):
                sid, _, path = line.rpartition("\t")  # "session_id<TAB>path" (or bare path)
                if a.session and sid and sid != a.session:
                    continue
                show(path)  # explicit Read/Write: always show, even if seen before
        if not a.no_poll:
            for d in session_dirs():
                for p in sorted(scan(d, a.depth)):
                    try:
                        if seen.get(p) != os.stat(p).st_mtime:
                            show(p)
                    except FileNotFoundError:
                        pass
        if a.owner:
            try:
                os.kill(a.owner, 0)
            except ProcessLookupError:
                break
        t_end = time.time() + a.interval
        while time.time() < t_end:  # short naps so a resize is handled promptly
            if resized_at and time.time() - resized_at > 0.4:  # debounce: wait until dragging stops
                resized_at = None
                redraw()
            time.sleep(0.1)
except KeyboardInterrupt:
    pass
finally:
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass

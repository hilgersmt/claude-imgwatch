# claude-imgwatch

A live media pane for Claude Code in iTerm2. Every interactive `claude` session gets its own
split pane that shows the images, videos and HTML pages that session produces or opens, as
they appear: PNG/JPEG/GIF/SVG inline, video as an animated GIF preview, and HTML (Plotly
etc.) as a real interactive page in an iTerm2 browser pane.

macOS + iTerm2 only (uses iTerm2's inline-image protocol, AppleScript and Python API).

## Reference versions (work Mac, 2026-11-13, all verified working together)

| Component | Version | Needed for |
|---|---|---|
| macOS | 26.5.1, arm64 | |
| iTerm2 | **3.7.3** | inline images (any 3.x); **Browser profiles need 3.7+** |
| Claude Code | 2.1.285 | `SessionStart` / `PostToolUse` hooks with `session_id`, `cwd`, `tool_input` on stdin |
| Python | 3.13.9 (Anaconda, `/opt/anaconda3/bin/python`) | stdlib is enough for stills |
| `iterm2` (pip) | 2.30 | HTML browser lane |
| `selenium` (pip) | 4.52.0 + Google Chrome 157 | HTML screenshot lane |
| ffmpeg | 8.0.1 (Homebrew, `/opt/homebrew/bin/ffmpeg`) | video lane |
| plotly | 6.4.0 | only for making test figures |

Pip gotchas seen with Anaconda:
- `pip install iterm2` pulls protobuf 7, which breaks streamlit 1.45. Pin back with
  `pip install --ignore-installed certifi 'protobuf<7'` (6.33.6 works for both).
- A plain `pip install selenium` fails trying to uninstall conda's RECORD-less certifi; use
  `pip install --ignore-installed certifi selenium`.

## Install / update

```bash
git clone <this repo> ~/src/claude-imgwatch
~/src/claude-imgwatch/install.sh -n    # dry run: shows what it would back up / link / add
~/src/claude-imgwatch/install.sh
```

`install.sh` is idempotent. It:
1. Writes `~/.config/imgwatch/config` if missing (picks the first Python that can `import iterm2`;
   edit it afterwards if that guess is wrong). An existing config is never touched.
2. Symlinks `~/bin/imgwatch`, `~/bin/imgpane`, `~/.claude/hooks/imgpane-autostart.sh`,
   `~/.claude/hooks/imgwatch-enqueue.sh` into the repo. Any existing non-symlink file at those
   paths is moved to `~/bin/backups/imgwatch_pre-repo_<stamp>/` first.
3. Copies `~/.claude/settings.json` to `~/.claude/backups/settings.json.<stamp>`, then *adds* the
   two hook entries if they are not there. Other hooks and settings are left alone.

Updating later = `git pull` (the symlinks pick it up; the next new Claude session uses it).

### iTerm2 setup (manual, once)
1. **Settings → General → Magic → Enable Python API** (browser lane). The `iterm2` module then
   connects without a prompt; the first `osascript` call may trigger a macOS Automation prompt
   for "iTerm2 controlling iTerm2": allow it (pane splitting needs it).
2. **A Browser-type profile named `imgbrowser`**: Settings → Profiles → `+` → set the profile
   type to *Browser* → name it `imgbrowser`. Nothing else needs changing. In the plist this shows
   as `"Custom Command": "Browser"`. A Dynamic Profile with that key is in
   `iterm2/imgbrowser.dynamic-profile.json` (copy to
   `~/Library/Application Support/iTerm2/DynamicProfiles/`). **Untested**: the GUI route is the
   one known to work. Either way, check with
   `/Applications/iTerm.app/Contents/Resources/utilities/it2 profile list`.
   No browser profile? Set `IMGWATCH_BROWSER_PROFILE=` (empty) in the config and HTML falls back
   to headless screenshots (or a clickable link without selenium).

## How it works

```
claude (iTerm2 pane)                               imgwatch pane (right, ~1/3 width)
 │ SessionStart hook ── imgpane-autostart.sh ──► imgpane (AppleScript split) ──► imgwatch --owner PID --session SID
 │ PostToolUse(Read|Write) ── imgwatch-enqueue.sh ──► ~/.claude/imgwatch/queue ──► (tailed, filtered by SID)
 │ figures written to scratch dir / cwd  ───────────────────────────────────────► (polled every 1 s)
 └ exits ───────────────────────────────────────────────────────────────────────► watcher sees owner gone, exits, pane closes
                                                     HTML ──► iTerm2 browser pane (below, via Python API)
```

### 1. `hooks/imgpane-autostart.sh` (SessionStart)
- Only runs when `TERM_PROGRAM=iTerm.app` with an `ITERM_SESSION_ID`, for interactive sessions
  (`CLAUDE_CODE_ENTRYPOINT` unset or `cli`, so `claude -p` / SDK runs are skipped), and not when
  `CLAUDE_NO_IMGPANE` is set (`CLAUDE_NO_IMGPANE=1 claude` = opt out for one launch).
- Reads `session_id` and `cwd` from the hook JSON on stdin.
- Finds the owning Claude CLI PID: `$CLAUDE_PID` if set, else walks up the parent chain to the
  first process whose `comm` contains `claude`.
- Dedup: looks for a watcher with `pgrep -f '^[^ ]+ [^ ]*/imgwatch\.py --owner <PID>( |$)'`.
  Same session id → it's a resume/compact, keep the pane. Different session id → it's `/clear`,
  kill the old watcher (its pane closes) and open a new one. The pattern is anchored on
  "`<interpreter> <path>/imgwatch.py`" so it never matches the `zsh -c` wrapper or a shell that
  merely mentions imgwatch. **If you change how imgwatch is launched, re-check this with
  `pgrep -fl imgwatch`**: a mismatch gives duplicate panes or stale ones after `/clear`.
- Starts `imgpane --owner PID --session SID [--browser-profile NAME]` in the background from the
  session's cwd.

### 2. `bin/imgpane` (AppleScript)
- Finds the iTerm2 session matching `$ITERM_SESSION_ID` (falls back to the current session),
  `split vertically with default profile command "/bin/zsh -c 'cd <cwd> && imgwatch …'"`. There's no
  interactive shell in the pane, so it closes when imgwatch exits.
- Width: `IMGPANE_WIDTH` = percent of the tab (default 33) or `<n>c` columns; minimum 20.
  Setting a pane's `columns` in iTerm2 resizes the **whole window**, so imgpane saves the window
  bounds, then up to 5 rounds of "set columns → restore bounds" (0.1 s delays) until it converges.
- Gives focus back to the Claude pane.
- Manual use: `imgpane [imgwatch args…]`, e.g. `imgpane -w 80% ~/figs`.

### 3. `hooks/imgwatch-enqueue.sh` (PostToolUse, matcher `Read|Write`)
- If `tool_input.file_path` has a media extension, appends `session_id<TAB>path` to
  `~/.claude/imgwatch/queue`. Always exits 0 so it can never block Claude.
- Note that when Claude `Read`s an image it also costs context (vision); writing figures into a
  polled dir shows them for free.

### 4. `bin/imgwatch` → `bin/imgwatch.py`
`bin/imgwatch` is a 3-line launcher: sources the config (with `set -a`, so the values are exported)
and `exec`s `$IMGWATCH_PYTHON imgwatch.py`. The watcher loop, every `--interval` (1 s):
- **Queue**: reads new bytes since the last offset (resets if the file shrank), skips lines
  whose session id isn't `--session`, and shows every remaining path, *even if already shown*.
- **Polling**: scans the session's scratch dir(s) `/private/tmp/claude-<uid>/*/<session_id>`
  (re-globbed every poll since it appears lazily) plus the cwd unless the cwd is `~` (never
  deep-scans home). Depth `--depth` 5; skips dot-dirs, `node_modules`, `.git`, `__pycache__`,
  `Library`. Files present at start are baselined (not replayed); new or changed mtime → shown.
- **Settle**: waits up to 10 × 0.3 s for the file size to stop changing before drawing.
- **Owner check**: `os.kill(owner, 0)`; gone → exit.
- **Resize**: SIGWINCH sets a flag; after 0.4 s with no further resize it clears screen *and*
  scrollback (`ESC]1337;ClearScrollback`, because iTerm2 keeps stale, mis-sized images there) and
  redraws the last `--keep` (4) items, since inline images are sized at draw time.

Rendering per type (each item gets a cyan header: name, time, folder):
- **Stills** `.png .jpg .jpeg .gif .webp .tif .tiff .bmp`: `imgcat -l -W <width>` (iTerm2's
  bundled `/Applications/iTerm.app/Contents/Resources/utilities/imgcat`). `-l` = legacy
  single-sequence mode. The multipart protocol garbles when two writers interleave.
- **SVG**: rasterised with Quick Look (`qlmanage -t -s 1200`) into `$TMPDIR`, then imgcat.
- **Video** `.mp4 .mov .m4v .webm .avi .mkv .mpg .mpeg`: ffmpeg →
  `fps=N,scale=W:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse`, loop
  forever, first `--video-seconds` only; iTerm2 animates GIFs inline. Cached in
  `~/.claude/imgwatch/cache/<sha1(path|mtime|params)>.gif` so a resize doesn't re-transcode.
  Missing, failed or timed-out (180 s) ffmpeg prints a warning and doesn't crash.
- **HTML** `.html .htm`, three lanes (`--html-mode auto` = browser if a browser profile is
  given, else shot if selenium imports, else link; files over `--html-max-mb` are skipped):
  - **browser**: on first use, splits the *imgwatch* pane horizontally with the Browser
    profile (`iterm2` API, `async_split_pane(vertical=False, profile=…)`), stores its session id
    in `~/.claude/imgwatch/browser_session` and reuses it (re-splits if it was closed). Each page
    goes in with `async_load_url`. Two gotchas:
    - iTerm2's browser rejects `file:///path` ("Invalid URL") but accepts `file://localhost/path`.
    - The original page is never loaded directly. A cached copy goes in
      (`cache/<hash>.html`) with `<base href="file://localhost/<original dir>/">`, so relative
      assets still resolve, plus an injected script that runs `Plotly.Plots.resize` on load,
      +300 ms, +1 s and on every ResizeObserver tick, and relayouts fixed-width figures wider
      than the pane down to the pane width. The pane's address bar therefore shows the cache path.
    - A brand-new browser pane shows iTerm2's onboarding page until the first URL loads.
  - **shot**: headless Chrome via selenium, viewport `--shot-px` (1400) wide at 2× scale,
    waits `--shot-wait` (2 s) for JS, grows the window to the page height (max 4000) and
    screenshots. Chrome **must** get `--use-gl=angle --use-angle=swiftshader
    --enable-unsafe-swiftshader` (not `--disable-gpu`), or Plotly 3D / Mol* screenshot as
    "WebGL is not supported". Cached like video.
  - **link**: prints an OSC 8 `file://` hyperlink (cmd-click opens the default browser).

### Flags (imgwatch)
| Flag | Default | |
|---|---|---|
| `dirs…` | scratch dir + cwd | explicit dirs to poll instead |
| `-w/--width` | `100%` | imgcat width (`80`, `600px`, …) |
| `-i/--interval` | 1.0 | poll seconds |
| `-d/--depth` | 5 | scan depth |
| `--no-poll` | off | queue only |
| `-k/--keep` | 4 | items redrawn after resize |
| `--owner PID` | | exit when PID ends |
| `-s/--session ID` | | filter queue + poll that session's scratch dir |
| `--video-seconds` / `--video-fps` / `--video-px` / `--no-video` | 8 / 10 / 720 / off | video preview |
| `--html-mode` | auto | `browser`, `shot`, `link`, `off` |
| `--browser-profile` | `$IMGWATCH_BROWSER_PROFILE` | iTerm2 Browser profile name |
| `--shot-wait` / `--shot-px` / `--html-max-mb` | 2 / 1400 / 40 | screenshot lane |

### Config (`~/.config/imgwatch/config`, see `config.example`)
`IMGWATCH_PYTHON`, `IMGWATCH_BROWSER_PROFILE` (default `imgbrowser`; empty = no browser lane),
`IMGPANE_WIDTH`. These have to live here: env vars in `~/.zshrc` do **not** reach the pane,
which runs imgwatch without a login shell.

### Runtime state (`~/.claude/imgwatch/`)
`queue` (append-only, shared by all sessions; safe to truncate any time), `cache/` (GIF/PNG/HTML
previews; safe to delete), `browser_session` (id of the reusable browser pane).

## Test checklist
1. Start `claude` in iTerm2: a right pane titled imgwatch opens and focus stays on the left.
2. Ask Claude for a matplotlib PNG, an SVG and an animated GIF in its scratch dir: all three
   appear, the GIF animates, the SVG is rasterised.
3. Ask it to `Read` one of the PNGs: it appears again (queue route).
4. Ask for a Plotly HTML (2D and 3D): it opens in a browser pane below imgwatch, hover/rotate
   work, and it refits when you drag the divider.
5. An mp4 (e.g. a matplotlib animation; even pixel dimensions or libx264 fails): plays as a GIF.
6. Drag the divider: the pane clears and redraws the last 4 items at the new width.
7. `/clear` → old pane closes, new one opens. Quit Claude → pane closes. Two Claude tabs → each
   pane shows only its own session's media.

## Uninstall
Remove the two hook entries from `~/.claude/settings.json`, delete the four symlinks and
`~/.config/imgwatch`, `~/.claude/imgwatch`. Pre-install files are in `~/bin/backups/imgwatch_pre-repo_*`.

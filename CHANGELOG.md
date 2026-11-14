# Changelog

Versions are git tags (`vMAJOR.MINOR.PATCH`); `imgwatch -V` and the pane banner show the tag and
commit, e.g. `v0.0.5 (1a2b3c4)`, or `v0.0.5+3 (abc1234)` for work three commits past a release.
Update with `git pull` in the repo; new Claude sessions pick it up.

## v0.0.8 — 2026-11-13
- `imgwatch-plugin` (`status` / `verify APP` / `install`): installs the iTerm2 Browser Plugin for you —
  downloads the official versioned zip (iterm2.com, GitHub mirror as fallback) and installs into
  /Applications only if bundle id, signature, Team ID (George Nachman) and notarization check out.
  `setup.sh` offers it (the iTerm2 restart is the only manual part left); the skill uses it; the
  doctor's FAIL message names it; `install.sh` links it into `~/bin`.
- `imgwatch-plugin move` / `setup.sh`: a plugin left in `~/Downloads` is moved into /Applications (a Downloads
  cleanup would otherwise silently break browser panes); docs say /Applications for the manual route.

## v0.0.7 — 2026-11-13
- Doctor asks iTerm2 (Python API) which profiles it actually loaded. A Browser profile that exists on
  disk but isn't loaded is now a FAIL pointing at the **iTerm2 Browser Plugin**: without the plugin
  iTerm2 silently drops Browser profiles and splits fail with `INVALID_PROFILE_NAME`.
- README, CLAUDE.md, `setup.sh` and the `setup-imgwatch` skill include installing the plugin.
- Tests: the doctor's live check is stubbed (`IMGWATCH_DOCTOR_LIVE` / `IMGWATCH_DOCTOR_OFFLINE`); two new checks.

## v0.0.6 — 2026-11-13
- HEIC/HEIF stills (iPhone photos), converted to JPEG with `sips`.
- No pane for sessions inside `screen`/`tmux` (detached `--remote-control` runs).
- `install.sh` uses an explicit `IMGWATCH_PYTHON` when writing a new config (fixes `tests/run.sh` testing a
  different Python than the one asked for on machines without Anaconda).

## v0.0.5 — 2026-11-13
- **Guided setup**: `./setup.sh` walks through iTerm2, choosing a Python, optional extras (offered
  one by one, installed only on a yes), the install itself, then the two manual steps (iTerm2 Python
  API, macOS Automation prompt) with exact clicks and a check that each worked, a health check and
  a live demo that asks what you see. `./setup.sh --demo` repeats the demo.
- `setup-imgwatch` Claude skill: the same walkthrough, driven by Claude.
- Versioning: release tags, version in the banner and `-V`, this changelog.

## v0.0.4 — 2026-11-13
- The hook runs for every tool: media files a Bash command or MCP tool mentions (e.g. a script
  printing `saved figure to …`, a PyMOL `png` via an MCP server) are shown if just written; paths
  with spaces work. `install.sh` widens older `Read|Write`-only hooks; the doctor warns about them.
- Compounds: `.smi` lists and 2D / multi-molecule SDF/MOL as an RDKit grid (dark style).
- PyMOL sessions (`.pse`): ray-traced snapshot of the saved view via headless PyMOL.
- Office/iWork files: first slide/page via Quick Look.
- Fix: the Quick Look padding trim now only applies to SVGs, so PDF/Office page margins are kept.

## v0.0.3 — 2026-11-13
- `tests/run.sh`: headless test suite against a throwaway HOME (hook, installer, doctor, pruning,
  every renderer).

## v0.0.2 — 2026-11-13
- PDFs (first page, page count), structures (`.pdb/.cif/.bcif/.sdf/.mol/.mol2`) in a Mol* viewer,
  contact sheets for bursts, FSEvents instead of polling, clickable headers, version in the banner.
- `imgwatch --doctor`; `install.sh` creates the `imgbrowser` Browser profile and runs the doctor.
- Housekeeping: cache pruned by age/size, queue trimmed, browser pane closed with its session.
- Fixes: one browser pane per session, never reused or closed across tabs; iTerm2 API errors no
  longer kill the watcher.
- Experimental, untested: WezTerm and kitty/Ghostty.

## v0.0.1 — 2026-11-13
- First public version: a per-session iTerm2 pane for Claude Code showing images (inline), SVG,
  video (animated-GIF preview) and HTML (iTerm2 browser pane, headless screenshot or link);
  `install.sh` with backups and a settings.json merge.

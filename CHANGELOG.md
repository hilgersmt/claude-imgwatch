# Changelog

Versions are git tags (`vMAJOR.MINOR.PATCH`); `imgwatch -V` and the pane banner show the tag and
commit, e.g. `v0.0.5 (1a2b3c4)`, or `v0.0.5+3 (abc1234)` for work three commits past a release.
Update with `git pull` in the repo; new Claude sessions pick it up.

## Unreleased
- HEIC/HEIF stills (iPhone photos), converted to JPEG with `sips`.
- No pane for sessions inside `screen`/`tmux` (detached `--remote-control` runs).

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

# claude-imgwatch: notes for Claude

You are probably on a machine that already has an older, hand-installed imgwatch (plain files in
`~/bin` and `~/.claude/hooks`, maybe images-only, maybe a different Python path). Your job is to
**update it to this repo**, not to merge by hand. When you are walking a person through it,
follow the `setup-imgwatch` skill in `.claude/skills/` (step by step, verifying each manual step); the
short version:

1. Inventory what exists: `~/bin/imgwatch`, `~/bin/imgpane`, `~/.claude/hooks/imgpane-autostart.sh`,
   `~/.claude/hooks/imgwatch-enqueue.sh`, the imgwatch/imgpane entries in `~/.claude/settings.json`,
   any running `pgrep -fl imgwatch`. Note anything the old setup does that this repo doesn't, and
   tell the user before replacing it.
2. Check prerequisites against the README's version table (iTerm2 ≥ 3.7 for the browser lane,
   python with `iterm2` / `selenium`, ffmpeg, Chrome). Install missing pieces only with the user's OK.
3. `./install.sh -n`, show the user the plan, then `./install.sh`. It backs up the old files and
   settings.json, creates the `imgbrowser` Browser profile if missing, and ends with
   `imgwatch --doctor`; fix every FAIL and explain the warnings.
4. Two manual steps (ask the user; they do them): iTerm2 > Settings > General > Magic > Enable Python API,
   and install the iTerm2 Browser Plugin (https://iterm2.com/browser-plugin.html) then restart iTerm2. Without the plugin iTerm2
   drops Browser profiles silently; the doctor's live profile check catches it.
5. Run `tests/run.sh` (headless, safe: throwaway HOME) and report the result, then the README's
   in-pane checklist from a **new** Claude session (hooks load at session start).

Rules: never overwrite `~/.claude/settings.json` wholesale (install.sh merges); keep the pgrep
pattern in `hooks/imgpane-autostart.sh` in sync with how `bin/imgwatch` launches Python, and the
extension list in `hooks/imgwatch-enqueue.sh` in sync with `EXTS` (the doctor checks both);
the WezTerm/kitty/Ghostty branches are untested; changes
go into this repo (commit), not into the symlinked copies.

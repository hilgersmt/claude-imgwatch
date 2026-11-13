# claude-imgwatch: notes for Claude

You are probably on a machine that already has an older, hand-installed imgwatch (plain files in
`~/bin` and `~/.claude/hooks`, maybe images-only, maybe a different Python path). Your job is to
**update it to this repo**, not to merge by hand:

1. Inventory what exists: `~/bin/imgwatch`, `~/bin/imgpane`, `~/.claude/hooks/imgpane-autostart.sh`,
   `~/.claude/hooks/imgwatch-enqueue.sh`, the imgwatch/imgpane entries in `~/.claude/settings.json`,
   any running `pgrep -fl imgwatch`. Note anything the old setup does that this repo doesn't, and
   tell the user before replacing it.
2. Check prerequisites against the README's version table (iTerm2 ≥ 3.7 for the browser lane,
   python with `iterm2` / `selenium`, ffmpeg, Chrome). Install missing pieces only with the user's OK.
3. `./install.sh -n`, show the user the plan, then `./install.sh`. It backs up the old files and
   settings.json; check `~/.config/imgwatch/config` points at the right Python.
4. iTerm2 manual steps (Python API, `imgbrowser` Browser profile): ask the user to do them in the GUI.
5. Run the README test checklist from a **new** Claude session (hooks load at session start).

Rules: never overwrite `~/.claude/settings.json` wholesale (install.sh merges); keep the pgrep
pattern in `hooks/imgpane-autostart.sh` in sync with how `bin/imgwatch` launches Python; changes
go into this repo (commit), not into the symlinked copies.

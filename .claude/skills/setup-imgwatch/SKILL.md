---
name: setup-imgwatch
description: Walk the user through installing, updating or repairing claude-imgwatch (the live media pane for Claude Code in iTerm2) step by step, including the two manual iTerm2/macOS steps, verifying each step before moving on. Use when the user asks to install, set up, update, fix or check imgwatch / the image pane, or opens this repo for the first time.
---

# Guided imgwatch setup

You are walking a person through setup. Go one step at a time, say in one or two plain sentences
what the step does and why, do it (or have them do it), **verify it**, then move on. Never install
anything without asking. `./setup.sh` is the same walkthrough for people running it themselves;
it is interactive, so do not run it yourself (except `./setup.sh --demo`, see step 8 note).

Paths below are relative to this repo. Run everything from the repo root.

## 0. Look first
- Already installed? `ls -l ~/bin/imgwatch ~/bin/imgpane ~/.claude/hooks/imgpane-autostart.sh ~/.claude/hooks/imgwatch-enqueue.sh`,
  the imgwatch entries in `~/.claude/settings.json`, `pgrep -fl imgwatch`.
- If an older hand-made setup exists, tell the user what it does that this repo doesn't (if anything)
  before replacing it. `install.sh` backs everything up to `~/bin/backups/imgwatch_pre-repo_<stamp>/`.

## 1. iTerm2
`/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' /Applications/iTerm.app/Contents/Info.plist`
- Missing: offer `brew install --cask iterm2` (if brew exists) or point to https://iterm2.com.
- Older than 3.7: images work, interactive web pages need 3.7+ (iTerm2 > Check for Updates).
- `echo $TERM_PROGRAM` should be `iTerm.app`; if not, say the pane only works when Claude runs in iTerm2.

## 2. Python
List candidates (`/opt/anaconda3/bin/python`, `~/miniconda3/bin/python`, `~/miniforge3/bin/python`,
`/opt/homebrew/bin/python3`, `/usr/local/bin/python3`, `command -v python3`) with which of
`iterm2 watchdog PIL rdkit pymol selenium` each can import. Ask which to use with AskUserQuestion
(recommend the one with the most). Write it to `~/.config/imgwatch/config` as
`IMGWATCH_PYTHON=<path>` (start from `config.example` if the file doesn't exist).

## 3. Optional extras (ask once, as a multi-select)
| module | pip name | what it enables |
|---|---|---|
| iterm2 | iterm2 | live web pages and Mol* structures in a browser pane |
| watchdog | watchdog | instant file events instead of rescanning every second |
| PIL | pillow | contact sheets for bursts |
| rdkit | rdkit | SMILES / 2D SDF molecule grids |
| selenium | selenium | page screenshots when there is no browser pane |
| pymol | conda: `pymol-open-source` (conda-forge) | PyMOL session snapshots |
Plus ffmpeg (`brew install ffmpeg`, videos) and Google Chrome (screenshot fallback only).
Install with `<python> -m pip install <name>`. Anaconda gotchas: if pip fails on certifi, retry
with `--ignore-installed certifi`; if streamlit is installed and iterm2 pulled protobuf ≥ 7,
offer `pip install 'protobuf<7'`.

## 4. Install
`./install.sh -n`, show the user the plan in a few lines, ask, then `./install.sh`. It links the
scripts, merges two hooks into `~/.claude/settings.json` (backup first), creates the `imgbrowser`
Browser profile if missing, and runs the doctor.

## 5. Manual: iTerm2 Python API (skip if the iterm2 module isn't installed)
Check: `<python> -c 'import iterm2
async def m(c): await iterm2.async_get_app(c)
iterm2.run_until_complete(m)' && echo ok`
If it fails, give these exact steps and wait for the user to say done, then re-check:
1. Click into iTerm2 and press **⌘ ,** (Command + comma) to open Settings.
2. Choose **General** at the top, then the **Magic** sub-tab.
3. Tick **Enable Python API**; iTerm2 asks to confirm — click **Accept**.
4. Close Settings.
If it still fails after they say it's done, ask them to confirm the box is ticked; offer to skip
(web pages then fall back to screenshots or links).

## 5b. Manual: iTerm2 Browser Plugin (skip if the iterm2 module isn't installed)
iTerm2's browser panes need a separate plugin; without it iTerm2 silently ignores Browser-type
profiles. Check: `~/bin/imgwatch --doctor | grep profile` should say "loaded by iTerm2". If it says
"install the iTerm2 Browser Plugin", ask the user to (it is a download they do themselves):
1. Open https://iterm2.com/browser-plugin.html, download the plugin and install it as that page says.
2. Quit and reopen iTerm2.
Then re-check. If it still isn't loaded: iTerm2 > Settings > Advanced > "Enable browser-style
profiles" must be Yes (the default).

## 6. Manual: Automation permission
Check: `osascript -e 'tell application "iTerm2" to get name of current window'`.
Before running it, warn: macOS may show "iTerm2 wants access to control iTerm2" — click **Allow**.
If it errors with "not allowed": System Settings → Privacy & Security → Automation → under iTerm2,
switch on **iTerm2**. Re-check.

## 7. Health check
`~/bin/imgwatch --doctor`. Explain every `warn` in one line; fix every `FAIL` before continuing.
Optionally `tests/run.sh` (about a minute, safe: throwaway HOME) and report the pass/fail count.

## 8. Live demo
Ask first. Then, from your Bash tool (it runs in the user's iTerm2 pane, so the split appears there):
```bash
demo=$(mktemp -d /tmp/imgwatch-demo.XXXX); sleep 600 </dev/null & own=$!; disown $own
(cd "$demo" && ~/bin/imgpane --owner $own "$demo" </dev/null)
```
Then write a PNG into `$demo` (any small plot), ask with AskUserQuestion whether it appeared in
the new right-hand pane; then a small HTML page with something visibly animated, ask whether it
appeared in a browser pane below; then `kill $own` and ask whether the demo panes closed. On a
"no": pane missing → step 6; pane there but no browser pane → step 5 and
`/Applications/iTerm.app/Contents/Resources/utilities/it2 profile list` (needs `imgbrowser`).
(People running setup themselves can do the same with `./setup.sh --demo`.)

## 9. Wrap up
Hooks load when a session starts: tell the user to start a **new** `claude` session in iTerm2,
where the pane opens by itself. Mention `CLAUDE_NO_IMGPANE=1 claude` (skip once),
`imgwatch --doctor`, and `git pull` in the repo to update.

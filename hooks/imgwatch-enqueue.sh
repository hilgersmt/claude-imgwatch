#!/bin/bash
# PostToolUse hook (Read|Write): append "session_id<TAB>media path" to the imgwatch queue.
# Stills, videos (capped animated-GIF preview), PDFs (first page), HTML (browser pane / headless
# screenshot / link, see imgwatch --html-mode) and structures (Mol* page). Keep this list in sync
# with EXTS in bin/imgwatch.py; `imgwatch --doctor` checks it.
[ -f ~/.config/imgwatch/config ] && . ~/.config/imgwatch/config
line=$("${IMGWATCH_PYTHON:-python3}" -c '
import json, sys
d = json.load(sys.stdin); f = d.get("tool_input", {}).get("file_path", "")
if f.lower().endswith((".png",".jpg",".jpeg",".gif",".webp",".svg",".tif",".tiff",".bmp",
                       ".mp4",".mov",".m4v",".webm",".avi",".mkv",".mpg",".mpeg",
                       ".html",".htm",".pdf",
                       ".pdb",".ent",".cif",".mmcif",".bcif",".sdf",".mol",".mol2")):
    print(d.get("session_id", "") + "\t" + f)' 2>/dev/null)
[ -n "$line" ] && { mkdir -p ~/.claude/imgwatch; printf '%s\n' "$line" >> ~/.claude/imgwatch/queue; }
exit 0

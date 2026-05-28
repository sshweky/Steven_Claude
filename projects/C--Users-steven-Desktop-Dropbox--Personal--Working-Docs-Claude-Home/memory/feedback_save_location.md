---
name: feedback-save-location
description: "Where to save generated files -- Claude Code home is C:\\Users\\steven\\.claude, NOT Dropbox"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b620c133-5940-4d1e-8185-1a0b4c3e2bed
---

Never save generated project files, scripts, or HTML codepages to the Dropbox working docs folder (C:\Users\steven\Desktop\Dropbox (Personal)\Working Docs\Claude Home).

The correct Claude Code home directory is: `C:\Users\steven\.claude`

Save all generated outputs (scripts, HTML, configs, etc.) to `C:\Users\steven\.claude` or an appropriate subdirectory within it.

**Why:** User explicitly corrected this -- Dropbox is not the intended destination for code outputs. The `.claude` folder is synced to GitHub (repo: sshweky/Steven_Claude) and is the canonical home for all Claude Code work.

**How to apply:** Before writing any output file, default to `C:\Users\steven\.claude\` as the root. Ask the user if a specific subdirectory is preferred for a given file type.

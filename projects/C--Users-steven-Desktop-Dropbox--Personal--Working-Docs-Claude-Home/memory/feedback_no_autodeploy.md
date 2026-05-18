---
name: Never run deploy_pages.py without explicit user instruction
description: Do not run deploy_pages.py (or any QB code page deploy) unless the user explicitly asks to deploy
type: feedback
originSessionId: 192e4f01-3664-463b-bda7-b157c0280869
---
Never run `deploy_pages.py` unless the user explicitly says to deploy. Running it overwrites the live QB code pages immediately, which erases the current production version. The user has to manually restore from backup when this happens.

**Why:** Deploying to QB code pages is a destructive, irreversible action that affects the live tool all planners use. The user was burned when I auto-deployed after making edits.

**How to apply:** After editing viewer.js or viewer.html in the `.claude\skills\inventory-forecaster-cc\codepage\` directory, stop. Tell the user what was changed and wait for them to say "deploy" or "run deploy" before touching deploy_pages.py.

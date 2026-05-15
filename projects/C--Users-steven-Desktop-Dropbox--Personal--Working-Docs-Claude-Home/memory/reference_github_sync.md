---
name: GitHub Sync — Steven_Claude repo
description: GitHub repo and local git setup for syncing Claude skills, projects, and plugins
type: reference
originSessionId: c3fe6254-8a82-4f01-8dca-04c7f0a1ba8a
---
The Claude home directory `C:\Users\steven\.claude` is a git repo tracking `https://github.com/sshweky/Steven_Claude`.

**What is synced:**
- `skills/` — all Claude skills (inventory-forecaster-cc, customer-deep-dives)
- `projects/` — all Claude projects (amazon-trend-analyzer, Claude Home project)
- `plugins/` — installed plugins and marketplace

**What is excluded (gitignore):**
- `settings.json` — contains live CData MCP auth token
- `.credentials.json` — contains Anthropic OAuth access/refresh tokens and MCP client secrets
- `cache/` — contains files >100 MB (GitHub hard limit)
- `sessions/`, `*.jsonl` — conversation session data
- `history.jsonl`, `telemetry/`, `debug/`, `shell-snapshots/`

**To sync changes to GitHub:**
```
git -C "C:\Users\steven\.claude" add -A
git -C "C:\Users\steven\.claude" commit -m "your message"
git -C "C:\Users\steven\.claude" push
```

**To pull updates from GitHub:**
```
git -C "C:\Users\steven\.claude" pull
```

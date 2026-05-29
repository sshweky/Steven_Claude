# Memory Index

## Feedback
- [feedback_save_location.md](feedback_save_location.md) — Save generated files to C:\Users\steven\.claude, NOT Dropbox -- that is the Claude Code home for all sessions
- [feedback_forecasting_methodology.md](feedback_forecasting_methodology.md) — Forecasting model tuning: HW trend collapse, Croston's over-projection, baseline should use all-weeks avg, viewer preferences, narrative style
- [feedback_no_unicode_chars.md](feedback_no_unicode_chars.md) — Never use em-dashes, replacement chars, ellipsis, or any non-ASCII Unicode anywhere -- causes diamond-question-mark in browser
- [feedback_no_autodeploy.md](feedback_no_autodeploy.md) — Never run deploy_pages.py without explicit user instruction — it overwrites live QB pages immediately
- [feedback_no_local_viewer.md](feedback_no_local_viewer.md) — Never launch the local viewer (viewer.py / http://127.0.0.1:8765) — user only uses the QB codepage viewer

## References
- [reference_github_sync.md](reference_github_sync.md) — GitHub repo (sshweky/Steven_Claude) syncs C:\Users\steven\.claude — git commands, what's included/excluded
- [reference_quickbase.md](reference_quickbase.md) — Quickbase API credentials and full table index for Amazon AdTrack app (pim.quickbase.com, app ID: bqkdiemav)
- [reference_inv_history_weekly.md](reference_inv_history_weekly.md) — Inventory History - Weekly schema: relationship 35, summary field FIDs (64–116), date formula fields (68–119) in History
- [reference_quickbase_api_rules.md](reference_quickbase_api_rules.md) — Universal rule book for accessing QB without throttling: bulk write patterns, retry/backoff policy, anti-patterns, pre-flight checklist. Apply to any chat/script/skill that touches Quickbase.
- [reference_cdata_mcp.md](reference_cdata_mcp.md) — CData MCP auth (Basic email:PAT), response format, and inventory forecaster setup notes
- [reference_contacts.md](reference_contacts.md) — Key internal contacts: Mikey Scott (Director of Inventory Management), Nancy Lee (VP Supply Chain)
- [reference_inv_mgmt_codepages.md](reference_inv_mgmt_codepages.md) — Production inv mgmt viewer files: inv_mgmt_full.html (pg 52) + inv_mgmt.js (pg 56); NOT viewer.html/viewer.js; deploy API broken as of 2026-05-18

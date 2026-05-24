# archive/

Historical artifacts from earlier development cycles. Each subdir is dated.

These files are NOT loaded by the live forecaster. They're kept for forensic
reference (e.g. "what did the forecast look like before F47 was added?").

## Conventions

- New cycle? Create `archive/YYYY-MM-DD/` and move stale snapshots there.
- Keep ONLY the most recent of each artifact in the skill root:
  - One `forecast_results.json` + `forecast_results.completed.json`
  - One `validation_results.json`
  - One `forecast_report.html`
  - One `all_writeback_*.log` (most recent successful run)
  - One `all_dryrun.log` (most recent dry run)
- Anything `*.before_*.json`, `*.v[0-9]+.{log,json}`, `fr_*.json`, or
  one-off `analyze_*.py` scripts belongs here, not at the skill root.

## 2026-05-23 sweep

First systematic archive sweep (deep audit Phase 1). Moved ~161 MB of stale
backtest logs, snapshot JSONs, one-off analysis scripts, and historical
writeback logs from skill root.

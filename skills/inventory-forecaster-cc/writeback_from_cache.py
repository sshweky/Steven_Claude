"""
One-shot: push the existing forecast_results.json to QB via the bulk REST path.
Reuses the patched qb_bulk_update() helper. Skips all Phase 1-3 work.
"""
import json, sys, time
sys.path.insert(0, "scripts")
from inventory_forecaster import (
    qb_get_field_map, qb_bulk_update, QB_PROJ_TABLE, QB_BULK_BATCH
)

def main():
    print(f"[1/4] Loading forecast_results.json ...", flush=True)
    with open("forecast_results.json") as f:
        results = json.load(f)
    records = results.get("records", results) if isinstance(results, dict) else results
    print(f"      {len(records):,} records loaded", flush=True)

    print(f"[2/4] Fetching QB field map for {QB_PROJ_TABLE} ...", flush=True)
    fmap = qb_get_field_map(QB_PROJ_TABLE)
    if not fmap:
        sys.exit("[ABORT] field map empty")
    merge_fid    = fmap.get("Acct# - MStyle (Key)")
    ai_alert_fid = fmap.get("AI ALERT")
    wk_fids      = [fmap.get(f"AI PRJ W{i}") for i in range(1, 27)]
    if not (merge_fid and ai_alert_fid and all(wk_fids)):
        sys.exit(f"[ABORT] missing fids: merge={merge_fid} alert={ai_alert_fid} wks_ok={all(wk_fids)}")
    print(f"      merge_fid={merge_fid}, alert_fid={ai_alert_fid}, "
          f"AI PRJ W1-26 = {wk_fids[0]}-{wk_fids[-1]}", flush=True)

    print(f"[3/4] Composing bulk payload ({len(records):,} records) ...", flush=True)
    payload = []
    for rec in records:
        row = {merge_fid: rec["key"], ai_alert_fid: rec.get("alert", "")}
        fcst = rec.get("forecast") or [0] * 26
        for i, fid in enumerate(wk_fids):
            row[fid] = int(round(fcst[i])) if i < len(fcst) else 0
        payload.append(row)
    print(f"      payload ready ({len(payload):,} records, "
          f"~{len(payload)/QB_BULK_BATCH + 0.999:.0f} batches of {QB_BULK_BATCH})", flush=True)

    print(f"[4/4] Bulk POST /v1/records ...", flush=True)
    t0 = time.time()
    n_ok, n_fail, errors = qb_bulk_update(QB_PROJ_TABLE, payload, merge_fid)
    elapsed = time.time() - t0
    print(f"      {n_ok:,} OK · {n_fail:,} failed · {elapsed:.1f}s "
          f"({n_ok/max(elapsed,0.01):.0f} rec/s)", flush=True)
    if errors:
        with open("forecast_results.bulk_errors.json", "w") as f:
            json.dump(errors, f, indent=2)
        print(f"      Errors → forecast_results.bulk_errors.json", flush=True)

if __name__ == "__main__":
    main()

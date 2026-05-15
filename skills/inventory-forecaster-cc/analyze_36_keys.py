"""
Analyze 36 flagged keys — history + manual + AI forecast.
Pulls Projections data for the 36 keys, computes L4/L13/L26/L52 stats and
order cadence, and writes a structured report.
"""
import sys, json
sys.path.insert(0, 'scripts')
from inventory_forecaster import cdata_query

KEYS = [
    "12446-BB0234CAN","12446-BB0761CAN","12835-BB12022/12","12835-FF33094",
    "13640-FF10162","16553-FF8990","16553-SF8168PS","16553-SF8169PS",
    "16553-SF8170PS","1864-BB0466","1864-BB13437","1864-BB28719",
    "1864-BB31552","1864-BB35097","1864-FF10479EC","1864-FF12376",
    "1864-FF12508","1864-FF12689","1864-FF12842","1864-FF12843",
    "1864-FF15584","1864-FF22031","1864-FF31068","1864-FF35147",
    "1864-FF7372","1864-FF7619","20006-FF19341","23011-FF12659",
    "23011-FF12660","23011-FF15592","23011-FF19998","23011-FF38640",
    "23011-FF38641","23011-FF7112","23011-FF8882/2","3102-FF12846",
]

# Map prj columns
prj_cols = ['04_19_W1','04_26_W2','05_03_W3','05_10_W4','05_17_W5','05_24_W6',
            '05_31_W7','06_07_W8','06_14_W9','06_21_W10','06_28_W11','07_05_W12',
            '07_12_W13','07_19_W14','07_26_W15','08_02_W16','08_09_W17','08_16_W18',
            '08_23_W19','08_30_W20','09_06_W21','09_13_W22','09_20_W23','09_27_W24',
            '10_04_W25','10_11_W26']

ord_cols = ['Ord_LW'] + [f'Ord_LW_{i}' for i in range(1,52)]

hist_cols_sql = ",".join(f"[{c}]" for c in ord_cols)
prj_cols_sql  = ",".join(f"[{c}]" for c in prj_cols)
in_keys       = ",".join(f"'{k}'" for k in KEYS)

sql = (
    f"SELECT [Acct_MStyle_Key_],[Cust_Name],[Description],[Status_Cust],"
    f"[Mstyle],[Master_Pack] AS MP_placeholder,"
    f"{hist_cols_sql},{prj_cols_sql} "
    f"FROM [Quickbase1].[InventoryTrack].[Projections] "
    f"WHERE [Acct_MStyle_Key_] IN ({in_keys})"
)

print(f"Pulling {len(KEYS)} keys...")
rows = cdata_query(sql, "36 key analysis")
print(f"  got {len(rows)} rows")

def to_f(v):
    try: return float(v or 0)
    except: return 0.0

results = []
for r in rows:
    key = r.get("Acct_MStyle_Key_")
    cust = (r.get("Cust_Name") or "").split("<")[0][:40]  # strip html
    desc = (r.get("Description") or "")[:50]
    # history (oldest -> newest: W-51 … W-1, then W-0 = Ord_LW)
    hist_cols_order = ['Ord_LW_51'] + [f'Ord_LW_{i}' for i in range(50,0,-1)] + ['Ord_LW']
    hist = [to_f(r.get(c)) for c in hist_cols_order]
    manual = [to_f(r.get(c)) for c in prj_cols]

    L4, L13, L26, L52 = hist[-4:], hist[-13:], hist[-26:], hist
    nz13 = [v for v in L13 if v>0]
    nz26 = [v for v in L26 if v>0]
    nz52 = [v for v in L52 if v>0]

    # cadence on L52
    nz_idx = [i for i,v in enumerate(hist) if v>0]
    gaps = [nz_idx[i+1]-nz_idx[i] for i in range(len(nz_idx)-1)] if len(nz_idx)>=2 else []
    median_gap = sorted(gaps)[len(gaps)//2] if gaps else 0

    # manual patterns
    manual_nz = [(i,v) for i,v in enumerate(manual) if v>0]
    manual_total = sum(manual)
    manual_order_sizes = [v for _,v in manual_nz]

    results.append({
        "key": key, "cust": cust, "desc": desc,
        "status": r.get("Status_Cust",""),
        "L4_sum": sum(L4), "L4_avg": sum(L4)/4,
        "L13_sum": sum(L13), "L13_avg": sum(L13)/13,
        "L13_nz_avg": sum(nz13)/len(nz13) if nz13 else 0,
        "L13_nz_count": len(nz13),
        "L13_zero_count": 13 - len(nz13),
        "L26_sum": sum(L26), "L26_avg": sum(L26)/26,
        "L26_nz_count": len(nz26),
        "L26_nz_rate": len(nz26)/26,
        "L52_sum": sum(L52), "L52_avg": sum(L52)/52,
        "L52_nz_count": len(nz52),
        "median_gap_wks": median_gap,
        "avg_gap_wks": sum(gaps)/len(gaps) if gaps else 0,
        "manual_total": manual_total,
        "manual_nz_count": len(manual_nz),
        "manual_order_weeks": [i+1 for i,_ in manual_nz],
        "manual_order_sizes": manual_order_sizes,
        "manual_avg_order": (sum(manual_order_sizes)/len(manual_order_sizes)) if manual_order_sizes else 0,
    })

# Add AI forecast from current fr_all_t1_t4.json
import os
if os.path.exists("fr_all_t1_t4.json"):
    fc = json.load(open("fr_all_t1_t4.json"))
    by_key = {r["key"]:r for r in fc.get("records",[])}
    for res in results:
        ai = by_key.get(res["key"])
        if ai:
            res["ai_total"]    = ai.get("new_total",0)
            res["ai_model"]    = ai.get("model","")
            res["ai_pct_diff"] = ai.get("pct_diff",0)

with open("analysis_36_keys.json","w") as f:
    json.dump(results, f, indent=2)
print(f"Wrote analysis_36_keys.json ({len(results)} records)")

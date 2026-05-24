import sys
sys.path.insert(0, '.')
import inventory_forecaster as f
from datetime import date, timedelta

f.ORIG_PRJ_COLS = ['05_17_W1','05_24_W2','05_31_W3','06_07_W4','06_14_W5','06_21_W6',
                   '06_28_W7','07_05_W8','07_12_W9','07_19_W10','07_26_W11','08_02_W12',
                   '08_09_W13','08_16_W14','08_23_W15','08_30_W16','09_06_W17','09_13_W18',
                   '09_20_W19','09_27_W20','10_04_W21','10_11_W22','10_18_W23','10_25_W24',
                   '11_01_W25','11_08_W26']

W1 = date(2026, 5, 17)

for tag in ['', 'Holiday', 'Fall/Winter', 'Halloween', 'July 4th', 'Easter', 'Spring/Summer']:
    boosts = f._get_t5_seasonal_boosts(tag)
    label = tag if tag else '(standard)'
    if not boosts:
        print(f"Season '{label}': no boosts")
    else:
        print(f"Season '{label}':")
        for wk, mult in sorted(boosts.items()):
            dt = W1 + timedelta(weeks=wk-1)
            print(f"  W{wk:2d} ({dt.strftime('%b %d')}): {mult:.2f}x")

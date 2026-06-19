#!/usr/bin/env python3
"""Internal: fast random-search weight tuner for Option A."""
import os, sys, time, random, json
from copy import deepcopy
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
for m in list(sys.modules):
    if m.startswith("eval_weights") or m.startswith("predict") or m.startswith("config"):
        sys.modules.pop(m, None)

from eval_weights import (
    build_stats, build_races, build_history_cache, is_classic_race,
    load_meeting_files, normalize_weights,
)
from config import WEIGHTS_GENERAL, WEIGHTS_CLASSIC
from predict import score_field

def load_actual_top4_v2(p):
    df = pd.read_excel(p, sheet_name="Results")
    out = {}
    for race_no, group in df.groupby("race_no"):
        tops = group.sort_values("pos").head(4)["horse_no"].tolist()
        out[int(race_no)] = [str(int(float(x))) if str(x).strip() not in ("", "nan") else "" for x in tops]
        out[int(race_no)] = [x for x in out[int(race_no)] if x]
    return out

# Use only the 11 meetings that ALSO have stored predictions (apples-to-apples
# with the historical 48.85% baseline from model_accuracy_report.py).
all_meetings = load_meeting_files(24)
pred_dir = ROOT / "data" / "predictions"
meetings = [m for m in all_meetings if (pred_dir / f"{m[0].stem}.xlsx").exists()]
print(f"Using {len(meetings)} meetings (those with stored predictions)")

print("Pre-caching…")
t0 = time.time()
meeting_data = []
for raw_path, results_path in meetings:
    jky, trn = build_stats(raw_path)
    races = build_races(raw_path)
    actual = load_actual_top4_v2(results_path)
    hids = {h["horse_id"] for r in races for h in r["horses"] if h.get("horse_id")}
    hist = build_history_cache(hids)
    meeting_data.append({
        "venue": raw_path.stem.split("_")[-1],
        "jky": jky, "trn": trn, "races": races, "actual": actual, "hist": hist,
    })
print(f"  cached in {time.time()-t0:.1f}s")

def fast_evaluate(weights):
    hits = picks = 0
    for md in meeting_data:
        for race in md["races"]:
            actual = md["actual"].get(int(race["race_no"]), [])
            if not actual:
                continue
            w = WEIGHTS_CLASSIC if is_classic_race(race.get("race_name", "")) else weights
            scored = score_field(race, md["venue"], md["jky"], md["trn"], md["hist"], w)
            if scored.empty:
                continue
            pred = scored.head(4)["horse_no"].astype(str).tolist()
            hits += sum(1 for p in pred if p in actual)
            picks += 4
    return hits / picks * 100 if picks else 0

KEYS = ["form","rating","market","draw","jockey","trainer","h2h","weight"]

def perturb(w, scale=0.15):
    nw = deepcopy(w)
    n = random.randint(1, max(1, len(KEYS)//2))
    for _ in range(n):
        k = random.choice(KEYS)
        delta = random.gauss(0, scale) * max(nw.get(k, 0.1), 0.05)
        nw[k] = max(0.0, nw.get(k, 0.0) + delta)
    s = sum(nw.values()) or 1
    return {k: v/s for k, v in nw.items()}

classic = normalize_weights(WEIGHTS_CLASSIC)
base    = normalize_weights(WEIGHTS_GENERAL)
random.seed(20260619)

t0 = time.time()
base_hr = fast_evaluate(base)
print(f"\nBaseline (current weights):  {base_hr:.2f}%   ({time.time()-t0:.1f}s)")

# Run search. Each iter takes ~0.5-1s with cached stats. Aim for 1500 iters.
N_ITER = 400
best_w   = base
best_hr  = base_hr
hist     = [(0, base_hr)]
print(f"\nSearching {N_ITER} random perturbations…")
t_start = time.time()
for i in range(1, N_ITER + 1):
    cand = perturb(best_w)
    hr = fast_evaluate(cand)
    if hr > best_hr:
        best_hr, best_w = hr, cand
        hist.append((i, hr))
        print(f"  iter {i:5d}: NEW BEST {hr:.2f}%  weights={best_w}")
    if i % 100 == 0:
        elapsed = time.time() - t_start
        print(f"  ... iter {i}/{N_ITER}  elapsed {elapsed:.0f}s  best so far {best_hr:.2f}%", flush=True)

elapsed = time.time() - t_start
print(f"\nDone in {elapsed:.0f}s")
print(f"Best hit rate: {best_hr:.2f}%   (delta {best_hr - base_hr:+.2f}pp)")
print(f"Best weights:  {best_w}")

# Save
out = {
    "WEIGHTS_GENERAL": best_w,
    "WEIGHTS_CLASSIC": WEIGHTS_CLASSIC,
    "TARGET_HIT_RATE": 75.0,
    "baseline_hit_rate": round(base_hr, 4),
    "best_hit_rate":    round(best_hr, 4),
    "delta_pp":         round(best_hr - base_hr, 4),
    "n_iter":           N_ITER,
    "improvements":     [{"iter": i, "hr": hr} for i, hr in hist[1:]],
}
Path("data").mkdir(exist_ok=True)
Path("data/weight_tuning_result.json").write_text(json.dumps(out, indent=2))
print(f"\nSaved → data/weight_tuning_result.json")

if best_hr > base_hr:
    print(f"\n✓ IMPROVEMENT FOUND. Run `python -c \"import json; print(json.dumps(out))\"` to inspect.")
    print(f"  To save to weights.json (overwrite!), confirm explicitly.")
else:
    print(f"\n✗ No improvement; weights unchanged.")

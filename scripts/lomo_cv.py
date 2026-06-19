#!/usr/bin/env python3
"""LOMO CV for Option A — small enough to run in ~5 min."""
import os, sys, time, random, json
from copy import deepcopy
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
for m in list(sys.modules):
    if m.startswith("eval_weights") or m.startswith("predict") or m.startswith("config"):
        sys.modules.pop(m, None)

import pandas as pd
from eval_weights import (
    build_stats, build_races, build_history_cache, is_classic_race, load_meeting_files,
    normalize_weights,
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

all_meetings = load_meeting_files(24)
pred_dir = ROOT / "data" / "predictions"
meetings = [m for m in all_meetings if (pred_dir / f"{m[0].stem}.xlsx").exists()]
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

print("Pre-caching…", flush=True)
t0 = time.time()
cache = {}
for raw_path, results_path in meetings:
    jky, trn = build_stats(raw_path)
    races = build_races(raw_path)
    actual = load_actual_top4_v2(results_path)
    hids = {h["horse_id"] for r in races for h in r["horses"] if h.get("horse_id")}
    hist = build_history_cache(hids)
    cache[raw_path.stem] = {
        "venue": raw_path.stem.split("_")[-1],
        "jky": jky, "trn": trn, "races": races, "actual": actual, "hist": hist,
    }
print(f"  cached {len(cache)} meetings in {time.time()-t0:.1f}s", flush=True)

def evaluate_on(tags, weights):
    hits = picks = 0
    for tag in tags:
        md = cache[tag]
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

all_tags = sorted(cache.keys())
N_ITER = 100
random.seed(42)
results = []
print(f"\nLOMO CV: {len(all_tags)} folds, {N_ITER} iters per fold", flush=True)
print(f"{'holdout':<18}{'base':>8}{'tuned':>9}{'delta':>10}", flush=True)
fold_times = []

for holdout in all_tags:
    t_fold = time.time()
    train_tags = [t for t in all_tags if t != holdout]
    base_w = normalize_weights(WEIGHTS_GENERAL)
    base_hr_h = evaluate_on([holdout], base_w)
    base_hr_t = evaluate_on(train_tags, base_w)
    best_w, best_hr = base_w, base_hr_t
    for i in range(N_ITER):
        cand = perturb(best_w)
        hr = evaluate_on(train_tags, cand)
        if hr > best_hr:
            best_hr, best_w = hr, cand
    tuned_hr = evaluate_on([holdout], best_w)
    delta = tuned_hr - base_hr_h
    fold_t = time.time() - t_fold
    fold_times.append(fold_t)
    avg_so_far = sum(fold_times) / len(fold_times)
    eta = avg_so_far * (len(all_tags) - len(results) - 1)
    print(f"{holdout:<18}{base_hr_h:>7.2f}%{tuned_hr:>8.2f}%{delta:>+9.2f}pp  "
          f"({fold_t:.0f}s, ETA {eta:.0f}s)", flush=True)
    results.append({
        "holdout": holdout, "base_hr": base_hr_h, "tuned_hr": tuned_hr,
        "delta": delta, "tuned_weights": best_w,
    })

avg_base  = sum(r["base_hr"]  for r in results) / len(results)
avg_tuned = sum(r["tuned_hr"] for r in results) / len(results)
improved = sum(1 for r in results if r["delta"] > 0)
hurt     = sum(1 for r in results if r["delta"] < 0)
same     = sum(1 for r in results if r["delta"] == 0)
print(f"\n{'AVG':<18}{avg_base:>7.2f}%{avg_tuned:>8.2f}%{avg_tuned-avg_base:>+9.2f}pp", flush=True)
print(f"Fold outcomes: improved={improved}  same={same}  hurt={hurt}", flush=True)

Path("data/lomo_cv_result.json").write_text(json.dumps({
    "folds": results,
    "avg_base": round(avg_base, 4),
    "avg_tuned": round(avg_tuned, 4),
    "improved_folds": improved, "hurt_folds": hurt, "same_folds": same,
    "n_iter_per_fold": N_ITER,
}, indent=2))
print(f"\nSaved → data/lomo_cv_result.json", flush=True)

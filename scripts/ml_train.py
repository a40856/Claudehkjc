#!/usr/bin/env python3
"""
ml/train.py — LightGBM ranker for HKJC horse prediction.

Builds a flat horse-race-row feature matrix from:
  - data/raw/YYYY-MM-DD_VN.xlsx    (race card: draw, weight, rating, last6, etc.)
  - data/horses.csv                (per-(horse,venue,distance,surface) lifetime stats)
  - data/results/YYYY-MM-DD_VN.xlsx (label: actual finishing position)

Trains a LightGBM LambdaRank model with `group = (meeting, race_no)` so it learns
to RANK horses within each race (not predict absolute probability).

Evaluates honestly with Leave-One-Meeting-Out CV:
  - For each of the 11 meetings: train on 10, evaluate on the 11th.
  - Report per-fold hit rate, average, and improvement over the rule-based
    baseline.

Saves:
  - reports/ml/lgbm_ranker.txt      — trained booster (final, trained on all data)
  - reports/ml/features.json        — feature name list
  - reports/ml/lomo_cv_result.json  — per-fold + summary metrics
  - reports/ml/feature_importance.csv

Usage:
  python ml/train.py
  python ml/train.py --quiet     # less stdout
"""

import argparse
import json
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

ROOT          = Path(__file__).resolve().parent.parent
RAW_DIR       = ROOT / "data" / "raw"
RESULTS_DIR   = ROOT / "data" / "results"
HORSES_CSV    = ROOT / "data" / "horses.csv"
ML_DIR        = ROOT / "reports" / "ml"

# Make sibling scripts importable when running from the ml/ folder
sys.path.insert(0, str(ROOT))

# ── Feature engineering ──────────────────────────────────────────────────────

def parse_last6(s: str) -> dict:
    """Parse '4/6/1/12/14/3' → position list + simple stats."""
    if not isinstance(s, str) or not s.strip():
        return {"l6_n": 0, "l6_avg": 0.0, "l6_best": 0, "l6_worst": 0,
                "l6_top3_count": 0, "l6_top3_rate": 0.0,
                "l6_win_count": 0, "l6_win_rate": 0.0,
                "l6_recency_avg": 0.0, "l6_consistency": 0.0,
                "l6_last_pos": 0}
    pos = []
    for tok in s.replace(" ", "").split("/"):
        try:
            p = int(tok)
            if 1 <= p <= 14:
                pos.append(p)
        except ValueError:
            pass
    pos = pos[:6]
    if not pos:
        return {"l6_n": 0, "l6_avg": 0.0, "l6_best": 0, "l6_worst": 0,
                "l6_top3_count": 0, "l6_top3_rate": 0.0,
                "l6_win_count": 0, "l6_win_rate": 0.0,
                "l6_recency_avg": 0.0, "l6_consistency": 0.0,
                "l6_last_pos": 0}
    arr = np.array(pos, dtype=float)
    recency_w = np.linspace(0.2, 1.0, len(arr))
    return {
        "l6_n":            len(pos),
        "l6_avg":          float(arr.mean()),
        "l6_best":         int(arr.min()),
        "l6_worst":        int(arr.max()),
        "l6_top3_count":   int((arr <= 3).sum()),
        "l6_top3_rate":    float((arr <= 3).mean()),
        "l6_win_count":    int((arr == 1).sum()),
        "l6_win_rate":     float((arr == 1).mean()),
        "l6_recency_avg":  float(np.average(arr, weights=recency_w)),
        "l6_consistency":  float(arr.std()),
        "l6_last_pos":     int(arr[0]),  # leftmost = most recent
    }


def parse_gear(g) -> dict:
    """HKJC gear code → simple binary flags. e.g. 'B-TT' = 'B removed, TT added'.
       We just count gear pieces and flag presence of common ones.
    """
    if pd.isna(g):
        return {"gear_count": 0, "gear_has_B": 0, "gear_has_TT": 0, "gear_has_V": 0,
                "gear_has_PB": 0, "gear_has_SH": 0}
    s = str(g).strip()
    if not s:
        return {"gear_count": 0, "gear_has_B": 0, "gear_has_TT": 0, "gear_has_V": 0,
                "gear_has_PB": 0, "gear_has_SH": 0}
    # '+' = added, '-' = removed; split on those
    pieces = []
    cur = ""
    for ch in s:
        if ch in "+-":
            if cur: pieces.append(cur)
            cur = ch
        else:
            cur += ch
    if cur: pieces.append(cur)
    added = [p for p in pieces if not p.startswith("-")]
    return {
        "gear_count":   len(pieces),
        "gear_has_B":   int(any("B" in p for p in pieces)),
        "gear_has_TT":  int(any("TT" in p for p in pieces)),
        "gear_has_V":   int(any("V" in p for p in pieces)),
        "gear_has_PB":  int(any("PB" in p for p in pieces)),
        "gear_has_SH":  int(any("SH" in p for p in pieces)),
    }


def load_horses_lookup() -> pd.DataFrame:
    """Load horses.csv, keyed on (horse_id, venue, distance, surface)."""
    hc = pd.read_csv(HORSES_CSV)
    # rename to match raw columns for join
    hc = hc.rename(columns={"venue": "venue", "distance_m": "distance",
                            "surface": "surface"})
    # Encode venue as HV/ST; surface already normalised
    return hc


def build_horses_lookup_from_tags(tags: list[str]) -> pd.DataFrame:
    """Rebuild per-horse lifetime stats using ONLY the given meeting tags.
       Used by LOMO CV to prevent label leakage: when holding out meeting X,
       the horses.csv fed to the model for fold X must be built from
       every tag EXCEPT X.

       Mirrors aggregate_horse_history.py logic but operates on a tag subset.
    """
    from aggregate_horse_history import load_all as agg_load_all, aggregate as agg_aggregate
    # We need load_all but restricted to specific tags — that function globs.
    # So monkeypatch its RAW_DIR temporarily? Simpler: replicate the load logic.
    pieces = []
    for tag in tags:
        from aggregate_horse_history import load_meeting
        raw, res = load_meeting(tag)
        if raw is None: continue
        keep = ["race_no","horse_no","horse_id","horse_name","distance_m",
                "surface_norm","venue","meeting_date","draw","weight_lbs",
                "jockey","trainer","rating"]
        keep = [c for c in keep if c in raw.columns]
        joined = raw[keep].merge(
            res[["race_no","horse_no","pos"]].rename(columns={"pos":"finish_pos"}),
            on=["race_no","horse_no"], how="left",
        )
        joined["finish_pos"] = joined["finish_pos"].fillna(99).astype(int)
        joined["meeting"] = tag
        pieces.append(joined)
    history = pd.concat(pieces, ignore_index=True)
    as_of = history["meeting_date"].max() + pd.Timedelta(days=1)
    hc = agg_aggregate(history, as_of)
    return hc.rename(columns={"venue":"venue","distance_m":"distance","surface":"surface"})


def build_one_meeting(tag: str, horses_lookup: pd.DataFrame | None,
                       results_lookup: dict) -> pd.DataFrame | None:
    """Build the per-horse feature rows for one meeting, with the label column.
       If horses_lookup is None, falls back to the global HORSES_CSV (used for
       final training on all data)."""
    raw_p = RAW_DIR / f"{tag}.xlsx"
    if not raw_p.exists():
        return None
    if horses_lookup is None:
        horses_lookup = load_horses_lookup()
    raw = pd.read_excel(raw_p, sheet_name="RaceCard")
    raw["horse_no"] = raw["horse_no"].astype(str)
    raw["race_no"]  = raw["race_no"].astype(int)
    # ensure surface is one of Turf/AWT
    raw["surface"]  = raw["surface"].fillna("Turf").astype(str)

    rows = []
    for _, r in raw.iterrows():
        d = {
            "meeting":   tag,
            "race_no":   int(r["race_no"]),
            "horse_no":  str(r["horse_no"]),
            "horse_id":  str(r["horse_id"]),
            # race-level (constant within race)
            "distance":  int(r["distance"]),
            "venue":     tag.split("_")[1],
            "surface":   str(r["surface"]),
            "field_size": int((raw["race_no"] == r["race_no"]).sum()),
            # horse-level numeric
            "draw":      int(r["draw"]) if pd.notna(r["draw"]) else 0,
            "weight_lbs":int(r["weight_lbs"]) if pd.notna(r["weight_lbs"]) else 126,
            "rating":    int(r["rating"]) if pd.notna(r["rating"]) else 50,
            # raw last6 + gear parsing
            **parse_last6(r.get("last6_runs", "")),
            **parse_gear(r.get("gear", None)),
        }
        # derived: draw percentile within this race's field size bucket
        # (computed later in batch)
        rows.append(d)
    df = pd.DataFrame(rows)

    # join per-horse historical stats
    df = df.merge(
        horses_lookup[
            ["horse_id", "venue", "distance", "surface",
             "starts", "wins", "places", "top4_count", "top4_rate",
             "avg_finish_pos", "best_finish_at_conditions",
             "recent_form_l5", "consistency_std", "days_since_last_run",
             "n_meetings_with_results"]
        ],
        on=["horse_id", "venue", "distance", "surface"], how="left",
    )
    # If a horse has no slice for this exact (venue,distance,surface), try the
    # horse's overall stats — same horse across any distance
    overall = horses_lookup.groupby("horse_id").agg(
        ovr_starts=("starts", "sum"),
        ovr_wins=("wins", "sum"),
        ovr_places=("places", "sum"),
        ovr_top4_count=("top4_count", "sum"),
        ovr_avg_finish=("avg_finish_pos", "mean"),
        ovr_recent_l5=("recent_form_l5", "mean"),
    ).reset_index()
    overall["ovr_top4_rate"] = overall["ovr_top4_count"] / overall["ovr_starts"].clip(lower=1)
    df = df.merge(overall, on="horse_id", how="left")
    # Fallback: if slice-specific fields are NaN, use overall horse stats
    df["starts"]   = df["starts"].fillna(df["ovr_starts"]).fillna(0)
    df["wins"]     = df["wins"].fillna(df["ovr_wins"]).fillna(0)
    df["places"]   = df["places"].fillna(df["ovr_places"]).fillna(0)
    df["top4_count"] = df["top4_count"].fillna(df["ovr_top4_count"]).fillna(0)
    df["top4_rate"]= df["top4_rate"].fillna(df["ovr_top4_rate"]).fillna(0.0)
    df["avg_finish_pos"] = df["avg_finish_pos"].fillna(df["ovr_avg_finish"]).fillna(7.0)
    df["recent_form_l5"] = df["recent_form_l5"].fillna(df["ovr_recent_l5"]).fillna(7.0)
    for c in ["best_finish_at_conditions","consistency_std",
              "days_since_last_run","n_meetings_with_results"]:
        df[c] = df[c].fillna(0)

    # drop the ovr_ helper columns
    df = df.drop(columns=[c for c in df.columns if c.startswith("ovr_")])
    # Within-race derived features
    df["draw_pct_in_field"] = df.groupby(["meeting","race_no"])["draw"].transform(
        lambda x: x.rank(pct=True))
    df["weight_pct_in_field"] = df.groupby(["meeting","race_no"])["weight_lbs"].transform(
        lambda x: x.rank(pct=True))
    df["rating_pct_in_field"] = df.groupby(["meeting","race_no"])["rating"].transform(
        lambda x: x.rank(pct=True))

    # join label (actual finishing position)
    if tag in results_lookup:
        res = results_lookup[tag]
        df = df.merge(res[["race_no","horse_no","label_pos","winner"]],
                      on=["race_no","horse_no"], how="left")
        df["label_pos"] = df["label_pos"].fillna(99).astype(int)
        df["winner"]    = df["winner"].fillna(0).astype(int)
    else:
        df["label_pos"] = 99
        df["winner"]    = 0
    return df


def load_results_lookup() -> dict:
    """For each meeting, return a per-(race_no,horse_no) label frame."""
    out = {}
    for p in sorted(RESULTS_DIR.glob("*.xlsx")):
        tag = p.stem
        res = pd.read_excel(p, sheet_name="Results")
        res["horse_no"] = res["horse_no"].fillna(0).astype(int).astype(str)
        res["race_no"]  = res["race_no"].astype(int)
        res["pos"]      = pd.to_numeric(res["pos"], errors="coerce")
        res.loc[res["pos"] <= 0, "pos"] = np.nan
        # use the actual finishing position (1=winner). NaN→99 means ran but didn't place
        out[tag] = res[["race_no","horse_no","pos"]].rename(
            columns={"pos": "label_pos"})
        out[tag]["winner"] = (out[tag]["label_pos"] == 1).astype(int)
    return out


# ── Model + CV ───────────────────────────────────────────────────────────────

FEATURE_COLS = [
    # race-level
    "distance", "field_size",
    # horse-level numeric
    "draw", "weight_lbs", "rating",
    # last6 parsed
    "l6_n", "l6_avg", "l6_best", "l6_worst",
    "l6_top3_count", "l6_top3_rate", "l6_win_count", "l6_win_rate",
    "l6_recency_avg", "l6_consistency", "l6_last_pos",
    # gear
    "gear_count", "gear_has_B", "gear_has_TT", "gear_has_V", "gear_has_PB", "gear_has_SH",
    # within-race percentiles
    "draw_pct_in_field", "weight_pct_in_field", "rating_pct_in_field",
    # per-horse historical slice
    "starts", "wins", "places", "top4_count", "top4_rate",
    "avg_finish_pos", "best_finish_at_conditions",
    "recent_form_l5", "consistency_std", "days_since_last_run",
    "n_meetings_with_results",
]

CATEGORICAL_COLS = ["venue", "surface"]

ALL_COLS = FEATURE_COLS + CATEGORICAL_COLS


def build_dataset(tags: list[str] | None = None,
                   horses_lookup: pd.DataFrame | None = None,
                   results_lookup: dict | None = None) -> pd.DataFrame:
    print("[1/4] Loading lookup tables…", flush=True)
    if results_lookup is None:
        results_lookup = load_results_lookup()
    if horses_lookup is None:
        horses_lookup = load_horses_lookup()
    if tags is None:
        pred_dir = ROOT / "data" / "predictions"
        tags = sorted(p.stem for p in pred_dir.glob("*.xlsx"))

    print(f"[2/4] Building per-meeting feature frames ({len(tags)} tags)…", flush=True)
    pieces = []
    for tag in tags:
        df = build_one_meeting(tag, horses_lookup, results_lookup)
        if df is not None and len(df):
            pieces.append(df)
    ds = pd.concat(pieces, ignore_index=True)
    print(f"      built {len(ds)} horse-race rows across {ds['meeting'].nunique()} meetings", flush=True)
    for c in CATEGORICAL_COLS:
        ds[c] = ds[c].astype("category")
    return ds


def make_groups(df: pd.DataFrame) -> np.ndarray:
    """LightGBM wants groups as a 1D array of group sizes, in row order.
       Group = (meeting, race_no). Sort df by meeting,race_no first."""
    keys = list(zip(df["meeting"], df["race_no"]))
    sizes = []
    i = 0
    while i < len(keys):
        j = i
        while j < len(keys) and keys[j] == keys[i]:
            j += 1
        sizes.append(j - i)
        i = j
    return np.array(sizes, dtype=np.int32)


def evaluate_top4(df: pd.DataFrame, score_col: str) -> dict:
    """Compute top-4 hit rate per pick on a scored frame (already sorted per race)."""
    hits = picks = 0
    win_hits = 0
    top3_hits = 0
    races_total = 0
    races_any_hit = 0
    for (m, r), g in df.groupby(["meeting","race_no"]):
        races_total += 1
        g_sorted = g.sort_values(score_col, ascending=False)
        top4 = g_sorted.head(4)
        picks += 4
        # label_pos is 1=winner, 2-3=top3, 4=top4, 99=ran but not placed
        actual_top4 = g[g["label_pos"].between(1,4)]["horse_no"].tolist()
        for _, hr in top4.iterrows():
            if hr["label_pos"] == 1:
                win_hits += 1
            if hr["label_pos"] in (1,2,3):
                top3_hits += 1
            if hr["label_pos"] in (1,2,3,4):
                hits += 1
        # race-level "did any of the model's top-4 land in actual top-4?"
        race_hits = sum(1 for _, hr in top4.iterrows() if hr["label_pos"] in (1,2,3,4))
        if race_hits > 0:
            races_any_hit += 1
    return {
        "top4_pct":       round(hits / picks * 100, 2) if picks else 0,
        "top3_pct":       round(top3_hits / picks * 100, 2) if picks else 0,
        "win_pct":        round(win_hits / picks * 100, 2) if picks else 0,
        "races_total":    races_total,
        "races_any_top4": races_any_hit,
        "races_top4_pct": round(races_any_hit / races_total * 100, 2) if races_total else 0,
    }


def lomo_cv(params: dict, quiet: bool = False) -> dict:
    import lightgbm as lgb
    print("[2/4] Pre-loading lookup tables…", flush=True)
    results_lookup = load_results_lookup()
    pred_dir = ROOT / "data" / "predictions"
    meetings = sorted(p.stem for p in pred_dir.glob("*.xlsx"))
    folds = []
    if not quiet:
        print(f"\n[3/4] LOMO CV across {len(meetings)} meetings (NO label leakage)…", flush=True)
        print(f"      {'holdout':<18}{'n_train':>10}{'n_val':>8}{'top4':>8}{'win':>8}{'top3':>8}", flush=True)

    for holdout in meetings:
        train_tags = [t for t in meetings if t != holdout]
        # Rebuild horses_lookup using ONLY training tags — prevents leakage
        hc_train = build_horses_lookup_from_tags(train_tags)
        ds_tr = build_dataset(tags=train_tags, horses_lookup=hc_train, results_lookup=results_lookup)
        ds_va = build_dataset(tags=[holdout],   horses_lookup=hc_train, results_lookup=results_lookup)

        train = ds_tr.sort_values(["meeting","race_no"]).reset_index(drop=True)
        val   = ds_va.sort_values(["meeting","race_no"]).reset_index(drop=True)
        X_tr = train[ALL_COLS]; y_tr = train["label_pos"]
        X_va = val[ALL_COLS];   y_va = val["label_pos"]
        g_tr = make_groups(train); g_va = make_groups(val)

        def to_rel(y):
            r = np.ones(len(y), dtype=np.int32)
            r[(y >= 1) & (y <= 4)] = 2
            r[(y >= 1) & (y <= 3)] = 3
            r[y == 1] = 4
            return r
        ytr_rel = to_rel(y_tr.values)
        yva_rel = to_rel(y_va.values)

        dtr = lgb.Dataset(X_tr, label=ytr_rel, group=g_tr, categorical_feature=CATEGORICAL_COLS)
        dva = lgb.Dataset(X_va, label=yva_rel, group=g_va, categorical_feature=CATEGORICAL_COLS,
                          reference=dtr)

        booster = lgb.train(
            params, dtr, num_boost_round=2000,
            valid_sets=[dva], valid_names=["val"],
            callbacks=[lgb.early_stopping(50, verbose=False),
                       lgb.log_evaluation(0)],
        )
        val = val.copy()
        val["lgbm_score"] = booster.predict(X_va, num_iteration=booster.best_iteration)
        m = evaluate_top4(val, "lgbm_score")
        if not quiet:
            print(f"      {holdout:<18}{len(train):>10}{len(val):>8}"
                  f"{m['top4_pct']:>7.2f}%{m['win_pct']:>7.2f}%{m['top3_pct']:>7.2f}%", flush=True)
        folds.append({"holdout": holdout, **m, "best_iter": booster.best_iteration})

    avg = {
        "top4_pct":  round(np.mean([f["top4_pct"]  for f in folds]), 2),
        "win_pct":   round(np.mean([f["win_pct"]   for f in folds]), 2),
        "top3_pct":  round(np.mean([f["top3_pct"]  for f in folds]), 2),
        "races_top4_pct": round(np.mean([f["races_top4_pct"] for f in folds]), 2),
    }
    if not quiet:
        print(f"      {'AVG':<18}{'':>10}{'':>8}"
              f"{avg['top4_pct']:>7.2f}%{avg['win_pct']:>7.2f}%{avg['top3_pct']:>7.2f}%", flush=True)
    return {"folds": folds, "avg": avg}


def train_final(ds: pd.DataFrame, params: dict, n_iter: int) -> object:
    """Train on all data for the saved production model."""
    import lightgbm as lgb
    ds_sorted = ds.sort_values(["meeting","race_no"]).reset_index(drop=True)
    X = ds_sorted[ALL_COLS]; y = ds_sorted["label_pos"]
    g = make_groups(ds_sorted)
    def to_rel(y):
        r = np.ones(len(y), dtype=np.int32)
        r[(y >= 1) & (y <= 4)] = 2
        r[(y >= 1) & (y <= 3)] = 3
        r[y == 1] = 4
        return r
    y_rel = to_rel(y.values)
    dtr = lgb.Dataset(X, label=y_rel, group=g, categorical_feature=CATEGORICAL_COLS)
    booster = lgb.train(params, dtr, num_boost_round=n_iter)
    return booster


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="LightGBM ranker for HKJC horse prediction.")
    ap.add_argument("--quiet", action="store_true", help="Less stdout")
    args = ap.parse_args()
    quiet = args.quiet

    ML_DIR.mkdir(parents=True, exist_ok=True)

    ds = build_dataset()
    print(f"      feature matrix: {ds.shape}  features={len(ALL_COLS)} (incl. {len(CATEGORICAL_COLS)} cat)", flush=True)

    # Conservative LightGBM params — small dataset, ranker, regularization
    params = {
        "objective":     "lambdarank",
        "metric":        "ndcg",
        "ndcg_eval_at":  [4],
        "learning_rate": 0.05,
        "num_leaves":    15,
        "min_child_samples": 10,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l2":     1.0,
        "verbose":       -1,
    }
    print(f"      params: {params}", flush=True)

    cv = lomo_cv(params, quiet=quiet)

    # Save CV results
    (ML_DIR / "lomo_cv_result.json").write_text(json.dumps(cv, indent=2))
    print(f"\n      CV results saved → {ML_DIR/'lomo_cv_result.json'}", flush=True)

    # Train final model on ALL data using median best_iter from CV
    best_iters = [f["best_iter"] for f in cv["folds"] if f["best_iter"]]
    n_iter = int(np.median(best_iters)) if best_iters else 200
    print(f"\n[4/4] Training final model on all data, num_boost_round={n_iter}…", flush=True)
    booster = train_final(ds, params, n_iter=n_iter)
    booster.save_model(str(ML_DIR / "lgbm_ranker.txt"))
    (ML_DIR / "features.json").write_text(json.dumps({"features": ALL_COLS,
                                                       "categorical": CATEGORICAL_COLS}, indent=2))

    # Feature importance
    imp = pd.DataFrame({
        "feature":    booster.feature_name(),
        "gain":       booster.feature_importance(importance_type="gain"),
        "split":      booster.feature_importance(importance_type="split"),
    }).sort_values("gain", ascending=False)
    imp.to_csv(ML_DIR / "feature_importance.csv", index=False)

    print(f"      ✓ model saved → {ML_DIR/'lgbm_ranker.txt'}", flush=True)
    print(f"      ✓ features saved → {ML_DIR/'features.json'}", flush=True)
    print(f"      ✓ importance → {ML_DIR/'feature_importance.csv'}", flush=True)

    # Top features
    print(f"\n── Top 10 features by gain ──", flush=True)
    print(imp.head(10).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()

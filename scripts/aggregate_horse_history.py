#!/usr/bin/env python3
"""
aggregate_horse_history.py — Build per-horse lifetime stats from raw XLSX

Walks every data/raw/YYYY-MM-DD_VN.xlsx, joins with data/results/ to get
finishing positions, and emits a flat CSV at data/horses.csv with one row
per (horse_id, venue, distance_m, surface).

Per slice the script reports:
  starts, wins, places (top-3), top4_rate, avg_finish_pos, best_finish_pos,
  recent_form_l5 (avg position, recency-weighted), consistency (std),
  days_since_last_run (against "today" = latest meeting in dataset).

Usage:
    python aggregate_horse_history.py
    python aggregate_horse_history.py --out data/horses.csv
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ── Constants ────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).resolve().parent.parent
RAW_DIR       = ROOT / "data" / "raw"
RESULTS_DIR   = ROOT / "data" / "results"
DEFAULT_OUT   = ROOT / "data" / "horses.csv"

# Surface classification (results surface can be NaN/中文 — fall back to "Turf")
SURF_MAP = {
    "草地": "Turf", "草地「A」跑道": "Turf", "草地「B」跑道": "Turf",
    "草地「C」跑道": "Turf", "全天候": "AWT", "AWT": "AWT", "TURF": "Turf",
}


def norm_surface(s):
    if pd.isna(s):
        return "Turf"
    s = str(s).strip()
    return SURF_MAP.get(s, s if s in ("Turf", "AWT") else "Turf")


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_meeting(tag: str):
    """Return (raw_df, results_df) for one meeting tag e.g. '2026-04-29_HV'."""
    raw_p = RAW_DIR / f"{tag}.xlsx"
    res_p = RESULTS_DIR / f"{tag}.xlsx"
    if not (raw_p.exists() and res_p.exists()):
        return None, None

    raw = pd.read_excel(raw_p, sheet_name="RaceCard")
    res = pd.read_excel(res_p, sheet_name="Results")

    # Normalise keys
    raw["horse_no"] = raw["horse_no"].astype(str)
    raw["race_no"]  = raw["race_no"].astype(int)
    res["horse_no"] = res["horse_no"].fillna(0).astype(int).astype(str)
    res["race_no"]  = res["race_no"].astype(int)
    res["pos"]      = pd.to_numeric(res["pos"], errors="coerce")
    # pos=0 means DQ / non-starter / scratched — not a real finish
    res.loc[res["pos"] <= 0, "pos"] = np.nan

    # Surface from race meta (rows in results share same surface per meeting)
    if "surface" in res.columns and res["surface"].notna().any():
        surf_src = res.groupby("race_no")["surface"].first()
    elif "surface" in raw.columns and raw["surface"].notna().any():
        surf_src = raw.groupby("race_no")["surface"].first()
    else:
        surf_src = pd.Series("Turf", index=raw["race_no"].unique())

    surf_norm = surf_src.apply(norm_surface)
    raw["surface_norm"] = raw["race_no"].map(surf_norm)
    res["surface_norm"] = res["race_no"].map(surf_norm)

    # Meeting date
    raw["meeting_date"] = pd.to_datetime(tag[:10])
    res["meeting_date"] = pd.to_datetime(tag[:10])

    # Bring distance from raw (per race)
    if "distance" in raw.columns:
        dist_src = raw.groupby("race_no")["distance"].first()
        raw["distance_m"] = raw["race_no"].map(dist_src).fillna(1200).astype(int)
        res["distance_m"] = res["race_no"].map(dist_src).fillna(1200).astype(int)
    else:
        raw["distance_m"] = 1200
        res["distance_m"] = 1200

    # Venue is encoded in the tag
    raw["venue"] = tag.split("_")[1]
    res["venue"] = tag.split("_")[1]

    return raw, res


def load_all():
    """Return a long-format frame: one row per horse-race appearance with pos."""
    pieces = []
    for p in sorted(RAW_DIR.glob("*.xlsx")):
        tag = p.stem
        raw, res = load_meeting(tag)
        if raw is None:
            continue

        # Join raw to results on (race_no, horse_no) to get actual pos per runner.
        # Results only has the top finishers; horses missing from results get pos=NaN
        # — that means "ran but didn't place in recorded top-14". We keep them
        # labelled as pos=99 so they count in `starts` but not in win/place.
        keep = ["race_no", "horse_no", "horse_id", "horse_name",
                "distance_m", "surface_norm", "venue", "meeting_date",
                "draw", "weight_lbs", "jockey", "trainer", "rating"]
        keep = [c for c in keep if c in raw.columns]
        joined = raw[keep].merge(
            res[["race_no", "horse_no", "pos"]].rename(columns={"pos": "finish_pos"}),
            on=["race_no", "horse_no"], how="left",
        )
        joined["finish_pos"] = joined["finish_pos"].fillna(99).astype(int)
        joined["meeting"]   = tag
        pieces.append(joined)

    if not pieces:
        raise SystemExit("No usable raw + results meetings found.")
    return pd.concat(pieces, ignore_index=True)


# ── Aggregations ─────────────────────────────────────────────────────────────

def aggregate(history: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """
    Group by (horse_id, venue, distance_m, surface_norm) and emit lifetime stats.
    `as_of` = the meeting date being predicted for (we use max meeting in dataset
    so 'days_since_last_run' is always >= 0).
    """
    g = history.groupby(["horse_id", "venue", "distance_m", "surface_norm"], dropna=False)

    rows = []
    for keys, sub in g:
        if not isinstance(keys, tuple):
            keys = (keys,)
        horse_id, venue, distance, surface = keys
        sub = sub.sort_values("meeting_date")

        starts = len(sub)
        # `finish_pos == 99` means ran but didn't place in top 14
        placed_mask = (sub["finish_pos"] < 99)
        placed = sub[placed_mask]
        n_placed = int(placed_mask.sum())

        wins   = int((placed["finish_pos"] == 1).sum()) if n_placed else 0
        places = int((placed["finish_pos"] <= 3).sum()) if n_placed else 0
        top4   = int((placed["finish_pos"] <= 4).sum()) if n_placed else 0

        avg_pos = float(placed["finish_pos"].mean()) if n_placed else np.nan
        best    = int(placed["finish_pos"].min()) if n_placed else 99

        # Last 5 finishes, recency-weighted (most recent run gets weight 1.0,
        # oldest gets 0.2). Uses only placed runs to avoid skewing with 99s.
        last5 = placed.tail(5)["finish_pos"].tolist()
        if last5:
            w = np.linspace(0.2, 1.0, len(last5))
            recent_form = float(np.average(last5, weights=w))
        else:
            recent_form = np.nan

        # Consistency: std of placed finishes (lower = more consistent)
        consistency = float(placed["finish_pos"].std()) if n_placed >= 2 else np.nan

        # Days since last run
        last_run_date = sub["meeting_date"].max()
        days_since    = int((as_of - last_run_date).days)

        # Career-best at this exact slice
        best_finish_at_conditions = best if n_placed else 99

        # Jockey/ trainer most recently used at this slice (carry-forward signal)
        last_jockey  = sub.iloc[-1].get("jockey", "") if "jockey"  in sub.columns else ""
        last_trainer = sub.iloc[-1].get("trainer", "") if "trainer" in sub.columns else ""

        rows.append({
            "horse_id":                 str(horse_id),
            "horse_name_last":          sub.iloc[-1].get("horse_name", ""),
            "venue":                    venue,
            "distance_m":               int(distance),
            "surface":                  surface,
            "starts":                   starts,
            "wins":                     wins,
            "places":                   places,           # top-3
            "top4_count":               top4,
            "top4_rate":                round(top4 / max(starts, 1), 4),
            "avg_finish_pos":           round(avg_pos, 2) if not np.isnan(avg_pos) else np.nan,
            "best_finish_at_conditions":best_finish_at_conditions,
            "recent_form_l5":           round(recent_form, 2) if not np.isnan(recent_form) else np.nan,
            "consistency_std":          round(consistency, 2) if not np.isnan(consistency) else np.nan,
            "days_since_last_run":      days_since,
            "last_run_date":            last_run_date.strftime("%Y-%m-%d"),
            "last_jockey":              str(last_jockey),
            "last_trainer":             str(last_trainer),
            "n_meetings_with_results":  int(sub["meeting"].nunique()),
            "as_of":                    as_of.strftime("%Y-%m-%d"),
        })

    out = pd.DataFrame(rows).sort_values(
        ["horse_id", "venue", "distance_m", "surface"]
    ).reset_index(drop=True)
    return out


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Build per-horse lifetime stats CSV.")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="Output CSV path (default: data/horses.csv)")
    args = ap.parse_args()

    print(f"[1/3] Loading all raw + results XLSX from {RAW_DIR}…")
    history = load_all()
    print(f"      loaded {len(history)} horse-race appearances "
          f"across {history['meeting'].nunique()} meetings")

    # Coverage: how many of those appearances have a recorded finishing position?
    placed = (history["finish_pos"] < 99).sum()
    print(f"      {placed}/{len(history)} have a recorded top-14 finish "
          f"({placed/len(history)*100:.1f}%) — the rest ran but didn't place in "
          f"the captured window.")

    # 'as_of' = day after the latest meeting, so days_since_last_run stays positive
    as_of = history["meeting_date"].max() + pd.Timedelta(days=1)
    print(f"[2/3] Aggregating per (horse, venue, distance, surface) "
          f"with as_of={as_of.date()}…")

    out_df = aggregate(history, as_of)

    # Summary stats
    print(f"      {len(out_df)} horse-slice rows  "
          f"({out_df['horse_id'].nunique()} unique horses, "
          f"{out_df['starts'].sum()} lifetime starts)")

    # Coverage sanity: slices with >=3 starts should be most of them
    multi = (out_df["starts"] >= 3).sum()
    print(f"      {multi}/{len(out_df)} slices have >=3 starts "
          f"({multi/len(out_df)*100:.1f}%) — these are the ones with real signal")

    print(f"[3/3] Writing CSV → {args.out}")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"      ✓ wrote {len(out_df)} rows")

    # Quick sanity preview
    print("\n── preview (top 5 by starts) ──")
    cols = ["horse_id", "horse_name_last", "venue", "distance_m", "surface",
            "starts", "wins", "places", "top4_rate", "avg_finish_pos",
            "recent_form_l5", "days_since_last_run"]
    cols = [c for c in cols if c in out_df.columns]
    print(out_df.sort_values("starts", ascending=False).head(5)[cols].to_string(index=False))

    print("\n── preview (fresh horses, days_since_last_run < 14) ──")
    fresh = out_df[out_df["days_since_last_run"] < 14].sort_values(
        ["horse_id", "days_since_last_run"]
    ).head(5)
    if len(fresh):
        print(fresh[cols].to_string(index=False))
    else:
        print("  (no rows)")


if __name__ == "__main__":
    main()

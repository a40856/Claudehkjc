#!/usr/bin/env python3
"""
scripts/merge_odds.py — Merge pre-odds + post-dividends + predictions + results
                            into one unified per-meeting JSON for the dashboard.

For each meeting that has either pre-odds OR post-dividends:
  Reads:
    data/predictions/<tag>.xlsx        (model's top-4 picks with calc_odds)
    data/regen/<tag>.xlsx              (or predictions, for the 24 historical meetings)
    data/results/<tag>.xlsx            (actual finishing positions)
    data/odds/<tag>_pre.json           (live odds, future meetings only)
    data/odds/<tag>_post.json          (dividends, historical meetings)
    data/horses.csv                    (horse lifetime stats, for side-panel)

  Writes:
    data/odds/<tag>_merged.json
      {
        "tag": "2026-04-29_HV",
        "venue": "HV",
        "race_date": "2026/04/29",
        "races": {
          "1": {
            "race_no": 1,
            "distance": 1200,
            "surface": "Turf",
            "pre_odds":   {win: {1: 16.0, ...}, pla: {...}, qin: {...}, qpl: {...}},
            "post_div":   {win: [{combo:"4",div:54.0}], place: [...], ...},
            "horses": [
              {
                "horse_no": 1, "horse_id": "K059", "horse_name": "...",
                "draw": 7, "weight_lbs": 135, "rating": 38, "jockey": "...", "trainer": "...",
                "model_rank": 1, "win_prob": 22.1, "calc_odds": 3.8,
                "pre_win_odds": 16.0, "pre_pla_odds": 4.1,
                "win_div": 54.0, "pla_div": 20.5,           # null if not in top dividend
                "actual_pos": 3, "is_top4": true,
                "lifetime": {...horses.csv slice...}
              },
              ...
            ]
          }
        }
      }

Usage:
    python scripts/merge_odds.py                      # all 24 meetings
    python scripts/merge_odds.py --tag 2026-04-29_HV  # one meeting
    python scripts/merge_odds.py --pred-dir data/regen  # use regen instead of stored
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT          = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RAW_DIR       = ROOT / "data" / "raw"
RESULTS_DIR   = ROOT / "data" / "results"
PRED_DIR      = ROOT / "data" / "predictions"
REGEN_DIR     = ROOT / "data" / "regen"
ODDS_DIR      = ROOT / "data" / "odds"
HORSES_CSV    = ROOT / "data" / "horses.csv"


def load_predictions(tag: str, pred_dir: Path) -> pd.DataFrame | None:
    p = pred_dir / f"{tag}.xlsx"
    if not p.exists():
        return None
    df = pd.read_excel(p, sheet_name="Predictions")
    df["horse_no"] = df["horse_no"].astype(str)
    df["race_no"]  = df["race_no"].astype(int)
    return df


def load_results(tag: str) -> pd.DataFrame | None:
    p = RESULTS_DIR / f"{tag}.xlsx"
    if not p.exists():
        return None
    df = pd.read_excel(p, sheet_name="Results")
    df["horse_no"] = df["horse_no"].fillna(0).astype(int).astype(str)
    df["race_no"]  = df["race_no"].astype(int)
    df["pos"]      = pd.to_numeric(df["pos"], errors="coerce")
    df.loc[df["pos"] <= 0, "pos"] = None
    return df


def load_raw_race_meta(tag: str) -> pd.DataFrame | None:
    p = RAW_DIR / f"{tag}.xlsx"
    if not p.exists():
        return None
    df = pd.read_excel(p, sheet_name="RaceCard")
    df["horse_no"] = df["horse_no"].astype(str)
    df["race_no"]  = df["race_no"].astype(int)
    return df


def load_horses_lookup() -> pd.DataFrame:
    return pd.read_csv(HORSES_CSV)


def load_odds_file(tag: str, suffix: str) -> dict | None:
    p = ODDS_DIR / f"{tag}_{suffix}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def lookup_horse_history(hc: pd.DataFrame, horse_id: str,
                         venue: str, distance: int, surface: str) -> dict | None:
    """Look up the horse's stats for this (venue, distance, surface) slice."""
    if hc is None or hc.empty:
        return None
    sub = hc[(hc["horse_id"] == horse_id)
             & (hc["venue"] == venue)
             & (hc["distance_m"] == distance)
             & (hc["surface"] == surface)]
    if sub.empty:
        sub = hc[hc["horse_id"] == horse_id]
    if sub.empty:
        return None
    sub = sub.sort_values("starts", ascending=False)
    row = sub.iloc[0]
    return {c: (None if pd.isna(v) else v) for c, v in row.to_dict().items()}


def build_race_block(race_no: int, race_meta: dict,
                     race_horses: pd.DataFrame, race_results: pd.DataFrame,
                     pre_odds: dict, post_div: dict,
                     horses_lookup: pd.DataFrame,
                     raw_meta: pd.DataFrame | None = None) -> dict:
    """Build the merged dict for one race."""
    # Race-level meta from raw
    venue = race_meta.get("venue", "")
    distance = int(race_meta.get("distance", 0))
    surface = race_meta.get("surface", "Turf")

    # pre/post dicts (note: the JSON files have a top-level "races" key)
    pre_odds_by_race = (pre_odds or {}).get("races", {}) if pre_odds else {}
    post_div_by_race = (post_div or {}).get("races", {}) if post_div else {}
    pre_race_odds = pre_odds_by_race.get(str(race_no), {}) if pre_odds_by_race else {}
    post_race_div = post_div_by_race.get(str(race_no), {}) if post_div_by_race else {}

    win_pre  = pre_race_odds.get("win",  {}) or {}
    pla_pre  = pre_race_odds.get("pla",  {}) or {}
    qin_pre  = pre_race_odds.get("qin",  {}) or {}
    qpl_pre  = pre_race_odds.get("qpl",  {}) or {}
    horse_names_pre = pre_race_odds.get("horse_names", {}) or {}

    win_post = post_race_div.get("win",            []) or []
    pla_post = post_race_div.get("place",          []) or []
    qin_post = post_race_div.get("quinella",       []) or []
    qpl_post = post_race_div.get("quinella_place", []) or []

    # Quick lookup: horse_no → dividend
    def div_for(pool_list, horse_no_str):
        for entry in pool_list:
            if str(entry.get("combo", "")) == horse_no_str:
                return entry["dividend"]
        return None

    # Quick lookup: horse pair → dividend (canonicalize to sorted pair)
    def pair_div(pool_list, a, b):
        want = f"{min(int(a),int(b))},{max(int(a),int(b))}"
        for entry in pool_list:
            if str(entry.get("combo", "")) == want:
                return entry["dividend"]
        # Try reverse pair too (HKJC sometimes flips)
        want2 = f"{b},{a}"
        for entry in pool_list:
            if str(entry.get("combo", "")) == want2:
                return entry["dividend"]
        return None

    # Per horse: build the row.
    # We need data from BOTH the predictions XLSX (model scores, actual_pos)
    # AND the raw XLSX (horse_id, weight_lbs, rating, last6_runs, gear,
    # full horse_name) because the predictions master sheet only has
    # horse_no + horse_name + draw + jockey + trainer.
    horses_out = []
    for _, hr in race_horses.iterrows():
        hno = str(hr["horse_no"])

        # Look up the same horse in raw XLSX for enriched metadata
        raw_row = None
        if raw_meta is not None and not raw_meta.empty:
            raw_match = raw_meta[(raw_meta["race_no"] == race_no)
                                 & (raw_meta["horse_no"].astype(str) == hno)]
            if not raw_match.empty:
                raw_row = raw_match.iloc[0]

        actual_pos = None
        if race_results is not None and not race_results.empty:
            rrow = race_results[race_results["horse_no"] == hno]
            if not rrow.empty and pd.notna(rrow.iloc[0]["pos"]):
                actual_pos = int(rrow.iloc[0]["pos"])
        is_top4 = actual_pos is not None and 1 <= actual_pos <= 4

        # Resolve values: prefer raw (richer), fall back to pred
        horse_id = (str(raw_row["horse_id"]).strip() if raw_row is not None
                    and pd.notna(raw_row.get("horse_id")) else
                    str(hr.get("horse_id", "") if "horse_id" in hr else ""))
        horse_name = (str(raw_row["horse_name"]).strip() if raw_row is not None
                      and pd.notna(raw_row.get("horse_name")) else
                      str(hr.get("horse_name", "") or horse_names_pre.get(hno, "")))
        weight_lbs = (int(raw_row["weight_lbs"]) if raw_row is not None
                      and pd.notna(raw_row.get("weight_lbs"))
                      else int(hr.get("weight_lbs", 0) or 0))
        rating = (int(raw_row["rating"]) if raw_row is not None
                   and pd.notna(raw_row.get("rating"))
                   else int(hr.get("rating", 0) or 0))
        last6 = (str(raw_row["last6_runs"]) if raw_row is not None
                 and pd.notna(raw_row.get("last6_runs")) else
                 str(hr.get("last6_runs", "")))
        draw = (int(raw_row["draw"]) if raw_row is not None
                 and pd.notna(raw_row.get("draw"))
                 else int(hr.get("draw", 0) or 0))
        jockey = (str(raw_row["jockey"]) if raw_row is not None
                  and pd.notna(raw_row.get("jockey"))
                  else str(hr.get("jockey", "")))
        trainer = (str(raw_row["trainer"]) if raw_row is not None
                   and pd.notna(raw_row.get("trainer"))
                   else str(hr.get("trainer", "")))

        # Build row
        h = {
            "horse_no":   hno,
            "horse_id":   horse_id,
            "horse_name": horse_name,
            "draw":       draw,
            "weight_lbs": weight_lbs,
            "rating":     rating,
            "jockey":     jockey,
            "trainer":    trainer,
            "last6_runs": last6,
            "model_rank": int(hr["pos"]) if pd.notna(hr.get("pos")) else None,
            "win_prob":   float(hr["win_prob%"]) if pd.notna(hr.get("win_prob%")) else None,
            "calc_odds":  float(hr["calc_odds"]) if pd.notna(hr.get("calc_odds")) else None,
            "pre_win_odds":  win_pre.get(hno),
            "pre_pla_odds":  pla_pre.get(hno),
            "win_div":    div_for(win_post, hno),
            "pla_div":    div_for(pla_post, hno),
            "actual_pos": actual_pos,
            "is_top4":    is_top4,
            "lifetime":   lookup_horse_history(
                horses_lookup,
                horse_id,
                venue, distance, surface,
            ),
        }
        # Quinella place dividend (per pair involving this horse)
        qpl_entries = []
        if qpl_post:
            for entry in qpl_post:
                combo = str(entry.get("combo", ""))
                if "," in combo:
                    a, b = combo.split(",", 1)
                    if hno in (str(a), str(b)):
                        qpl_entries.append({
                            "pair": combo,
                            "dividend": entry["dividend"],
                        })
        h["qpl_pairs"] = qpl_entries
        horses_out.append(h)

    # Quinella best guess: if model picked P1 + P2, what was the actual Q dividend?
    # Useful for the dashboard's "if you'd bet the model's top 2, here's the payout"
    model_picks = sorted(
        [h for h in horses_out if h["model_rank"] is not None],
        key=lambda h: h["model_rank"],
    )[:2]
    if len(model_picks) >= 2:
        a, b = model_picks[0]["horse_no"], model_picks[1]["horse_no"]
        actual_q_div = pair_div(qin_post, a, b)
        if actual_q_div is None:
            # Check if either ordering matches a stored combo
            for entry in qin_post:
                combo = str(entry.get("combo", ""))
                if "," in combo:
                    x, y = combo.split(",", 1)
                    if {str(a), str(b)} == {str(x), str(y)}:
                        actual_q_div = entry["dividend"]
                        break
    else:
        actual_q_div = None

    return {
        "race_no":      race_no,
        "distance":     distance,
        "surface":      surface,
        "field_size":   int(race_meta.get("field_size", len(race_horses))),
        "pre_odds": {
            "win": win_pre, "pla": pla_pre, "qin": qin_pre, "qpl": qpl_pre,
        },
        "post_div": {
            "win":            win_post,
            "place":          pla_post,
            "quinella":       qin_post,
            "quinella_place": qpl_post,
        },
        "horses": horses_out,
        "actual_quinella_div_for_model_p12": actual_q_div,
    }


def merge_one_meeting(tag: str, horses_lookup: pd.DataFrame,
                      pred_dir: Path) -> dict | None:
    """Merge everything for one meeting. Returns None if nothing to merge."""
    # Predictions
    pred = load_predictions(tag, pred_dir)
    if pred is None or pred.empty:
        return None  # no predictions = skip

    results  = load_results(tag)
    raw_meta = load_raw_race_meta(tag)
    pre_odds = load_odds_file(tag, "pre")
    post_div = load_odds_file(tag, "post")

    if pre_odds is None and post_div is None and results is None:
        # Nothing to merge for this meeting
        return None

    # Determine venue from tag
    venue = tag.split("_")[1]
    race_date = tag[:10].replace("-", "/")

    races_out = {}
    for race_no, race_pred in pred.groupby("race_no"):
        race_no = int(race_no)
        # Race-level meta
        race_meta = {"venue": venue}
        if raw_meta is not None and not raw_meta.empty:
            rrow = raw_meta[raw_meta["race_no"] == race_no]
            if not rrow.empty:
                first = rrow.iloc[0]
                race_meta["distance"] = int(first.get("distance", 0) or 0)
                race_meta["surface"]  = str(first.get("surface", "Turf") or "Turf")
                race_meta["field_size"] = int((raw_meta["race_no"] == race_no).sum())

        race_results = (results[results["race_no"] == race_no]
                        if results is not None and not results.empty
                        else pd.DataFrame())

        races_out[str(race_no)] = build_race_block(
            race_no, race_meta, race_pred, race_results,
            pre_odds or {}, post_div or {},
            horses_lookup, raw_meta=raw_meta,
        )

    return {
        "tag":         tag,
        "venue":       venue,
        "race_date":   race_date,
        "has_pre":     pre_odds is not None,
        "has_post":    post_div is not None,
        "races":       races_out,
    }


def main():
    ap = argparse.ArgumentParser(description="Merge odds/dividends/predictions/results.")
    ap.add_argument("--tag", help="Single meeting tag")
    ap.add_argument("--pred-dir", default=str(PRED_DIR),
                    help=f"Directory with predictions XLSX (default: {PRED_DIR})")
    args = ap.parse_args()

    pred_dir = Path(args.pred_dir)
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    horses_lookup = load_horses_lookup()

    if args.tag:
        tags = [args.tag]
    else:
        # All meetings with predictions
        tags = sorted(p.stem for p in pred_dir.glob("*.xlsx"))

    print(f"Merging {len(tags)} meetings from {pred_dir}\n", flush=True)
    ok = 0
    for tag in tags:
        merged = merge_one_meeting(tag, horses_lookup, pred_dir)
        if merged is None:
            print(f"  ✗ {tag}: no predictions or empty")
            continue
        out = ODDS_DIR / f"{tag}_merged.json"
        out.write_text(json.dumps(merged, indent=2, ensure_ascii=False, default=str),
                       encoding="utf-8")
        n_races = len(merged["races"])
        flags = "".join(["P" if merged["has_pre"] else ".",
                         "D" if merged["has_post"] else "."])
        print(f"  ✓ {tag}: {n_races} races [{flags}]")
        ok += 1
    print(f"\nDone: {ok}/{len(tags)} merged.")


if __name__ == "__main__":
    main()

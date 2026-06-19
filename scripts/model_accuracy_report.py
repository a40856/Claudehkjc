#!/usr/bin/env python3
"""
model_accuracy_report.py — Compare stored predictions against actual results

Uses the predictions (data/predictions/*.xlsx) and results (data/results/*.xlsx)
that are already on disk. No scraping, no re-running the model. Pure audit.

For every race it computes, for each of the model's top-4 picks (P1..P4):
  - win          : did this horse win (actual_pos == 1)?
  - place_top3   : did this horse finish in the top 3?
  - place_top4   : did this horse finish in the top 4?  (your model's official metric)
  - payout       : if we had bet $1 at win_odds, what did we get back?

Then aggregates:
  - per meeting: hit rate per rank
  - per venue (HV vs ST): hit rate
  - overall:  win hit %, place top-3 %, top-4 %, total $/bet, ROI%

Usage:
    python model_accuracy_report.py
    python model_accuracy_report.py --out data/model_accuracy_report.json
    python model_accuracy_report.py --show-unpicked     # also show best un-picked horse
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT        = Path(__file__).resolve().parent.parent
PRED_DIR    = ROOT / "data" / "predictions"
RESULTS_DIR = ROOT / "data" / "results"
DEFAULT_OUT = ROOT / "data" / "model_accuracy_report.json"
# HKJC place payout rule of thumb (used to estimate place dividend when we
# don't have the dividend data — accurate within a few % for short fields).
# Format: {field_size_min: (win, place)} as fraction of stake returned.
PLACE_PAYOUT_BY_FIELD = {
    # field_size -> (win_unit, place_unit) where unit = HK$ returned per HK$1
    7:  (8.5, 2.6),
    8:  (9.0, 2.5),
    9:  (9.5, 2.4),
    10: (10.0, 2.3),
    11: (11.0, 2.2),
    12: (12.0, 2.1),
    13: (13.0, 2.05),
    14: (14.0, 2.0),
}


# ── Data loaders ─────────────────────────────────────────────────────────────

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
    df.loc[df["pos"] <= 0, "pos"] = np.nan
    return df


def load_post_div_for(tag: str, race_no: int) -> dict | None:
    """Load real dividends for one race from data/odds/<tag>_post.json.
    Returns the per-race dict (win/place/quinella/quinella_place) or None."""
    p = ROOT / "data" / "odds" / f"{tag}_post.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        races = d.get("races", {})
        return races.get(str(race_no), races.get(int(race_no)))
    except Exception:
        return None


def common_meetings(pred_dir: Path) -> list[str]:
    a = {p.stem for p in pred_dir.glob("*.xlsx")}
    b = {p.stem for p in RESULTS_DIR.glob("*.xlsx")}
    return sorted(a & b)


# ── Per-pick scoring ─────────────────────────────────────────────────────────

def score_pick(pick: pd.Series, race_results: pd.DataFrame, field_size: int,
                post_div: dict | None = None) -> dict:
    """Score a single pick against the race's actual results.

    If `post_div` is provided (the per-race dict from _post.json with pools
    win/place/quinella/quinella_place), use real dividends instead of the
    rule-of-thumb estimate.
    """
    r = race_results[race_results["horse_no"] == pick["horse_no"]]
    if r.empty:
        actual_pos = np.nan
    else:
        actual_pos = float(r.iloc[0]["pos"])

    win_odds = float(r["win_odds"].iloc[0]) if (not r.empty and "win_odds" in r.columns
                                                and pd.notna(r["win_odds"].iloc[0])) else np.nan

    win   = (actual_pos == 1)
    top3  = pd.notna(actual_pos) and actual_pos <= 3
    top4  = pd.notna(actual_pos) and actual_pos <= 4

    # ── Real dividends (from _post.json) take priority over rule-of-thumb ──
    win_payout = 0.0
    place_payout = 0.0
    actual_win_div = None
    actual_pla_div = None
    if post_div:
        for entry in post_div.get("win", []):
            if str(entry.get("combo", "")) == str(pick["horse_no"]):
                actual_win_div = entry["dividend"]
                break
        for entry in post_div.get("place", []):
            if str(entry.get("combo", "")) == str(pick["horse_no"]):
                actual_pla_div = entry["dividend"]
                break
    if win and actual_win_div is not None:
        win_payout = actual_win_div
    elif win and not np.isnan(win_odds):
        win_payout = win_odds  # fall back to payout odds

    if top3 and actual_pla_div is not None:
        place_payout = actual_pla_div
    elif top3 and not np.isnan(win_odds):
        # rule-of-thumb estimate
        bucket = min(PLACE_PAYOUT_BY_FIELD.keys(), key=lambda k: abs(k - field_size))
        win_unit, place_unit = PLACE_PAYOUT_BY_FIELD[bucket]
        place_payout = win_odds * (place_unit / win_unit)

    # ── Model vs payout calibration (not a true value flag — payout odds are
    #    retrospective, not pre-race market odds. For real value bets you'd
    #    compare against pre-race live odds from data/odds/<tag>_pre.json)
    calc_odds = float(pick.get("calc_odds", 0) or 0)
    payout_odds = win_odds if not np.isnan(win_odds) else None
    calib_flag = None
    calib_ratio = None
    if payout_odds and calc_odds > 0:
        calib_ratio = round(payout_odds / calc_odds, 3)
        if   calib_ratio >= 1.20: calib_flag = "UNFAIR ⬆"  # market paid ≥20% more than model said
        elif calib_ratio <= 0.80: calib_flag = "LUCKY ⬇"  # market paid ≤20% less (we got lucky)
        else:                     calib_flag = "FAIR"

    return {
        "pos_in_picks": int(pick["pos"]),
        "horse_no":     str(pick["horse_no"]),
        "horse":        str(pick.get("horse", "")),
        "win_prob_pct": float(pick.get("win_prob%", 0) or 0),
        "composite":    float(pick.get("composite", 0) or 0),
        "calc_odds":    calc_odds,
        "actual_pos":   int(actual_pos) if pd.notna(actual_pos) else None,
        "win":          bool(win),
        "top3":         bool(top3),
        "top4":         bool(top4),
        "win_odds":     float(win_odds) if not np.isnan(win_odds) else None,
        "win_div":      actual_win_div,
        "pla_div":      actual_pla_div,
        "win_payout_per_1":   round(float(win_payout), 2),
        "place_payout_per_1": round(float(place_payout), 2),
        "value_flag":   calib_flag,
        "value_ratio":  calib_ratio,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Audit stored predictions vs actuals.")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="JSON output path (default: data/model_accuracy_report.json)")
    ap.add_argument("--pred-dir", default=str(PRED_DIR),
                    help="Directory holding predictions XLSX (default: data/predictions)")
    ap.add_argument("--show-unpicked", action="store_true",
                    help="Also list best unpicked horse per race (would-have-been-winner)")
    args = ap.parse_args()

    pred_dir = Path(args.pred_dir)
    meetings = common_meetings(pred_dir)
    print(f"Auditing {len(meetings)} meetings with both predictions + results "
          f"(pred_dir={pred_dir})…\n")

    all_picks   = []  # flat list of pick-level records
    race_summaries = []

    for tag in meetings:
        pred  = load_predictions(tag, pred_dir)
        res   = load_results(tag)
        if pred is None or res is None:
            continue

        venue = tag.split("_")[1]

        for race_no, race_pred in pred.groupby("race_no"):
            race_res = res[res["race_no"] == race_no]
            if race_res.empty:
                continue

            field_size = int(race_pred["horse_no"].nunique())

            picks_sorted = race_pred.sort_values("pos").head(4)
            post_div = load_post_div_for(tag, int(race_no))
            scored = [score_pick(p, race_res, field_size, post_div=post_div)
                      for _, p in picks_sorted.iterrows()]


            # Track best unpicked (would-have-been-winner) if requested
            winner_row = race_res[race_res["pos"] == 1]
            unpicked_winner = None
            if args.show_unpicked and not winner_row.empty:
                w_no = str(winner_row.iloc[0]["horse_no"])
                if w_no not in picks_sorted["horse_no"].astype(str).tolist():
                    unpicked_winner = {
                        "horse_no": w_no,
                        "horse":    str(winner_row.iloc[0].get("horse_name", "")),
                        "win_odds": float(winner_row.iloc[0].get("win_odds", np.nan) or np.nan),
                    }

            race_summaries.append({
                "meeting":        tag,
                "venue":          venue,
                "race_no":        int(race_no),
                "field_size":     field_size,
                "picks":          scored,
                "unpicked_winner":unpicked_winner,
                "any_top4_hit":   any(p["top4"] for p in scored),
                "best_actual_pos":int(race_res["pos"].min()),
            })

            for p in scored:
                p2 = dict(p)
                p2["meeting"] = tag
                p2["venue"]   = venue
                p2["race_no"] = int(race_no)
                p2["field_size"] = field_size
                all_picks.append(p2)

    picks_df = pd.DataFrame(all_picks)
    n_picks = len(picks_df)
    n_races = len(race_summaries)

    print("="*72)
    print(f"  MODEL ACCURACY REPORT  ({n_races} races, {n_picks} picks, {len(meetings)} meetings)")
    print("="*72)

    # ── Headline metrics ─────────────────────────────────────────────────────
    print("\n── Headline (any pick hit any criteria) ──")
    for col, label in [("win", "Win (P1-P4 = winner)"),
                       ("top3", "Place top-3 (any pick finished 1-3)"),
                       ("top4", "Place top-4 (any pick finished 1-4)")]:
        n = int(picks_df[col].sum())
        print(f"  {label:<40} {n:>4}/{n_picks}  = {n/n_picks*100:5.2f}%")

    # Per-rank metrics (the model picks 4 horses; rank them by how often each wins)
    print("\n── Per-rank accuracy (P1 = model's strongest pick) ──")
    print(f"  {'rank':<6}{'picks':>8}{'win':>10}{'top3':>10}{'top4':>10}")
    for r in [1, 2, 3, 4]:
        sub = picks_df[picks_df["pos_in_picks"] == r]
        if sub.empty:
            continue
        print(f"  P{r:<5}{len(sub):>8}"
              f"{sub['win'].sum()*100/len(sub):>9.1f}%"
              f"{sub['top3'].sum()*100/len(sub):>9.1f}%"
              f"{sub['top4'].sum()*100/len(sub):>9.1f}%")

    # ── By venue ─────────────────────────────────────────────────────────────
    print("\n── By venue ──")
    print(f"  {'venue':<8}{'races':>8}{'top4 hit races':>18}{'win hit':>12}")
    for v, g in picks_df.groupby("venue"):
        races = g.groupby(["meeting","race_no"]).size().shape[0]
        # a race 'hit' on top-4 if any of its 4 picks finished top-4
        race_hit_top4 = g.groupby(["meeting","race_no"])["top4"].any().sum()
        win_hits = g["win"].sum()
        print(f"  {v:<8}{races:>8}{int(race_hit_top4):>14}  ({race_hit_top4/races*100:5.1f}%)"
              f"{win_hits:>8}  ({win_hits/races*100:5.1f}%)")

    # ── By meeting (chronological) ───────────────────────────────────────────
    print("\n── Per-meeting top-4 hit rate (the model's official metric) ──")
    print(f"  {'meeting':<18}{'races':>7}{'P1 win':>9}{'top4 hit':>11}{'top4%':>8}")
    per_meeting = []
    for tag in meetings:
        sub = picks_df[picks_df["meeting"] == tag]
        if sub.empty:
            continue
        races = sub.groupby("race_no").size().shape[0]
        p1 = sub[sub["pos_in_picks"] == 1]
        race_hit_top4 = int(sub.groupby("race_no")["top4"].any().sum())
        top4_pct = race_hit_top4 / races * 100
        print(f"  {tag:<18}{races:>7}"
              f"{int(p1['win'].sum()):>8}"
              f"{race_hit_top4:>10}/{races:<2}"
              f"{top4_pct:>7.1f}%")
        per_meeting.append({"meeting": tag, "races": races,
                            "p1_wins": int(p1["win"].sum()),
                            "races_top4_hit": race_hit_top4,
                            "top4_pct": round(top4_pct, 2)})

    # ── Naïve baseline: market favourite per race ────────────────────────────
    print("\n── Naïve baseline: just pick the 4 lowest-odds horses each race ──")
    fav_hits = 0
    fav_top4 = 0
    fav_picks = 0
    for tag in meetings:
        res = load_results(tag)
        if res is None:
            continue
        res["win_odds"] = pd.to_numeric(res.get("win_odds"), errors="coerce")
        for race_no, g in res.groupby("race_no"):
            g = g.dropna(subset=["win_odds"]).sort_values("win_odds").head(4)
            fav_picks += len(g)
            fav_hits  += int((g["pos"] == 1).sum())
            fav_top4  += int((g["pos"] <= 4).sum())
    print(f"  favourite top-4 hit races : {fav_top4} / {fav_picks} picks  "
          f"= {fav_top4/fav_picks*100:.2f}%")
    print(f"  favourite wins (any of 4) : {fav_hits} / {fav_picks} picks  "
          f"= {fav_hits/fav_picks*100:.2f}%")

    # ── Calibration: model.calc_odds vs actual payout odds ─────────────────────
    if "value_flag" in picks_df.columns:
        v = picks_df["value_flag"].fillna("(no odds)").value_counts()
        print("\n── Calibration (model.calc_odds vs final payout odds) ──")
        for label, cnt in v.items():
            print(f"  {label:<14} {cnt:>4} picks")
        # ROI per bucket
        if "UNFAIR ⬆" in v.index:
            value_subset = picks_df[picks_df["value_flag"] == "UNFAIR ⬆"]
            v_stake = len(value_subset)
            v_return = float(value_subset["place_payout_per_1"].sum())
            print(f"  UNFAIR picks place-ROI: "
                  f"{v_return:.2f} returned on {v_stake} staked = "
                  f"{(v_return - v_stake) / v_stake * 100:+.2f}%")
        if "LUCKY ⬇" in v.index:
            short_subset = picks_df[picks_df["value_flag"] == "LUCKY ⬇"]
            s_stake = len(short_subset)
            s_return = float(short_subset["place_payout_per_1"].sum())
            print(f"  LUCKY picks place-ROI: "
                  f"{s_return:.2f} returned on {s_stake} staked = "
                  f"{(s_return - s_stake) / s_stake * 100:+.2f}%")

    # ── ROI simulation (win bet $1 per pick; place bet $1 per pick) ──────────
    print("\n── Hypothetical ROI ($1 win bet per pick, $1 place bet per pick) ──")
    win_bets,  win_returned  = n_picks, float(picks_df["win_payout_per_1"].sum())
    pl_bets,   pl_returned   = n_picks, float(picks_df["place_payout_per_1"].sum())
    print(f"  Win bets   : staked ${win_bets},  returned ${win_returned:>7.2f}   "
          f"ROI {(win_returned-win_bets)/win_bets*100:+6.2f}%")
    print(f"  Place bets : staked ${pl_bets},   returned ${pl_returned:>7.2f}   "
          f"ROI {(pl_returned-pl_bets)/pl_bets*100:+6.2f}%")

    # ── Save JSON ────────────────────────────────────────────────────────────
    report = {
        "as_of":            pd.Timestamp.now().isoformat(timespec="seconds"),
        "n_meetings":       len(meetings),
        "n_races":          n_races,
        "n_picks":          n_picks,
        "headline": {
            "win_pct_any_pick":   round(picks_df["win"].sum()  / n_picks * 100, 2),
            "top3_pct_any_pick":  round(picks_df["top3"].sum() / n_picks * 100, 2),
            "top4_pct_any_pick":  round(picks_df["top4"].sum() / n_picks * 100, 2),
            "p1_win_pct":         round(
                picks_df[picks_df["pos_in_picks"]==1]["win"].sum() /
                max((picks_df["pos_in_picks"]==1).sum(), 1) * 100, 2),
        },
        "per_rank": {},
        "per_venue": {},
        "per_meeting": per_meeting,
        "naive_baseline_top4_pct": round(fav_top4 / fav_picks * 100, 2) if fav_picks else None,
        "roi_sim": {
            "win_bets":  win_bets,  "win_returned": round(win_returned, 2),
            "win_roi_pct": round((win_returned - win_bets) / win_bets * 100, 2),
            "place_bets": pl_bets,  "place_returned": round(pl_returned, 2),
            "place_roi_pct": round((pl_returned - pl_bets) / pl_bets * 100, 2),
        },
        "caveat": (
            "win_payout uses HKJC payout odds from results XLSX. "
            "place_payout is a rule-of-thumb estimate (1/4 win div, top-3, "
            "adjusted by field size). The model does not store live pre-race "
            "odds yet, so value-betting ROI cannot be computed accurately — "
            "fix that by wiring live_odds.py into the prediction pipeline."
        ),
    }

    for r in [1, 2, 3, 4]:
        sub = picks_df[picks_df["pos_in_picks"] == r]
        if sub.empty:
            continue
        report["per_rank"][f"P{r}"] = {
            "n":       len(sub),
            "win_pct": round(sub["win"].sum() / len(sub) * 100, 2),
            "top3_pct":round(sub["top3"].sum() / len(sub) * 100, 2),
            "top4_pct":round(sub["top4"].sum() / len(sub) * 100, 2),
        }

    for v, g in picks_df.groupby("venue"):
        races = g.groupby(["meeting","race_no"]).size().shape[0]
        race_hit_top4 = int(g.groupby(["meeting","race_no"])["top4"].any().sum())
        report["per_venue"][v] = {
            "n_races":          races,
            "races_top4_hit":   race_hit_top4,
            "races_top4_pct":   round(race_hit_top4 / races * 100, 2),
            "win_hits_any_pick":int(g["win"].sum()),
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n✓ Full report written to {out_path}")
    print(f"  (open it for the per-meeting / per-venue detail)")


if __name__ == "__main__":
    main()

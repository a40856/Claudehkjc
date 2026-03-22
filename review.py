"""
review.py — Post-Race Review & Backtest Logger
================================================
Run AFTER a race day. Fetches final results + final odds,
compares against predictions, calculates accuracy, and
appends to the long-term backtest log CSV.

Usage:
    python review.py                              # today, ST
    python review.py --date 2026/03/22 --venue HV
    python review.py --show-log                   # print cumulative stats only
"""

import argparse, csv, json, os, re, sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import URLS, HEADERS, OUTPUT_DIR, PLACES

os.makedirs(OUTPUT_DIR, exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

BACKTEST_LOG = Path(OUTPUT_DIR) / "backtest_log.csv"
LOG_COLUMNS  = [
    "date", "venue", "race_no", "race_name", "class_", "distance", "surface",
    "pred_rank1", "pred_rank2", "pred_rank3", "pred_rank4",
    "actual_rank1", "actual_rank2", "actual_rank3", "actual_rank4",
    "hits", "hit_rate", "model", "notes",
]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. FETCH FINAL RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_results(race_date: str, venue: str) -> dict:
    """
    Fetch official HKJC final results for all races.
    Returns: {race_no: [{place, horse_no, horse_name, final_odds}, ...]}
    Tries HKJC ResultsAll page first, falls back to SCMP.
    Caches result permanently once retrieved.
    """
    cache_path = Path(OUTPUT_DIR) / \
        f"results_{race_date.replace('/', '-')}_{venue}.json"
    if cache_path.exists():
        with open(cache_path) as f:
            return {int(k): v for k, v in json.load(f).items()}

    results = {}

    # ── HKJC ResultsAll ───────────────────────────────────────────────────────
    try:
        params = {"RaceDate": race_date, "Racecourse": venue}
        resp   = SESSION.get(URLS["race_results"], params=params, timeout=15)
        resp.raise_for_status()
        soup   = BeautifulSoup(resp.text, "html.parser")

        for block in soup.select(".raceResult, .resultTable"):
            rno   = _parse_race_no(block)
            table = block.select_one("table")
            if not table:
                continue
            finishers = []
            for row in table.select("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.select("td")]
                if len(cells) >= 3 and cells[0].isdigit():
                    finishers.append({
                        "place":      int(cells[0]),
                        "horse_no":   str(cells[1]),
                        "horse_name": cells[2],
                        "final_odds": _safe_float(cells[-1]),
                    })
            if finishers:
                results[rno] = finishers

        if results:
            print(f"  ✓ Results via HKJC: {len(results)} races")
    except Exception as e:
        print(f"  ⚠ HKJC results fetch: {e}")

    # ── SCMP fallback ─────────────────────────────────────────────────────────
    if not results:
        print("  Trying SCMP fallback...")
        date_compact = race_date.replace("/", "")
        for rno in range(1, 13):
            try:
                url  = f"{URLS['scmp_result']}/{date_compact}/{rno}"
                resp = SESSION.get(url, timeout=10)
                if resp.status_code != 200:
                    break
                soup      = BeautifulSoup(resp.text, "html.parser")
                finishers = []
                for row in soup.select("table.race-result tr, table tr"):
                    cells = [td.get_text(strip=True) for td in row.select("td")]
                    if len(cells) >= 4 and re.match(r"^\d+$", cells[0]):
                        finishers.append({
                            "place":      int(cells[0]),
                            "horse_no":   str(cells[1]),
                            "horse_name": cells[2],
                            "final_odds": _safe_float(cells[-2]),
                        })
                if finishers:
                    results[rno] = finishers
            except Exception:
                break
        if results:
            print(f"  ✓ Results via SCMP: {len(results)} races")

    if not results:
        print("  ✗ Could not fetch results from any source.")
        return {}

    # Cache permanently
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ACCURACY CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_race(pred_df: pd.DataFrame, actual: list) -> dict:
    """
    Compare top-PLACES predictions vs actual finishing positions.
    Returns dict with pred_top4, actual_top4, hits, hit_rate,
    hit_horses, missed_horses, surprise_horses.
    """
    pred_top4   = pred_df.head(PLACES)["horse_no"].astype(str).tolist()
    actual_top4 = [str(r["horse_no"]) for r in actual if r["place"] <= PLACES]
    pred_set    = set(pred_top4)
    actual_set  = set(actual_top4)
    hits        = len(pred_set & actual_set)

    return {
        "pred_top4":       pred_top4,
        "actual_top4":     actual_top4,
        "pred_set":        pred_set,
        "hits":            hits,
        "hit_rate":        round(hits / PLACES, 3),
        "hit_horses":      sorted(pred_set & actual_set),
        "missed_horses":   sorted(pred_set - actual_set),
        "surprise_horses": sorted(actual_set - pred_set),
    }


def evaluate_day(predictions: dict, results: dict) -> list:
    """Evaluate all races for one race day."""
    evals = []
    for rno in sorted(predictions.keys()):
        if rno not in results:
            continue
        ev          = evaluate_race(predictions[rno], results[rno])
        ev["race_no"] = rno
        evals.append(ev)
    return evals


# ═══════════════════════════════════════════════════════════════════════════════
# 3. BACKTEST LOG
# ═══════════════════════════════════════════════════════════════════════════════

def append_to_log(evals: list, race_date: str, venue: str,
                  predictions: dict, results: dict):
    """Append today's evaluation to the long-term backtest CSV log."""
    file_exists = BACKTEST_LOG.exists()
    with open(BACKTEST_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if not file_exists:
            writer.writeheader()
        for ev in evals:
            rno = ev["race_no"]
            row = {
                "date":        race_date,
                "venue":       venue,
                "race_no":     rno,
                "race_name":   "",
                "class_":      "",
                "distance":    "",
                "surface":     "",
                "pred_rank1":  ev["pred_top4"][0] if len(ev["pred_top4"]) > 0 else "",
                "pred_rank2":  ev["pred_top4"][1] if len(ev["pred_top4"]) > 1 else "",
                "pred_rank3":  ev["pred_top4"][2] if len(ev["pred_top4"]) > 2 else "",
                "pred_rank4":  ev["pred_top4"][3] if len(ev["pred_top4"]) > 3 else "",
                "actual_rank1":ev["actual_top4"][0] if len(ev["actual_top4"]) > 0 else "",
                "actual_rank2":ev["actual_top4"][1] if len(ev["actual_top4"]) > 1 else "",
                "actual_rank3":ev["actual_top4"][2] if len(ev["actual_top4"]) > 2 else "",
                "actual_rank4":ev["actual_top4"][3] if len(ev["actual_top4"]) > 3 else "",
                "hits":        ev["hits"],
                "hit_rate":    ev["hit_rate"],
                "model":       "ScenarioE",
                "notes":       "",
            }
            writer.writerow(row)


def load_backtest_log() -> pd.DataFrame:
    """Load and return the full backtest log as a DataFrame."""
    if not BACKTEST_LOG.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    return pd.read_csv(BACKTEST_LOG)


def print_log_summary():
    """Print aggregate accuracy statistics from the backtest log."""
    df = load_backtest_log()
    if df.empty:
        print("  No backtest data yet.")
        return

    print(f"\n{'═'*85}")
    print(f"  BACKTEST LOG SUMMARY  "
          f"({len(df)} races across {df['date'].nunique()} race days)")
    print(f"{'─'*85}")

    overall = df["hit_rate"].mean() * 100
    st_avg  = df[df["venue"] == "ST"]["hit_rate"].mean() * 100 \
              if "ST" in df["venue"].values else 0
    hv_avg  = df[df["venue"] == "HV"]["hit_rate"].mean() * 100 \
              if "HV" in df["venue"].values else 0

    print(f"  Overall avg top-{PLACES} hit rate : {overall:.1f}%  (target ≥50%)")
    print(f"  Sha Tin  (ST)              : {st_avg:.1f}%")
    print(f"  Happy Valley (HV)          : {hv_avg:.1f}%")

    print(f"\n  Hit distribution:")
    for h in [4, 3, 2, 1, 0]:
        cnt = (df["hits"] == h).sum()
        pct = cnt / len(df) * 100
        bar = "█" * int(pct / 2)
        print(f"    {h}/{PLACES} hits : {cnt:3d} races  ({pct:5.1f}%)  {bar}")

    print(f"\n  By race day:")
    by_day = df.groupby(["date", "venue"])["hit_rate"].mean() * 100
    for (date, v), acc in by_day.items():
        bar = "█" * int(acc / 5)
        print(f"    {date}  {v}  {acc:5.1f}%  {bar}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def print_review_table(ev: dict, pred_df: pd.DataFrame, actual: list):
    """Print per-race review: predictions vs actuals with final odds."""
    rno = ev["race_no"]
    print(f"\n  RACE {rno}  {'─'*65}")
    print(f"  Predicted top-{PLACES} : "
          f"{', '.join('#' + h for h in ev['pred_top4'])}")
    print(f"  Actual top-{PLACES}    : "
          f"{', '.join('#' + h for h in ev['actual_top4'])}")
    print(f"  Hits            : {ev['hits']}/{PLACES}  "
          f"({ev['hit_rate']*100:.0f}%)")
    print(f"  ✅ Correct       : "
          f"{', '.join('#'+h for h in ev['hit_horses']) or 'None'}")
    print(f"  ❌ Missed        : "
          f"{', '.join('#'+h for h in ev['missed_horses']) or 'None'}")
    print(f"  😮 Surprises     : "
          f"{', '.join('#'+h for h in ev['surprise_horses']) or 'None'}")

    print(f"\n  {'Place':<7} {'#':<4} {'Horse':<22} {'FinalOdds':>10} "
          f"{'OurRank':>8} {'CalcOdds':>9} {'InTop4?':>7}")
    print(f"  {'─'*72}")

    for r in sorted(actual, key=lambda x: x["place"])[:8]:
        hno     = str(r["horse_no"])
        our_row = pred_df[pred_df["horse_no"].astype(str) == hno]
        our_rank  = int(our_row.index[0]) + 1 if not our_row.empty else "─"
        calc_odds = f"{our_row.iloc[0]['calc_odds']:.1f}x" \
                    if not our_row.empty else "─"
        in_top4   = "★" if hno in ev["pred_set"] else " "
        print(
            f"  {r['place']:<7} #{hno:<3} {str(r['horse_name']):<22} "
            f"{r['final_odds']:>9.1f}x {str(our_rank):>8} "
            f"{calc_odds:>9} {in_top4:>7}"
        )


def print_day_summary(evals: list):
    """Print race-day accuracy summary."""
    total_hits = sum(e["hits"] for e in evals)
    total_pred = len(evals) * PLACES
    avg_rate   = total_hits / total_pred * 100 if total_pred else 0

    print(f"\n{'═'*85}")
    print(f"  RACE DAY SUMMARY  ({len(evals)} races evaluated)")
    print(f"{'─'*85}")
    print(f"  Total correct : {total_hits} / {total_pred}")
    print(f"  Avg hit rate  : {avg_rate:.1f}%   (target ≥50%)")
    print(f"{'─'*85}")

    for ev in evals:
        bar  = "█" * ev["hits"]
        miss = "░" * (PLACES - ev["hits"])
        hits_str     = ', '.join('#' + h for h in ev['hit_horses'])     or '─'
        surprise_str = ', '.join('#' + h for h in ev['surprise_horses']) or '─'
        print(
            f"  Race {ev['race_no']:>2}  {bar}{miss}  {ev['hits']}/{PLACES}  "
            f"✅ {hits_str:<20}  😮 {surprise_str}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run(race_date: str, venue: str, show_log: bool = False):
    if show_log:
        print_log_summary()
        return

    print(f"\n{'═'*85}")
    print(f"  POST-RACE REVIEW  |  {race_date}  |  {venue}")
    print(f"{'═'*85}")

    # Load predictions
    date_str  = race_date.replace("/", "-")
    json_path = Path(OUTPUT_DIR) / f"predictions_{date_str}_{venue}.json"
    if not json_path.exists():
        print(f"  ✗ No predictions at {json_path}. Run predict.py first.")
        sys.exit(1)
    with open(json_path) as f:
        raw = json.load(f)
    predictions = {
        item["race_no"]: pd.DataFrame(item["horses"]) for item in raw
    }

    # Fetch results
    print(f"  Fetching official results...")
    results = fetch_results(race_date, venue)
    if not results:
        sys.exit(1)

    # Evaluate each race
    evals = evaluate_day(predictions, results)
    for ev in evals:
        rno = ev["race_no"]
        print_review_table(ev, predictions[rno], results[rno])

    # Day summary
    print_day_summary(evals)

    # Append to backtest log
    append_to_log(evals, race_date, venue, predictions, results)
    print(f"\n  ✓ Appended to backtest log → {BACKTEST_LOG}")

    # Show updated cumulative stats
    print_log_summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Post-Race Review")
    parser.add_argument("--date",     default=datetime.now().strftime("%Y/%m/%d"),
                        help="Race date YYYY/MM/DD")
    parser.add_argument("--venue",    default="ST", choices=["ST", "HV"],
                        help="Venue ST or HV")
    parser.add_argument("--show-log", action="store_true",
                        help="Print cumulative backtest log only")
    args = parser.parse_args()
    run(args.date, args.venue, args.show_log)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_float(s) -> float:
    try:    return float(re.sub(r"[^\d.]", "", str(s)))
    except: return 0.0

def _parse_race_no(block) -> int:
    txt = " ".join(block.get("class", [])) + " " + block.get("id", "")
    m   = re.search(r"(\d+)", txt)
    return int(m.group(1)) if m else 0
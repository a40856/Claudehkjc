"""
live_odds.py — Live Odds Fetcher & Comparison
===============================================
Run DURING a race day to pull live HKJC win odds and compare
against the model's calculated odds.

Usage:
    python live_odds.py                              # today, ST
    python live_odds.py --date 2026/03/22 --venue HV
    python live_odds.py --date 2026/03/22 --venue HV --race 3
    python live_odds.py --date 2026/03/22 --venue ST --watch
"""

import argparse, json, os, re, sys, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import URLS, HEADERS, OUTPUT_DIR, PLACES

os.makedirs(OUTPUT_DIR, exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LIVE ODDS FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_live_odds(race_date: str, venue: str, race_no: int = 0) -> dict:
    """
    Fetch live win odds from HKJC for all races (or one specific race).
    Returns: {race_no: {horse_no: live_odds, ...}}
    Tries JSON API first, falls back to HTML scrape.
    """
    live = {}

    # ── Try HKJC JSON odds API ────────────────────────────────────────────────
    try:
        params = {
            "type":       "winplaodds",
            "date":       race_date.replace("/", ""),
            "Racecourse": venue,
        }
        if race_no:
            params["RaceNo"] = race_no

        resp = SESSION.get(URLS["odds_api"], params=params, timeout=10)
        if resp.status_code == 200 and resp.text.strip().startswith("{"):
            data = resp.json()
            for item in data.get("oddsNodes", []):
                rno  = int(item.get("raceNo", 0))
                hno  = str(item.get("horseNo", ""))
                odds = float(item.get("winOdds", 0))
                if rno and hno and odds:
                    live.setdefault(rno, {})[hno] = odds
            if live:
                print(f"  ✓ Live odds via JSON API: {len(live)} race(s)")
                return live
    except Exception as e:
        print(f"  ⚠ JSON API attempt: {e}")

    # ── Fallback: scrape HKJC bet page ───────────────────────────────────────
    try:
        params = {"date": race_date, "venue": venue}
        resp   = SESSION.get(URLS["live_odds_win"], params=params, timeout=10)
        resp.raise_for_status()
        soup   = BeautifulSoup(resp.text, "html.parser")

        for block in soup.select(".oddsTable, table.winOdds"):
            rno = _parse_race_no_from_block(block)
            if race_no and rno != race_no:
                continue
            for row in block.select("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.select("td")]
                if len(cells) >= 3:
                    hno  = cells[0]
                    odds = _safe_float(cells[2])
                    if odds:
                        live.setdefault(rno, {})[hno] = odds

        if live:
            print(f"  ✓ Live odds via HTML scrape: {len(live)} race(s)")
    except Exception as e:
        print(f"  ⚠ HTML scrape attempt: {e}")

    return live


def fetch_market_favourites(race_date: str, venue: str) -> dict:
    """
    Return HKJC market top-3 per race sorted by win odds.
    Returns: {race_no: [(horse_no, odds), ...]}
    """
    all_odds = fetch_live_odds(race_date, venue)
    favs = {}
    for rno, odds_map in all_odds.items():
        sorted_odds = sorted(odds_map.items(), key=lambda x: x[1])
        favs[rno]   = sorted_odds[:3]
    return favs


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LOAD PREDICTIONS & MERGE
# ═══════════════════════════════════════════════════════════════════════════════

def load_predictions(race_date: str, venue: str) -> dict:
    """
    Load prediction JSON written by predict.py.
    Returns: {race_no: DataFrame}
    """
    date_str  = race_date.replace("/", "-")
    json_path = Path(OUTPUT_DIR) / f"predictions_{date_str}_{venue}.json"
    if not json_path.exists():
        raise FileNotFoundError(
            f"No predictions at {json_path}. Run predict.py first."
        )
    with open(json_path) as f:
        data = json.load(f)
    return {item["race_no"]: pd.DataFrame(item["horses"]) for item in data}


def merge_odds(pred_df: pd.DataFrame, live_odds_race: dict) -> pd.DataFrame:
    """
    Attach live_odds and value_flag columns to prediction DataFrame.
    value_flag:
        VALUE ⬆  — live odds >= calc_odds * 1.15  (market underestimates horse)
        SHORT ⬇  — live odds <= calc_odds * 0.85  (market overestimates horse)
        FAIR     — within 15% of calc odds
    """
    df = pred_df.copy()
    df["live_odds"] = df["horse_no"].astype(str).map(
        lambda x: live_odds_race.get(x, live_odds_race.get(str(x), None))
    )

    def value_flag(row):
        lo = row.get("live_odds")
        co = row.get("calc_odds", 0)
        if lo is None or pd.isna(lo) or lo == 0 or co == 0:
            return "─"
        ratio = lo / co
        if ratio >= 1.15:
            return "VALUE ⬆"
        elif ratio <= 0.85:
            return "SHORT ⬇"
        return "FAIR"

    df["value_flag"] = df.apply(value_flag, axis=1)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# 3. DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def print_comparison_table(df: pd.DataFrame, race_no: int,
                            race_name: str = ""):
    """Print side-by-side model calc vs live odds for one race."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'═'*85}")
    print(f"  RACE {race_no}  {race_name}   [live as at {ts}]")
    print(f"{'─'*85}")
    print(f"  {'Rnk':<4} {'#':<4} {'Horse':<22} {'WinProb':>8} "
          f"{'CalcOdds':>9} {'LiveOdds':>9} {'Diff':>7} {'Signal':>10}")
    print(f"  {'─'*80}")

    for i, row in df.iterrows():
        star     = "★" if i < PLACES else " "
        live_str = f"{row['live_odds']:.1f}x" \
                   if pd.notna(row.get("live_odds")) \
                   and row.get("live_odds") else "─"
        calc_str = f"{row['calc_odds']:.1f}x"
        diff_str = ""
        if (pd.notna(row.get("live_odds"))
                and row.get("live_odds")
                and row.get("calc_odds")):
            d        = row["live_odds"] - row["calc_odds"]
            diff_str = f"{'+' if d >= 0 else ''}{d:.1f}"

        print(
            f"  {star}{i+1:<3} #{str(row['horse_no']):<3} "
            f"{str(row['horse_name']):<22} {row['win_prob']:>7.1f}% "
            f"{calc_str:>9} {live_str:>9} {diff_str:>7}  "
            f"{row.get('value_flag', '─')}"
        )
    print(f"  ★ = top-{PLACES} prediction")


def print_summary_table(all_race_dfs: dict):
    """Print master top-4 summary table for all races."""
    print(f"\n{'═'*85}")
    print(f"  TOP-{PLACES} PREDICTIONS — ALL RACES SUMMARY")
    print(f"{'─'*85}")
    print(f"  {'Race':<6} {'Rnk':<5} {'#':<4} {'Horse':<22} "
          f"{'WinProb':>8} {'CalcOdds':>9} {'LiveOdds':>9} {'Signal':>10}")
    print(f"  {'─'*80}")

    for rno in sorted(all_race_dfs.keys()):
        df   = all_race_dfs[rno]
        top4 = df.head(PLACES)
        for i, (_, row) in enumerate(top4.iterrows()):
            live_str = f"{row['live_odds']:.1f}x" \
                       if pd.notna(row.get("live_odds")) \
                       and row.get("live_odds") else "─"
            print(
                f"  R{rno:<5} {i+1:<5} #{str(row['horse_no']):<3} "
                f"{str(row['horse_name']):<22} {row['win_prob']:>7.1f}% "
                f"{row['calc_odds']:>8.1f}x {live_str:>9}  "
                f"{row.get('value_flag', '─')}"
            )
        print(f"  {'─'*80}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run(race_date: str, venue: str, race_no: int = 0, watch: bool = False):
    interval = 60   # seconds between auto-refreshes in watch mode

    while True:
        print(f"\n{'═'*85}")
        print(f"  LIVE ODDS COMPARISON  |  {race_date}  |  {venue}  "
              f"|  {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'═'*85}")

        # Load saved predictions
        try:
            predictions = load_predictions(race_date, venue)
        except FileNotFoundError as e:
            print(f"  ✗ {e}")
            sys.exit(1)

        # Fetch live odds
        live = fetch_live_odds(race_date, venue, race_no)
        if not live:
            print("  ⚠ No live odds returned — market may not be open yet.")

        # Merge and display
        races_to_show = [race_no] if race_no else sorted(predictions.keys())
        updated_dfs   = {}

        for rno in races_to_show:
            if rno not in predictions:
                continue
            live_race       = live.get(rno, {})
            df              = merge_odds(predictions[rno], live_race)
            updated_dfs[rno] = df
            print_comparison_table(df, rno)

        if len(races_to_show) > 1:
            print_summary_table(updated_dfs)

        # Save snapshot with live odds attached
        date_str  = race_date.replace("/", "-")
        live_path = Path(OUTPUT_DIR) / f"live_{date_str}_{venue}.json"
        save_data = {
            str(rno): df.to_dict(orient="records")
            for rno, df in updated_dfs.items()
        }
        with open(live_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, default=str)
        print(f"\n  ✓ Snapshot saved → {live_path}")

        if not watch:
            break
        print(f"\n  [Auto-refresh in {interval}s — Ctrl+C to stop]")
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Live Odds Comparison")
    parser.add_argument("--date",  default=datetime.now().strftime("%Y/%m/%d"),
                        help="Race date YYYY/MM/DD")
    parser.add_argument("--venue", default="ST", choices=["ST", "HV"],
                        help="Venue ST or HV")
    parser.add_argument("--race",  type=int, default=0,
                        help="Race number (0 = all races)")
    parser.add_argument("--watch", action="store_true",
                        help="Auto-refresh every 60 seconds")
    args = parser.parse_args()
    run(args.date, args.venue, args.race, args.watch)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_float(s) -> float:
    try:    return float(re.sub(r"[^\d.]", "", str(s)))
    except: return 0.0

def _parse_race_no_from_block(block) -> int:
    txt = " ".join(block.get("class", [])) + " " + block.get("id", "")
    m   = re.search(r"(\d+)", txt)
    return int(m.group(1)) if m else 0
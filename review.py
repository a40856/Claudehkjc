"""
review.py — Post-Race Review, Full Results & Dividends Capture, Backtest Logger
================================================================================
Run ~65 minutes after the last race on race day.

What it does:
  1. Fetches official finishing results for every race (full column set)
  2. Captures COMPLETE dividends table — all 9 pool types per race:
       WIN, PLACE, QUINELLA, QUINELLA PLACE, FORECAST,
       TIERCE, TRIO, FIRST 4, QUARTET
  3. Parses race metadata: going, course, race time, sectional times
  4. Compares predictions vs actual top-4 → hits / hit_rate
  5. Saves:
       results_<date>_<venue>.json        — full race results per race
       dividends_<date>_<venue>.json      — all pools all races
       combined_<date>_<venue>.csv        — merged pred + result per horse
       backtest_log.csv                   — cumulative accuracy log

Usage:
    python review.py --date 2026/03/22 --venue ST
    python review.py --date 2026/03/25 --venue HV
    python review.py --show-log
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import (
    URLS, HEADERS, PLACES, BACKTEST_LOG, session_dirs
)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(s) -> float:
    try:    return float(re.sub(r"[^\d.]", "", str(s).replace(",", "")))
    except: return 0.0

def _safe_int(s) -> int:
    try:    return int(re.sub(r"\D", "", str(s)))
    except: return 0

def _placing_int(p):
    try:
        v = int(str(p).strip())
        return v if 1 <= v <= 20 else None
    except: return None

# Pool name normalisation — maps HKJC page text → canonical key
POOL_ALIASES = {
    "WIN":              "WIN",
    "PLACE":            "PLACE",
    "QUINELLA":         "QUINELLA",
    "QUINELLA PLACE":   "QUINELLA PLACE",
    "QP":               "QUINELLA PLACE",
    "Q":                "QUINELLA",
    "FORECAST":         "FORECAST",
    "F/CAST":           "FORECAST",
    "TIERCE":           "TIERCE",
    "TRIO":             "TRIO",
    "FIRST 4":          "FIRST 4",
    "FIRST4":           "FIRST 4",
    "1ST 4":            "FIRST 4",
    "QUARTET":          "QUARTET",
    "DOUBLE":           "DOUBLE",
    "DBL":              "DOUBLE",
    "TREBLE":           "TREBLE",
    "TBL":              "TREBLE",
    "TRIPLE TRIO":      "TRIPLE TRIO",
    "TT":               "TRIPLE TRIO",
    "SIX UP":           "SIX UP",
    "DOUBLE TRIO":      "DOUBLE TRIO",
    "JOCKEY CHALLENGE": "JOCKEY CHALLENGE",
}

ALL_POOLS = [
    "WIN", "PLACE", "QUINELLA", "QUINELLA PLACE",
    "FORECAST", "TIERCE", "TRIO", "FIRST 4", "QUARTET",
    "DOUBLE", "TREBLE", "TRIPLE TRIO",
]

# ═══════════════════════════════════════════════════════════════════════════════
# 1. FETCH OFFICIAL RESULTS  (full column set matching HKJC page)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_results(race_date: str, venue: str, dirs: dict) -> dict:
    """
    Fetch full race result data from HKJC ResultsAll page.
    Cached permanently (results never change).

    Each race returns:
      metadata  — going, course, prize, time splits, sectional times
      finishers — full column set per horse:
                  place, horse_no, horse_name, horse_code, horse_id,
                  jockey, trainer, actual_wt, decl_horse_wt, draw,
                  lbw, running_position, finish_time, win_odds
    """
    tag        = race_date.replace("/", "-")
    cache_path = dirs["results"] / f"results_{tag}_{venue}.json"
    html_path  = dirs["results"] / f"results_{tag}_{venue}.html"

    if cache_path.exists():
        print("   Using cached results.")
        with open(cache_path) as f:
            return {int(k): v for k, v in json.load(f).items()}

    print("   Fetching results from HKJC...")
    results = {}

    # Primary: HKJC ResultsAll
    try:
        params = {"RaceDate": race_date, "Racecourse": venue}
        resp   = SESSION.get(URLS["race_results"], params=params, timeout=20)
        resp.raise_for_status()
        html_path.write_text(resp.text, encoding="utf-8")
        soup    = BeautifulSoup(resp.text, "html.parser")
        results = _parse_results_page(soup)
        if results:
            print(f"   ✓ Results (HKJC): {len(results)} races parsed")
    except Exception as e:
        print(f"   ⚠ HKJC results error: {e}")

    # Fallback: SCMP
    if not results:
        print("   Trying SCMP fallback...")
        dc = race_date.replace("/", "")
        for rno in range(1, 13):
            try:
                resp = SESSION.get(
                    f"{URLS['scmp_result']}/{dc}/{rno}", timeout=10)
                if resp.status_code != 200:
                    break
                soup = BeautifulSoup(resp.text, "html.parser")
                race = _parse_scmp_race(soup, rno)
                if race:
                    results[rno] = race
            except Exception:
                break
        if results:
            print(f"   ✓ Results (SCMP): {len(results)} races")

    if not results:
        print("   ✗ Could not fetch results from any source.")
        return {}

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return results


def _parse_results_page(soup: BeautifulSoup) -> dict:
    results = {}
    for block in soup.select(
            ".raceResult, .resultTable, [class*='raceDetail'], [id*='raceResult']"):
        rno = _parse_race_no_from_block(block)
        if not rno:
            continue
        results[rno] = {
            "race_no":   rno,
            "metadata":  _parse_race_metadata(block),
            "finishers": _parse_finishers(block),
        }
    return results


def _parse_race_metadata(block) -> dict:
    """
    Extract from each race block:
      class_, distance, going, course,
      time_splits [(24.67)(47.53)(1:11.45)(1:34.71)],
      sectional_times [24.67, 22.86, 23.92, 23.26],
      prize_hkd
    """
    text = block.get_text(" ", strip=True)
    meta = {}

    cm = re.search(r"CLASS\s*([1-5])", text, re.I)
    meta["class_"] = f"CLASS {cm.group(1)}" if cm else ""

    dm = re.search(r"(\d{3,4})\s*[Mm]", text)
    meta["distance"] = int(dm.group(1)) if dm else 0

    gm = re.search(r"Going\s*[:\s]+([A-Z /]+)", text, re.I)
    meta["going"] = gm.group(1).strip() if gm else ""

    co = re.search(r"Course\s*[:\s]+(.+?)(?:Time|\n|$)", text, re.I)
    meta["course"] = co.group(1).strip() if co else ""

    meta["time_splits"]     = re.findall(r"\(([\d:.]+)\)", text)

    sm = re.search(r"Sectional Time[:\s]+([\d.\s]+)", text, re.I)
    meta["sectional_times"] = re.findall(r"[\d]+\.[\d]+", sm.group(1)) if sm else []

    pm = re.search(r"HK\$\s*([\d,]+)", text)
    meta["prize_hkd"] = pm.group(1).replace(",", "") if pm else ""

    return meta


def _parse_finishers(block) -> list:
    """
    Full column set from HKJC result table (matches image exactly):
      Pla | Horse No | Horse (Code) | Jockey | Trainer |
      Act.Wt | Declar.HorseWt | Dr | LBW | Running Position |
      Finish Time | Win Odds
    """
    finishers = []
    table = block.select_one("table")
    if not table:
        return finishers

    for row in table.select("tr"):
        cells = [td.get_text(" ", strip=True) for td in row.select("td")]
        if len(cells) < 5:
            continue
        placing_raw = re.sub(r"\D", "", cells[0])
        if not placing_raw or not placing_raw.isdigit():
            continue
        placing = int(placing_raw)
        if placing < 1 or placing > 20:
            continue

        horse_id = ""
        link = row.select_one("a[href*='HorseId'], a[href*='horse']")
        if link:
            hm = re.search(r"HorseId=([A-Z0-9]+)", link.get("href",""), re.I)
            if hm:
                horse_id = hm.group(1)

        horse_raw  = cells[2] if len(cells) > 2 else ""
        horse_name = re.sub(r"\s*\([^)]*\)", "", horse_raw).strip()
        horse_code = re.search(r"\(([^)]+)\)", horse_raw)
        horse_code = horse_code.group(1) if horse_code else ""

        finishers.append({
            "place":           placing,
            "horse_no":        str(cells[1]).strip(),
            "horse_name":      horse_name,
            "horse_code":      horse_code,       # e.g. L044, K526
            "horse_id":        horse_id,
            "jockey":          cells[3].strip()  if len(cells) > 3  else "",
            "trainer":         cells[4].strip()  if len(cells) > 4  else "",
            "actual_wt":       _safe_int(cells[5])   if len(cells) > 5  else 0,
            "decl_horse_wt":   _safe_int(cells[6])   if len(cells) > 6  else 0,
            "draw":            _safe_int(cells[7])   if len(cells) > 7  else 0,
            "lbw":             cells[8].strip()  if len(cells) > 8  else "",
            "running_position":cells[9].strip()  if len(cells) > 9  else "",
            "finish_time":     cells[10].strip() if len(cells) > 10 else "",
            "win_odds":        _safe_float(cells[11]) if len(cells) > 11 else 0.0,
        })
    return finishers


def _parse_scmp_race(soup, rno: int) -> dict:
    """Lightweight SCMP fallback — fewer columns available."""
    finishers = []
    for row in soup.select("table tr"):
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) >= 4 and re.match(r"^\d+$", cells[0]):
            finishers.append({
                "place": int(cells[0]), "horse_no": str(cells[1]),
                "horse_name": cells[2], "horse_code": "", "horse_id": "",
                "jockey": cells[3] if len(cells) > 3 else "",
                "trainer": cells[4] if len(cells) > 4 else "",
                "actual_wt": 0, "decl_horse_wt": 0, "draw": 0,
                "lbw": cells[5] if len(cells) > 5 else "",
                "running_position": "",
                "finish_time": cells[6] if len(cells) > 6 else "",
                "win_odds": _safe_float(cells[-1]),
            })
    return {"race_no": rno, "metadata": {}, "finishers": finishers} if finishers else None


def _parse_race_no_from_block(tag) -> int:
    text = tag.get_text(" ", strip=True)
    for pat in [r"RACE\s+(\d+)", r"race[\s_-]?no[\s:]*?(\d+)", r"R(\d+)\b"]:
        m = re.search(pat, text, re.I)
        if m:
            return int(m.group(1))
    for cls in tag.get("class", []):
        m = re.search(r"(\d+)", cls)
        if m:
            return int(m.group(1))
    return 0

# ═══════════════════════════════════════════════════════════════════════════════
# 2. FETCH FULL DIVIDENDS TABLE
#    WIN / PLACE / QUINELLA / QUINELLA PLACE / FORECAST /
#    TIERCE / TRIO / FIRST 4 / QUARTET / DOUBLE / TREBLE / TRIPLE TRIO
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_dividends(race_date: str, venue: str, dirs: dict) -> dict:
    """
    Scrape HKJC dividends page — all pool types per race. Cached permanently.

    Returns:
      {
        race_no: {
          "WIN":            [{"combo": "8",        "dividend": 73.0}],
          "PLACE":          [{"combo": "8",        "dividend": 23.5},
                             {"combo": "10",       "dividend": 56.5},
                             {"combo": "6",        "dividend": 21.0}],
          "QUINELLA":       [{"combo": "8,10",     "dividend": 872.5}],
          "QUINELLA PLACE": [{"combo": "8,10",     "dividend": 248.0},
                             {"combo": "6,8",      "dividend": 93.0},
                             {"combo": "6,10",     "dividend": 183.0}],
          "FORECAST":       [{"combo": "8,10",     "dividend": 1534.0}],
          "TIERCE":         [{"combo": "8,10,6",   "dividend": 12112.0}],
          "TRIO":           [{"combo": "6,8,10",   "dividend": 1887.0}],
          "FIRST 4":        [{"combo": "6,8,9,10", "dividend": 32908.0}],
          "QUARTET":        [{"combo": "8,10,6,9", "dividend": 529175.0}],
        }
      }
    """
    tag        = race_date.replace("/", "-")
    cache_path = dirs["results"] / f"dividends_{tag}_{venue}.json"
    html_path  = dirs["results"] / f"dividends_{tag}_{venue}.html"

    if cache_path.exists():
        print("   Using cached dividends.")
        with open(cache_path) as f:
            return {int(k): v for k, v in json.load(f).items()}

    print("   Fetching dividends from HKJC...")
    dividends = {}

    try:
        params = {"RaceDate": race_date, "Racecourse": venue}
        resp   = SESSION.get(URLS["dividends"], params=params, timeout=20)
        resp.raise_for_status()
        html_path.write_text(resp.text, encoding="utf-8")
        soup      = BeautifulSoup(resp.text, "html.parser")
        dividends = _parse_dividends_page(soup)
        if dividends:
            total = sum(sum(len(v) for v in p.values())
                        for p in dividends.values())
            print(f"   ✓ Dividends: {len(dividends)} races, {total} pool entries")
        else:
            print("   ⚠ No dividend data yet — HTML saved for inspection")
    except Exception as e:
        print(f"   ⚠ Dividends error: {e}")

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(dividends, f, ensure_ascii=False, indent=2)
    return dividends


def _parse_dividends_page(soup: BeautifulSoup) -> dict:
    """
    Walk every table row on the dividends page.
      Row type A — race header  : contains "RACE N"
      Row type B — pool header  : single cell e.g. "WIN", "QUINELLA PLACE"
      Row type C — data row     : [combo]  [HK$ amount]
    """
    dividends    = {}
    race_no      = 0
    current_pool = None

    for row in soup.select("table tr"):
        cells    = [td.get_text(" ", strip=True) for td in row.select("td, th")]
        if not cells:
            continue
        row_text = " ".join(cells).strip()

        # ── Race header ───────────────────────────────────────────────────────
        rm = re.search(r"RACE\s+(\d+)", row_text, re.I)
        if rm:
            race_no      = int(rm.group(1))
            current_pool = None
            if race_no not in dividends:
                dividends[race_no] = {p: [] for p in ALL_POOLS}
            continue

        if race_no == 0:
            continue

        # ── Pool header ───────────────────────────────────────────────────────
        if len(cells) <= 2 and not re.search(r"[\d,]+\.\d{2}", row_text):
            cand = cells[0].strip().upper()
            for alias, canonical in POOL_ALIASES.items():
                if alias in cand:
                    current_pool = canonical
                    if current_pool not in dividends[race_no]:
                        dividends[race_no][current_pool] = []
                    break
            continue

        # ── Data row  [combo]  [dividend] ────────────────────────────────────
        if current_pool and len(cells) >= 2:
            combo_raw = cells[0].strip()
            div_raw   = cells[1].strip()
            if re.search(r"[\d,]+\.\d{2}", div_raw) or re.search(r"^[\d,]+$", div_raw):
                dividend = _safe_float(div_raw)
                if dividend > 0 and combo_raw:
                    dividends[race_no][current_pool].append({
                        "combo":    combo_raw,
                        "dividend": dividend,
                    })

    # Remove empty entries
    return {
        rno: {pool: entries for pool, entries in pools.items() if entries}
        for rno, pools in dividends.items()
        if any(pools.values())
    }

# ═══════════════════════════════════════════════════════════════════════════════
# 3. EVALUATE PREDICTIONS VS RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_race(pred_df: pd.DataFrame, race_data: dict) -> dict:
    finishers   = race_data.get("finishers", [])
    pred_top4   = pred_df.head(PLACES)["horse_no"].astype(str).tolist()
    actual_top4 = [str(r["horse_no"]) for r in finishers if r["place"] <= PLACES]
    pred_set    = set(pred_top4)
    actual_set  = set(actual_top4)
    hits        = len(pred_set & actual_set)
    return {
        "pred_top4":   pred_top4, "actual_top4": actual_top4,
        "pred_set":    pred_set,  "actual_set":  actual_set,
        "hits":        hits,      "hit_rate":    round(hits / PLACES, 3),
        "correct":     sorted(pred_set & actual_set),
        "missed":      sorted(pred_set - actual_set),
        "surprise":    sorted(actual_set - pred_set),
    }


def evaluate_day(predictions: dict, results: dict) -> list:
    evals = []
    for rno in sorted(predictions.keys()):
        if rno not in results:
            print(f"   ⚠ Race {rno}: no result found — skipping")
            continue
        ev = evaluate_race(predictions[rno], results[rno])
        ev["race_no"] = rno
        evals.append(ev)
    return evals

# ═══════════════════════════════════════════════════════════════════════════════
# 4. BACKTEST LOG
# ═══════════════════════════════════════════════════════════════════════════════

LOG_COLUMNS = [
    "date", "venue", "race_no",
    "pred_1", "pred_2", "pred_3", "pred_4",
    "actual_1", "actual_2", "actual_3", "actual_4",
    "hits", "hit_rate",
    "correct_horses", "missed_horses", "surprise_horses", "model",
]

def append_to_log(evals: list, race_date: str, venue: str):
    file_exists = BACKTEST_LOG.exists()
    with open(BACKTEST_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if not file_exists:
            writer.writeheader()
        for ev in evals:
            writer.writerow({
                "date": race_date, "venue": venue, "race_no": ev["race_no"],
                "pred_1":  ev["pred_top4"][0] if len(ev["pred_top4"]) > 0 else "",
                "pred_2":  ev["pred_top4"][1] if len(ev["pred_top4"]) > 1 else "",
                "pred_3":  ev["pred_top4"][2] if len(ev["pred_top4"]) > 2 else "",
                "pred_4":  ev["pred_top4"][3] if len(ev["pred_top4"]) > 3 else "",
                "actual_1":ev["actual_top4"][0] if len(ev["actual_top4"]) > 0 else "",
                "actual_2":ev["actual_top4"][1] if len(ev["actual_top4"]) > 1 else "",
                "actual_3":ev["actual_top4"][2] if len(ev["actual_top4"]) > 2 else "",
                "actual_4":ev["actual_top4"][3] if len(ev["actual_top4"]) > 3 else "",
                "hits":            ev["hits"],
                "hit_rate":        ev["hit_rate"],
                "correct_horses":  " ".join(f"#{h}" for h in ev["correct"]),
                "missed_horses":   " ".join(f"#{h}" for h in ev["missed"]),
                "surprise_horses": " ".join(f"#{h}" for h in ev["surprise"]),
                "model":           "OptionA",
            })
    print(f"   ✓ Backtest log updated → {BACKTEST_LOG}")


def show_backtest_log():
    if not BACKTEST_LOG.exists():
        print("  No backtest data yet.")
        return
    df = pd.read_csv(BACKTEST_LOG)
    if df.empty:
        print("  Backtest log is empty.")
        return
    print(f"\n  {'═'*70}")
    print(f"  BACKTEST LOG — {len(df)} races across {df['date'].nunique()} days")
    print(f"  {'═'*70}")
    print(f"  Overall top-{PLACES} hit rate : {df['hit_rate'].mean()*100:.1f}%")
    for venue in ["ST", "HV"]:
        sub = df[df["venue"] == venue]
        if not sub.empty:
            print(f"  {venue} avg hit rate       : "
                  f"{sub['hit_rate'].mean()*100:.1f}%  ({len(sub)} races)")
    print(f"\n  Hit distribution:")
    for h in [4, 3, 2, 1, 0]:
        cnt = (df["hits"] == h).sum()
        pct = cnt / len(df) * 100
        print(f"  {h}/{PLACES}  {cnt:>4} races  ({pct:5.1f}%)  "
              f"{'█' * int(pct/2)}")
    print(f"\n  By race day:")
    for (date, venue), acc in (
            df.groupby(["date", "venue"])["hit_rate"].mean() * 100).items():
        print(f"  {date}  {venue}  {acc:5.1f}%  {'█'*int(acc/5)}")

# ═══════════════════════════════════════════════════════════════════════════════
# 5. SAVE COMBINED CSV
# ═══════════════════════════════════════════════════════════════════════════════

def save_combined(evals, predictions, results, dividends,
                  race_date, venue, dirs) -> Path:
    """
    Per-horse row with ALL data merged:
      identity + actual result + race metadata +
      prediction scores + all dividend pools
    """
    tag  = race_date.replace("/", "-")
    path = dirs["combined"] / f"combined_{tag}_{venue}.csv"
    rows = []

    for ev in evals:
        rno       = ev["race_no"]
        pred_df   = predictions[rno]
        race_res  = results.get(rno, {})
        finishers = race_res.get("finishers", [])
        meta      = race_res.get("metadata", {})
        div       = dividends.get(rno, {})

        def _all_div(pool):
            entries = div.get(pool, [])
            return " | ".join(
                f"{e['combo']} @ ${e['dividend']:,.0f}" for e in entries
            ) if entries else ""

        for h in finishers:
            hno      = str(h["horse_no"])
            pred_row = pred_df[pred_df["horse_no"].astype(str) == hno]
            rows.append({
                "date": race_date, "venue": venue, "race_no": rno,
                "horse_no":      hno,
                "horse_name":    h["horse_name"],
                "horse_code":    h.get("horse_code", ""),
                "jockey":        h.get("jockey", ""),
                "trainer":       h.get("trainer", ""),
                "actual_place":  h["place"],
                "actual_wt":     h.get("actual_wt", 0),
                "decl_horse_wt": h.get("decl_horse_wt", 0),
                "draw":          h.get("draw", 0),
                "lbw":           h.get("lbw", ""),
                "running_pos":   h.get("running_position", ""),
                "finish_time":   h.get("finish_time", ""),
                "final_odds":    h.get("win_odds", 0.0),
                "going":         meta.get("going", ""),
                "course":        meta.get("course", ""),
                "distance":      meta.get("distance", 0),
                "prize_hkd":     meta.get("prize_hkd", ""),
                "our_rank":      int(pred_row.index[0]) + 1
                                 if not pred_row.empty else None,
                "composite":     round(pred_row.iloc[0]["composite"], 4)
                                 if not pred_row.empty else None,
                "win_prob":      pred_row.iloc[0]["win_prob"]
                                 if not pred_row.empty else None,
                "calc_odds":     pred_row.iloc[0]["calc_odds"]
                                 if not pred_row.empty else None,
                "in_our_top4":   hno in ev["pred_set"],
                "in_actual_top4":hno in ev["actual_set"],
                # All dividend pools
                "div_WIN":        _all_div("WIN"),
                "div_PLACE":      _all_div("PLACE"),
                "div_QUINELLA":   _all_div("QUINELLA"),
                "div_QP":         _all_div("QUINELLA PLACE"),
                "div_FORECAST":   _all_div("FORECAST"),
                "div_TIERCE":     _all_div("TIERCE"),
                "div_TRIO":       _all_div("TRIO"),
                "div_FIRST4":     _all_div("FIRST 4"),
                "div_QUARTET":    _all_div("QUARTET"),
                "div_DOUBLE":     _all_div("DOUBLE"),
                "div_TREBLE":     _all_div("TREBLE"),
                "div_TRIPLE_TRIO":_all_div("TRIPLE TRIO"),
            })

    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
    print(f"   ✓ Combined data → {path}")
    return path

# ═══════════════════════════════════════════════════════════════════════════════
# 6. DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def print_race_review(ev: dict, results: dict, dividends: dict):
    rno       = ev["race_no"]
    race_res  = results.get(rno, {})
    meta      = race_res.get("metadata", {})
    finishers = race_res.get("finishers", [])
    div       = dividends.get(rno, {})

    print(f"\n  RACE {rno}  {'─'*65}")
    if meta.get("going"):
        print(f"  {meta.get('distance','')}m  |  "
              f"Going: {meta.get('going','')}  |  "
              f"Course: {meta.get('course','')}")
    splits = "  ".join(f"({t})" for t in meta.get("time_splits", []))
    if splits:
        print(f"  Time: {splits}")
    sect = "  ".join(meta.get("sectional_times", []))
    if sect:
        print(f"  Sectional: {sect}")

    print(f"  Predicted  top-{PLACES} : "
          f"{'  '.join('#'+h for h in ev['pred_top4'])}")
    print(f"  Actual     top-{PLACES} : "
          f"{'  '.join('#'+h for h in ev['actual_top4'])}")
    bar = "█" * ev["hits"] + "░" * (PLACES - ev["hits"])
    print(f"  Hits : {ev['hits']}/{PLACES}  {bar}  ({ev['hit_rate']*100:.0f}%)")
    print(f"  ✅ Correct  : "
          f"{'  '.join('#'+h for h in ev['correct']) or '─'}")
    print(f"  ❌ Missed   : "
          f"{'  '.join('#'+h for h in ev['missed']) or '─'}")
    print(f"  😮 Surprise : "
          f"{'  '.join('#'+h for h in ev['surprise']) or '─'}")

    # Full finishing table
    if finishers:
        print(f"\n  {'Pla':<4} {'#':<4} {'Horse':<24} {'Jockey':<18} "
              f"{'Wt':<5} {'Dr':<4} {'LBW':<8} "
              f"{'Running Pos':<16} {'Time':<10} Odds")
        print(f"  {'─'*105}")
        for h in finishers:
            print(f"  {h['place']:<4} #{str(h['horse_no']):<3} "
                  f"{h['horse_name']:<24} {h['jockey']:<18} "
                  f"{h['actual_wt']:<5} {h['draw']:<4} "
                  f"{str(h['lbw']):<8} {h['running_position']:<16} "
                  f"{h['finish_time']:<10} {h['win_odds']}")

    # Full dividends table
    if div:
        print(f"\n  {'Pool':<20} {'Combination':<20} {'HK$':>12}")
        print(f"  {'─'*56}")
        for pool in ALL_POOLS:
            entries = div.get(pool, [])
            if not entries:
                continue
            for i, e in enumerate(entries):
                label = pool if i == 0 else ""
                print(f"  {label:<20} {e['combo']:<20} "
                      f"{e['dividend']:>12,.2f}")
        print()


def print_day_review(evals: list, race_date: str, venue: str):
    total_hits = sum(e["hits"] for e in evals)
    total_pred = len(evals) * PLACES
    avg        = total_hits / total_pred * 100 if total_pred else 0
    print(f"\n  {'═'*70}")
    print(f"  RACE DAY SUMMARY  |  {race_date}  {venue}  |  "
          f"{len(evals)} races  |  {total_hits}/{total_pred}  |  {avg:.1f}%")
    print(f"  {'─'*70}")
    for ev in evals:
        bar  = "█" * ev["hits"] + "░" * (PLACES - ev["hits"])
        corr = "  ".join("#"+h for h in ev["correct"]) or "─"
        surp = "  ".join("#"+h for h in ev["surprise"]) or "─"
        print(f"  R{ev['race_no']:>2}  {bar}  {ev['hits']}/{PLACES}  "
              f"✅ {corr:<20}  😮 {surp}")
    print(f"  {'═'*70}")

# ═══════════════════════════════════════════════════════════════════════════════
# 7. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run(race_date: str, venue: str, show_log: bool = False):
    if show_log:
        show_backtest_log()
        return

    dirs = session_dirs(race_date, venue)
    print(f"\n  {'═'*60}")
    print(f"  POST-RACE REVIEW")
    print(f"  Race date : {race_date}  |  Venue : {venue}")
    print(f"  Run time  : {datetime.now().strftime('%Y-%m-%d %H:%M HKT')}")
    print(f"  {'═'*60}")

    tag       = race_date.replace("/", "-")
    json_path = dirs["pred"] / f"predictions_{tag}_{venue}.json"
    if not json_path.exists():
        print(f"  ✗ No predictions found at {json_path}")
        print("    Run predict.py first.")
        sys.exit(1)
    with open(json_path) as f:
        raw = json.load(f)
    predictions = {
        item["race_no"]: pd.DataFrame(item["horses"])
        for item in raw
    }
    print(f"  Loaded predictions: {len(predictions)} races")

    print("\n  [1/3] Fetching official results (full table)...")
    results = fetch_results(race_date, venue, dirs)
    if not results:
        print("  ✗ Could not retrieve results. Try again later.")
        sys.exit(1)

    print("\n  [2/3] Fetching full dividends table...")
    dividends = fetch_dividends(race_date, venue, dirs)

    print("\n  [3/3] Evaluating predictions...")
    evals = evaluate_day(predictions, results)

    for ev in evals:
        print_race_review(ev, results, dividends)

    print_day_review(evals, race_date, venue)

    print("  Saving outputs...")
    append_to_log(evals, race_date, venue)
    save_combined(evals, predictions, results, dividends,
                  race_date, venue, dirs)
    show_backtest_log()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Post-Race Review")
    parser.add_argument("--date",     default=datetime.now().strftime("%Y/%m/%d"))
    parser.add_argument("--venue",    default="ST", choices=["ST", "HV"])
    parser.add_argument("--show-log", action="store_true")
    args = parser.parse_args()
    run(args.date, args.venue, args.show_log)

"""
predict.py — HKJC Race Prediction Engine
=========================================
Usage:
    python predict.py                        # today
    python predict.py --date 2026/03/22      # specific date
    python predict.py --date 2026/03/22 --venue HV
"""

import argparse, json, os, re, sys, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup

from config import (
    URLS, HEADERS, WEIGHTS_GENERAL, WEIGHTS_CLASSIC,
    CLASSIC_RACE_NAMES, JOCKEY_SCORES, TRAINER_SCORES,
    get_draw_bias, DATA_DIR, OUTPUT_DIR, PLACES, FORM_RUNS
)

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_race_card(race_date: str, venue: str) -> list:
    """
    Fetch full race card from HKJC for given date and venue.
    Returns list of race dicts, each containing race metadata + horses list.
    Caches result to DATA_DIR for 6 hours.
    """
    cache_path = Path(DATA_DIR) / f"card_{race_date.replace('/','-')}_{venue}.json"
    if cache_path.exists():
        if (time.time() - cache_path.stat().st_mtime) < 3600 * 6:
            with open(cache_path) as f:
                return json.load(f)

    params = {"RaceDate": race_date, "Racecourse": venue, "RaceNo": "all"}
    resp   = SESSION.get(URLS["race_card"], params=params, timeout=15)
    resp.raise_for_status()
    soup   = BeautifulSoup(resp.text, "html.parser")

    races = []
    for race_block in soup.select(".raceDetail, .race_info, table.drawtable"):
        race = _parse_race_block(race_block)
        if race:
            races.append(race)

    if not races:
        raise ValueError(
            f"No race card found for {race_date} at {venue}. "
            "Check date/venue or HKJC site structure may have changed."
        )

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(races, f, ensure_ascii=False, indent=2)
    return races


def _parse_race_block(block) -> dict:
    """Parse one race block from HKJC HTML. Returns None if unparseable."""
    try:
        rows   = block.select("tr")
        horses = []
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 8:
                horses.append({
                    "horse_no":   cells[0],
                    "horse_id":   "",
                    "horse_name": cells[1],
                    "draw":       int(cells[2]) if cells[2].isdigit() else 0,
                    "weight_lbs": int(cells[3]) if cells[3].isdigit() else 126,
                    "jockey":     cells[4],
                    "trainer":    cells[5],
                    "rating":     int(cells[6]) if cells[6].isdigit() else 50,
                    "last6_runs": "",
                    "gear":       cells[7] if len(cells) > 7 else "",
                    "win_odds":   20.0,
                })
        return {"race_no": 1, "race_name": "", "class_": "",
                "distance": 1200, "surface": "Turf",
                "horses": horses} if horses else None
    except Exception:
        return None


def fetch_horse_history(horse_id: str, horse_name: str = "") -> list:
    """
    Fetch individual horse past performance from HKJC horse profile page.
    Returns list of run dicts. Caches to DATA_DIR for 6 hours.
    """
    cache_path = Path(DATA_DIR) / f"horse_{horse_id}.json"
    if cache_path.exists():
        if (time.time() - cache_path.stat().st_mtime) < 3600 * 6:
            with open(cache_path) as f:
                return json.load(f)

    params = {"HorseId": horse_id}
    resp   = SESSION.get(URLS["horse_profile"], params=params, timeout=15)
    resp.raise_for_status()
    soup   = BeautifulSoup(resp.text, "html.parser")

    history = []
    for row in soup.select("table.horseProfile tr")[1:]:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) >= 12:
            history.append({
                "date":         cells[0],
                "race_no":      cells[1],
                "placing":      cells[2],
                "distance":     _safe_int(re.sub(r"\D", "", cells[3])),
                "surface":      cells[3],
                "going":        cells[4],
                "class_":       cells[5],
                "draw":         cells[6],
                "odds":         _safe_float(cells[7]),
                "jockey":       cells[8],
                "trainer":      cells[9],
                "weight":       _safe_int(cells[10]),
                "run_position": cells[11],
                "finish_time":  cells[12] if len(cells) > 12 else "",
            })

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return history


def fetch_jockey_stats(venue: str = "ALL") -> dict:
    """
    Fetch current-season jockey win rates from HKJC ranking page.
    Returns {name: {wins, rides, win_rate}}.
    Caches for 12 hours.
    """
    url_map    = {"ST": URLS["jockey_rank_st"], "HV": URLS["jockey_rank_hv"]}
    url        = url_map.get(venue, URLS["jockey_rank_st"])
    cache_path = Path(DATA_DIR) / f"jockey_stats_{venue}.json"
    if cache_path.exists():
        if (time.time() - cache_path.stat().st_mtime) < 3600 * 12:
            with open(cache_path) as f:
                return json.load(f)

    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        stats = {}
        for row in soup.select("table.rankingTable tr, table tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 6:
                name = cells[1]
                stats[name] = {
                    "wins":     _safe_int(cells[2]),
                    "seconds":  _safe_int(cells[3]),
                    "thirds":   _safe_int(cells[4]),
                    "rides":    _safe_int(cells[5]),
                    "win_rate": _safe_float(cells[6]) if len(cells) > 6 else 0.0,
                }
        if stats:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        return stats
    except Exception as e:
        print(f"  ⚠ Jockey stats fetch failed: {e} — using lookup table fallback")
        return {}


def fetch_trainer_stats(venue: str = "ALL") -> dict:
    """
    Fetch current-season trainer win rates from HKJC ranking page.
    Returns {name: {wins, rides, win_rate}}.
    Caches for 12 hours.
    """
    cache_path = Path(DATA_DIR) / f"trainer_stats_{venue}.json"
    if cache_path.exists():
        if (time.time() - cache_path.stat().st_mtime) < 3600 * 12:
            with open(cache_path) as f:
                return json.load(f)

    try:
        resp = SESSION.get(URLS["trainer_rank"], timeout=15)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        stats = {}
        for row in soup.select("table.rankingTable tr, table tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 5:
                name = cells[1]
                stats[name] = {
                    "wins":     _safe_int(cells[2]),
                    "rides":    _safe_int(cells[5]) if len(cells) > 5 else 0,
                    "win_rate": _safe_float(cells[6]) if len(cells) > 6 else 0.0,
                }
        if stats:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        return stats
    except Exception as e:
        print(f"  ⚠ Trainer stats fetch failed: {e} — using lookup table fallback")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SCORING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_form_score(last6: str) -> float:
    """Convert last-6-run string e.g. '8/12/3/1/2/4' to 0-10 score."""
    if not isinstance(last6, str) or "/" not in last6:
        return 5.0
    vals = []
    for p in last6.split("/"):
        try:
            v = int(re.sub(r"\D", "", p) or "99")
        except ValueError:
            v = 99
        vals.append(min(v, 20))
    vals = vals[-FORM_RUNS:]
    wts  = list(range(1, len(vals) + 1))
    score_map = {1:10,2:9,3:8,4:6,5:5,6:4,7:3,8:2,9:2,10:1,11:1,12:1}
    scored = [score_map.get(v, 1) * w for v, w in zip(vals, wts)]
    return round(sum(scored) / sum(wts), 2)


def compute_h2h_score(horse_id: str, field_ids: list,
                      history_cache: dict) -> float:
    """
    H2H score: across all shared previous races between horse_id and each
    opponent in today's field, compute win rate. Returns 0-10 score.
    5.0 = no shared history (neutral).
    """
    my_hist = history_cache.get(horse_id, [])
    if not my_hist:
        return 5.0

    my_dates = {h["date"]: h["placing"] for h in my_hist}
    wins, total = 0, 0

    for opp_id in field_ids:
        if opp_id == horse_id:
            continue
        opp_hist = history_cache.get(opp_id, [])
        for h in opp_hist:
            if h["date"] in my_dates:
                my_p  = _placing_int(my_dates[h["date"]])
                opp_p = _placing_int(h["placing"])
                if my_p and opp_p:
                    total += 1
                    if my_p < opp_p:
                        wins += 1

    if total == 0:
        return 5.0
    return round(min(10.0, max(0.0, (wins / total) * 10)), 2)


def compute_draw_score(stall: int, venue: str, surface: str,
                       distance: int, field_size: int) -> float:
    """Convert draw stall to 0-10 score using DRAW_BIAS lookup tables."""
    bias = get_draw_bias(venue, surface, distance, stall)
    return round(min(10.0, max(1.0, bias * 7.0)), 2)


def jockey_score(name: str, live_stats: dict) -> float:
    """Return 0-10 jockey score. Live stats override lookup table."""
    if name in live_stats:
        wr = live_stats[name].get("win_rate", 0) / 100.0
        return round(min(10.0, max(1.0, 1 + 9 * min(wr / 0.25, 1))), 2)
    return JOCKEY_SCORES.get(name, 6.5)


def trainer_score(name: str, live_stats: dict) -> float:
    """Return 0-10 trainer score. Live stats override lookup table."""
    if name in live_stats:
        wr = live_stats[name].get("win_rate", 0) / 100.0
        return round(min(10.0, max(1.0, 1 + 9 * min(wr / 0.22, 1))), 2)
    return TRAINER_SCORES.get(name, 6.5)


def score_field(race: dict, venue: str, jky_stats: dict, trn_stats: dict,
                history_cache: dict, weights: dict) -> pd.DataFrame:
    """
    Score all horses in one race.
    Returns DataFrame sorted by composite score descending.
    Columns: horse_no, horse_name, draw, rating, weight_lbs, odds,
             jockey, trainer, last6_runs, all s_* sub-scores,
             composite, win_prob, calc_odds.
    """
    horses   = race["horses"]
    surface  = race.get("surface", "Turf")
    distance = int(race.get("distance", 1200))
    n        = len(horses)

    rows = []
    for h in horses:
        rows.append({
            "horse_no":   str(h.get("horse_no", "")),
            "horse_id":   str(h.get("horse_id", "")),
            "horse_name": h.get("horse_name", ""),
            "draw":       int(h.get("draw", n // 2)),
            "rating":     float(h.get("rating", 50)),
            "weight_lbs": float(h.get("weight_lbs", 126)),
            "odds":       float(h.get("win_odds", 20)),
            "jockey":     h.get("jockey", ""),
            "trainer":    h.get("trainer", ""),
            "last6_runs": h.get("last6_runs", ""),
            "s_form":     compute_form_score(h.get("last6_runs", "")),
            "s_draw":     compute_draw_score(
                              int(h.get("draw", n // 2)),
                              venue, surface, distance, n),
            "s_jockey":   jockey_score(h.get("jockey", ""), jky_stats),
            "s_trainer":  trainer_score(h.get("trainer", ""), trn_stats),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    min_r, max_r = df["rating"].min(),     df["rating"].max()
    min_o, max_o = df["odds"].min(),       df["odds"].max()
    max_w, min_w = df["weight_lbs"].max(), df["weight_lbs"].min()

    df["s_rating"] = 1 + 9 * (df["rating"]     - min_r) / max(max_r - min_r, 1)
    df["s_market"] = 10 - 9 * (df["odds"]       - min_o) / max(max_o - min_o, 1)
    df["s_weight"] = 1 + 9 * (max_w - df["weight_lbs"]) / max(max_w - min_w, 1)

    field_ids      = df["horse_id"].tolist()
    df["s_h2h"]    = df["horse_id"].apply(
        lambda hid: compute_h2h_score(hid, field_ids, history_cache)
    )

    w = weights
    df["composite"] = (
        w.get("form",    0) * df["s_form"]    +
        w.get("rating",  0) * df["s_rating"]  +
        w.get("market",  0) * df["s_market"]  +
        w.get("draw",    0) * df["s_draw"]     +
        w.get("jockey",  0) * df["s_jockey"]  +
        w.get("trainer", 0) * df["s_trainer"] +
        w.get("h2h",     0) * df["s_h2h"]     +
        w.get("weight",  0) * df["s_weight"]
    )

    exp_s          = np.exp(df["composite"] - df["composite"].max())
    df["win_prob"] = (exp_s / exp_s.sum() * 100).round(1)
    df["calc_odds"]= (1.0 / (df["win_prob"] / 100) * 0.85).round(1)

    return df.sort_values("composite", ascending=False).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def print_race_table(df: pd.DataFrame, race: dict, race_no: int):
    """Print formatted prediction table for one race."""
    name = race.get("race_name", f"Race {race_no}")
    cls  = race.get("class_", "")
    dist = race.get("distance", "")
    surf = race.get("surface", "")
    n    = len(df)

    print(f"\n{'═'*85}")
    print(f"  RACE {race_no} | {name} | {cls} | {dist}m {surf} | {n} runners")
    print(f"{'─'*85}")
    print(f"  {'Rnk':<4} {'#':<4} {'Horse':<22} {'Draw':<5} "
          f"{'Form':>5} {'Rtg':>4} {'Jky':>4} {'Trn':>4} {'H2H':>4} "
          f"{'MktOdds':>8} {'Score':>6} {'WinProb':>8} {'CalcOdds':>9} {'LiveOdds':>9}")
    print(f"  {'─'*82}")

    for i, row in df.iterrows():
        star = "★" if i < PLACES else " "
        print(
            f"  {star}{i+1:<3} #{str(row['horse_no']):<3} "
            f"{str(row['horse_name']):<22} {row['draw']:<5} "
            f"{row['s_form']:>5.1f} {row['rating']:>4.0f} "
            f"{row['s_jockey']:>4.1f} {row['s_trainer']:>4.1f} "
            f"{row['s_h2h']:>4.1f} "
            f"{row['odds']:>8.1f} {row['composite']:>6.2f} "
            f"{row['win_prob']:>7.1f}% {row['calc_odds']:>8.1f}x "
            f"{'─':>9}"
        )
    print(f"  ★ = top-{PLACES} prediction")


def build_summary_table(all_results: list) -> pd.DataFrame:
    """Build top-4 summary DataFrame across all races."""
    rows = []
    for item in all_results:
        rno  = item["race_no"]
        df   = item["df"]
        top4 = df.head(PLACES)
        for rank, (_, row) in enumerate(top4.iterrows(), 1):
            rows.append({
                "Race":       rno,
                "Rank":       rank,
                "Horse No":   row["horse_no"],
                "Horse":      row["horse_name"],
                "Draw":       row["draw"],
                "Win Prob %": row["win_prob"],
                "Calc Odds":  row["calc_odds"],
                "Live Odds":  "─",
                "Jockey":     row["jockey"],
                "Last 6":     row["last6_runs"],
            })
    return pd.DataFrame(rows)


def save_predictions(all_results: list, race_date: str, venue: str):
    """Save predictions to JSON + CSV for live_odds.py and review.py."""
    date_str  = race_date.replace("/", "-")
    json_path = Path(OUTPUT_DIR) / f"predictions_{date_str}_{venue}.json"
    csv_path  = Path(OUTPUT_DIR) / f"predictions_{date_str}_{venue}.csv"

    out = []
    for item in all_results:
        out.append({
            "race_no":   item["race_no"],
            "race_name": item.get("race_name", ""),
            "horses":    item["df"].to_dict(orient="records"),
        })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    csv_rows = []
    for item in out:
        for h in item["horses"]:
            h["race_no"] = item["race_no"]
            csv_rows.append(h)
    pd.DataFrame(csv_rows).to_csv(csv_path, index=False, encoding="utf-8")

    print(f"\n  ✓ Predictions saved → {json_path}")
    print(f"  ✓ Predictions saved → {csv_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run(race_date: str, venue: str):
    print(f"\n{'═'*85}")
    print(f"  HKJC PREDICTION ENGINE  |  {race_date}  |  {venue}")
    print(f"{'═'*85}")

    print("  [1/5] Fetching race card...")
    races = fetch_race_card(race_date, venue)
    print(f"        → {len(races)} races found")

    print("  [2/5] Fetching jockey / trainer stats...")
    jky_stats = fetch_jockey_stats(venue)
    trn_stats = fetch_trainer_stats(venue)
    print(f"        → {len(jky_stats)} jockeys | {len(trn_stats)} trainers")

    print("  [3/5] Fetching horse histories (H2H)...")
    history_cache = {}
    for race in races:
        for h in race["horses"]:
            hid = h.get("horse_id", "")
            if hid and hid not in history_cache:
                try:
                    history_cache[hid] = fetch_horse_history(
                        hid, h.get("horse_name", ""))
                    time.sleep(0.3)
                except Exception as e:
                    print(f"        ⚠ {hid}: {e}")
                    history_cache[hid] = []
    print(f"        → {len(history_cache)} horses cached")

    print("  [4/5] Scoring and predicting...")
    all_results = []
    for race in races:
        rno  = race["race_no"]
        name = race.get("race_name", "").upper()
        w    = (WEIGHTS_CLASSIC
                if any(c in name for c in CLASSIC_RACE_NAMES)
                else WEIGHTS_GENERAL)
        df = score_field(race, venue, jky_stats, trn_stats, history_cache, w)
        all_results.append({"race_no": rno, "race_name": name, "df": df})
        print_race_table(df, race, rno)

    print(f"\n  [5/5] Top-{PLACES} Summary — All Races")
    print(f"{'═'*85}")
    summary = build_summary_table(all_results)
    print(summary.to_string(index=False))

    save_predictions(all_results, race_date, venue)
    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Race Predictor")
    parser.add_argument("--date",  default=datetime.now().strftime("%Y/%m/%d"),
                        help="Race date YYYY/MM/DD (default: today)")
    parser.add_argument("--venue", default="ST", choices=["ST", "HV"],
                        help="Venue ST or HV (default: ST)")
    args = parser.parse_args()
    run(args.date, args.venue)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_float(s) -> float:
    try:    return float(re.sub(r"[^\d.]", "", str(s)))
    except: return 0.0

def _safe_int(s) -> int:
    try:    return int(re.sub(r"\D", "", str(s)))
    except: return 0

def _placing_int(p):
    try:
        v = int(str(p).strip())
        return v if 1 <= v <= 20 else None
    except: return None
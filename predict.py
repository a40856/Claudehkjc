"""
predict.py — HKJC Race Prediction Engine
=========================================
Run the evening BEFORE race day at 18:00 HKT.
Fetches race card, horse histories, jockey/trainer stats,
scores all horses, and saves predictions to data/predictions/.

Usage:
    python predict.py --date 2026/03/25 --venue HV
    python predict.py --date 2026/03/29 --venue ST
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import (
    URLS, HEADERS, PLACES, FORM_RUNS,
    WEIGHTS_GENERAL, WEIGHTS_CLASSIC, CLASSIC_RACE_NAMES,
    JOCKEY_SCORES, JOCKEY_DEFAULT, TRAINER_SCORES, TRAINER_DEFAULT,
    get_draw_bias, session_dirs,
)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

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

# ═══════════════════════════════════════════════════════════════════════════════
# 1. FETCH RACE CARD
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_race_card(race_date: str, venue: str, dirs: dict) -> list:
    """
    Fetch race card from HKJC. Cached for 6 hours.
    Returns list of race dicts, each with a 'horses' list.
    """
    tag        = race_date.replace("/", "-")
    cache_json = dirs["raw"] / f"card_{tag}_{venue}.json"
    cache_html = dirs["raw"] / f"card_{tag}_{venue}.html"

    if cache_json.exists() and (time.time() - cache_json.stat().st_mtime) < 21600:
        print("   Using cached race card.")
        with open(cache_json) as f:
            return json.load(f)

    print(f"   Fetching race card from HKJC...")
    params = {"RaceDate": race_date, "Racecourse": venue, "RaceNo": "all"}
    resp   = SESSION.get(URLS["race_card"], params=params, timeout=20)
    resp.raise_for_status()
    cache_html.write_text(resp.text, encoding="utf-8")

    soup  = BeautifulSoup(resp.text, "html.parser")
    races = _parse_race_card(soup)

    if not races:
        raise ValueError(
            f"No races parsed for {race_date} {venue}.\n"
            f"Raw HTML saved to {cache_html}\n"
            "HKJC page structure may have changed — check the HTML."
        )

    with open(cache_json, "w", encoding="utf-8") as f:
        json.dump(races, f, ensure_ascii=False, indent=2)
    return races


def _parse_race_card(soup: BeautifulSoup) -> list:
    races  = []
    blocks = (
        soup.select(".raceCard, .race_card, .raceDetail")
        or soup.select("table.drawtable")
        or soup.select("[id^='raceResult'], [class*='race']")
    )
    for idx, block in enumerate(blocks, start=1):
        race = _parse_race_block(block, fallback_no=idx)
        if race and race["horses"]:
            races.append(race)
    return races


def _parse_race_block(block, fallback_no: int = 1) -> dict:
    try:
        rno  = fallback_no
        meta = block.get_text(" ", strip=True)
        for pat in [r"race[\s_-]?no[\s:]*?(\d+)", r"race\s*(\d+)"]:
            m = re.search(pat, meta, re.I)
            if m:
                rno = int(m.group(1))
                break

        distance = 1200
        surface  = "Turf"
        dm = re.search(r"(\d{3,4})\s*[Mm]", meta)
        if dm:
            distance = int(dm.group(1))
        if any(t in meta.upper() for t in ["AWT", "ALL WEATHER", "全天候"]):
            surface = "AWT"

        class_ = ""
        nm = re.search(r"(CLASS\s*[1-5]|G[123]|GRIFFIN|RESTRICTED)", meta, re.I)
        if nm:
            class_ = nm.group(0).upper()

        horses = []
        for row in block.select("tr"):
            cells = [td.get_text(strip=True) for td in row.select("td,th")]
            if len(cells) >= 7 and re.match(r"^\d{1,2}$", cells[0]):
                horses.append({
                    "horse_no":   cells[0],
                    "horse_id":   "",
                    "horse_name": cells[1],
                    "draw":       _safe_int(cells[2]),
                    "weight_lbs": _safe_int(cells[3]) or 126,
                    "jockey":     cells[4] if len(cells) > 4 else "",
                    "trainer":    cells[5] if len(cells) > 5 else "",
                    "rating":     _safe_int(cells[6]) if len(cells) > 6 else 50,
                    "last6_runs": cells[7] if len(cells) > 7 else "",
                    "gear":       cells[8] if len(cells) > 8 else "",
                    "win_odds":   _safe_float(cells[9]) if len(cells) > 9 else 0.0,
                })
                link = row.select_one("a[href*='HorseId'], a[href*='horse']")
                if link:
                    hm = re.search(r"HorseId=([A-Z0-9]+)",
                                   link.get("href", ""), re.I)
                    if hm:
                        horses[-1]["horse_id"] = hm.group(1)

        return {"race_no": rno, "race_name": "", "class_": class_,
                "distance": distance, "surface": surface,
                "horses": horses} if horses else None
    except Exception as e:
        print(f"   ⚠ Block parse error (race {fallback_no}): {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# 2. FETCH HORSE HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_horse_history(horse_id: str, horse_name: str, dirs: dict) -> list:
    """Fetch horse past performance. Cached for 6 hours."""
    if not horse_id:
        return []
    cache_path = dirs["raw"] / f"horse_{horse_id}.json"
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < 21600:
        with open(cache_path) as f:
            return json.load(f)
    try:
        resp = SESSION.get(URLS["horse_profile"],
                           params={"HorseId": horse_id}, timeout=15)
        resp.raise_for_status()
        soup    = BeautifulSoup(resp.text, "html.parser")
        history = []
        for row in soup.select("table.horseProfile tr, .pastPerformance tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 10:
                history.append({
                    "date":        cells[0],
                    "race_no":     cells[1],
                    "placing":     cells[2],
                    "distance":    _safe_int(re.sub(r"\D", "", cells[3])),
                    "surface":     "AWT" if "AWT" in cells[3].upper() else "Turf",
                    "going":       cells[4],
                    "class_":      cells[5],
                    "draw":        cells[6],
                    "odds":        _safe_float(cells[7]),
                    "jockey":      cells[8],
                    "trainer":     cells[9],
                    "weight":      _safe_int(cells[10]) if len(cells) > 10 else 0,
                    "finish_time": cells[11] if len(cells) > 11 else "",
                })
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return history
    except Exception as e:
        print(f"   ⚠ History {horse_name} ({horse_id}): {e}")
        return []

# ═══════════════════════════════════════════════════════════════════════════════
# 3. FETCH JOCKEY / TRAINER STATS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_jockey_stats(venue: str, dirs: dict) -> dict:
    """Live jockey stats. Cached 12 hours. Falls back to JOCKEY_SCORES."""
    cache_path = dirs["raw"] / f"jockey_stats_{venue}.json"
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < 43200:
        with open(cache_path) as f:
            return json.load(f)
    url_key = "jockey_rank_hv" if venue == "HV" else "jockey_rank_st"
    try:
        resp  = SESSION.get(URLS[url_key], timeout=15)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        stats = {}
        for row in soup.select("table tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 5:
                name  = cells[1]
                wins  = _safe_int(cells[2])
                rides = _safe_int(cells[5]) if len(cells) > 5 else 1
                stats[name] = {
                    "wins":     wins,
                    "rides":    rides,
                    "win_rate": round(wins / max(rides, 1) * 100, 1),
                }
        if stats:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
            print(f"   Jockey stats: {len(stats)} loaded (live)")
        else:
            print("   ⚠ Jockey stats empty — using fallback table")
        return stats
    except Exception as e:
        print(f"   ⚠ Jockey stats failed ({e}) — using fallback table")
        return {}


def fetch_trainer_stats(dirs: dict) -> dict:
    """Live trainer stats. Cached 12 hours. Falls back to TRAINER_SCORES."""
    cache_path = dirs["raw"] / "trainer_stats.json"
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < 43200:
        with open(cache_path) as f:
            return json.load(f)
    try:
        resp  = SESSION.get(URLS["trainer_rank"], timeout=15)
        resp.raise_for_status()
        soup  = BeautifulSoup(resp.text, "html.parser")
        stats = {}
        for row in soup.select("table tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 5:
                name  = cells[1]
                wins  = _safe_int(cells[2])
                rides = _safe_int(cells[5]) if len(cells) > 5 else 1
                stats[name] = {
                    "wins":     wins,
                    "rides":    rides,
                    "win_rate": round(wins / max(rides, 1) * 100, 1),
                }
        if stats:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
            print(f"   Trainer stats: {len(stats)} loaded (live)")
        else:
            print("   ⚠ Trainer stats empty — using fallback table")
        return stats
    except Exception as e:
        print(f"   ⚠ Trainer stats failed ({e}) — using fallback table")
        return {}

# ═══════════════════════════════════════════════════════════════════════════════
# 4. SCORING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def score_form(last6: str) -> float:
    """
    Score from last-6-runs string e.g. "1/2/4/3/1/5".
    Most recent run weighted highest. Returns 1.0–10.0.
    """
    if not isinstance(last6, str) or not last6.strip():
        return 5.0
    place_map = {1:10, 2:9, 3:8, 4:6, 5:5, 6:4, 7:3, 8:2, 9:2, 10:1}
    vals = []
    for p in str(last6).replace("-", "/").split("/"):
        try:    v = int(re.sub(r"\D", "", p) or "99")
        except: v = 99
        vals.append(min(v, 12))
    vals    = vals[-FORM_RUNS:]
    if not vals:
        return 5.0
    weights = list(range(1, len(vals) + 1))
    scores  = [place_map.get(v, 1) for v in vals]
    return round(sum(s * w for s, w in zip(scores, weights)) / sum(weights), 2)


def score_h2h(horse_id: str, field_ids: list, history_cache: dict) -> float:
    """H2H record vs field. Returns 0.0–10.0 (5.0 = no data)."""
    my_hist = history_cache.get(horse_id, [])
    if not my_hist:
        return 5.0
    my_by_date = {h["date"]: h["placing"] for h in my_hist}
    wins = total = 0
    for opp_id in field_ids:
        if opp_id == horse_id:
            continue
        for run in history_cache.get(opp_id, []):
            if run["date"] in my_by_date:
                my_p  = _placing_int(my_by_date[run["date"]])
                opp_p = _placing_int(run["placing"])
                if my_p and opp_p:
                    total += 1
                    if my_p < opp_p:
                        wins += 1
    return 5.0 if total == 0 else round(min(10.0, wins / total * 10), 2)


def score_jockey(name: str, live_stats: dict) -> float:
    """Live win rate preferred. Formula: 1 + 9 × min(wr/25%, 1.0)"""
    if name in live_stats:
        wr = live_stats[name].get("win_rate", 0)
        return round(min(10.0, max(1.0, 1 + 9 * min(wr / 25.0, 1.0))), 2)
    return JOCKEY_SCORES.get(name, JOCKEY_DEFAULT)


def score_trainer(name: str, live_stats: dict) -> float:
    """Live win rate preferred. Formula: 1 + 9 × min(wr/22%, 1.0)"""
    if name in live_stats:
        wr = live_stats[name].get("win_rate", 0)
        return round(min(10.0, max(1.0, 1 + 9 * min(wr / 22.0, 1.0))), 2)
    return TRAINER_SCORES.get(name, TRAINER_DEFAULT)


def score_draw(stall: int, venue: str, surface: str,
               distance: int, field_size: int) -> float:
    """Draw bias → score 1.0–10.0 using DRAW_BIAS lookup."""
    bias  = get_draw_bias(venue, surface, distance, stall)
    score = 1 + 9 * min(max((bias - 0.50) / (1.40 - 0.50), 0), 1)
    return round(score, 2)

# ═══════════════════════════════════════════════════════════════════════════════
# 5. SCORE FULL FIELD
# ═══════════════════════════════════════════════════════════════════════════════

def score_field(race: dict, venue: str,
                jky_stats: dict, trn_stats: dict,
                history_cache: dict) -> pd.DataFrame:
    """Score all horses in one race. Returns DataFrame sorted by composite."""
    horses   = race["horses"]
    surface  = race.get("surface", "Turf")
    distance = int(race.get("distance", 1200))
    n        = len(horses)
    if not horses:
        return pd.DataFrame()

    rname   = race.get("race_name", "").upper()
    weights = (WEIGHTS_CLASSIC
               if any(c in rname for c in CLASSIC_RACE_NAMES)
               else WEIGHTS_GENERAL)

    rows = []
    for h in horses:
        rows.append({
            "horse_no":   str(h.get("horse_no", "")),
            "horse_id":   str(h.get("horse_id", "")),
            "horse_name": h.get("horse_name", ""),
            "draw":       int(h.get("draw", n // 2)),
            "rating":     float(h.get("rating", 50)),
            "weight_lbs": float(h.get("weight_lbs", 126)),
            "win_odds":   float(h.get("win_odds", 0.0)),
            "jockey":     h.get("jockey", ""),
            "trainer":    h.get("trainer", ""),
            "last6_runs": h.get("last6_runs", ""),
            "gear":       h.get("gear", ""),
            "s_form":    score_form(h.get("last6_runs", "")),
            "s_draw":    score_draw(int(h.get("draw", n // 2)),
                                    venue, surface, distance, n),
            "s_jockey":  score_jockey(h.get("jockey", ""), jky_stats),
            "s_trainer": score_trainer(h.get("trainer", ""), trn_stats),
        })

    df = pd.DataFrame(rows)

    # Rating → 1–10
    mn, mx       = df["rating"].min(), df["rating"].max()
    df["s_rating"] = 1 + 9 * (df["rating"] - mn) / max(mx - mn, 1)

    # Market odds → 1–10 (0 = not available → neutral 5.0)
    if (df["win_odds"] > 0).any():
        mn_o = df.loc[df["win_odds"] > 0, "win_odds"].min()
        mx_o = df.loc[df["win_odds"] > 0, "win_odds"].max()
        df["s_market"] = df["win_odds"].apply(
            lambda o: round(10 - 9 * (o - mn_o) / max(mx_o - mn_o, 1), 2)
            if o > 0 else 5.0
        )
    else:
        df["s_market"] = 5.0   # preview run — no odds yet

    # Weight → 1–10 (lighter = better)
    mx_w, mn_w   = df["weight_lbs"].max(), df["weight_lbs"].min()
    df["s_weight"] = 1 + 9 * (mx_w - df["weight_lbs"]) / max(mx_w - mn_w, 1)

    # H2H
    field_ids    = df["horse_id"].tolist()
    df["s_h2h"]  = df["horse_id"].apply(
        lambda hid: score_h2h(hid, field_ids, history_cache)
    )

    # Composite
    df["composite"] = (
        weights.get("form",    0) * df["s_form"]    +
        weights.get("h2h",     0) * df["s_h2h"]     +
        weights.get("rating",  0) * df["s_rating"]  +
        weights.get("market",  0) * df["s_market"]  +
        weights.get("draw",    0) * df["s_draw"]     +
        weights.get("jockey",  0) * df["s_jockey"]  +
        weights.get("trainer", 0) * df["s_trainer"] +
        weights.get("weight",  0) * df["s_weight"]
    )

    # Softmax win probability
    exp_s          = np.exp(df["composite"] - df["composite"].max())
    df["win_prob"] = (exp_s / exp_s.sum() * 100).round(1)
    df["calc_odds"]= (1.0 / (df["win_prob"] / 100) * 0.85).round(1)

    return df.sort_values("composite", ascending=False).reset_index(drop=True)

# ═══════════════════════════════════════════════════════════════════════════════
# 6. DISPLAY + SAVE
# ═══════════════════════════════════════════════════════════════════════════════

def print_race_table(df: pd.DataFrame, race: dict):
    rno = race["race_no"]
    print(f"\n  {'═'*88}")
    print(f"  RACE {rno}  |  {race.get('race_name','')}  "
          f"{race.get('class_','')}  |  "
          f"{race.get('distance','')}m {race.get('surface','')}  |  "
          f"{len(df)} runners")
    print(f"  {'─'*88}")
    print(f"  {'Rnk':<4} {'#':<4} {'Horse':<22} {'Drw':<4} "
          f"{'Form':>5} {'Rtg':>5} {'Mkt':>5} {'Draw':>5} "
          f"{'Jky':>5} {'Trn':>5} {'H2H':>5} "
          f"{'Score':>7} {'Prob%':>7} {'Fair':>7}")
    print(f"  {'─'*88}")
    for i, row in df.iterrows():
        star = "★" if i < PLACES else " "
        print(f"  {star}{i+1:<3} #{str(row['horse_no']):<3} "
              f"{str(row['horse_name']):<22} {row['draw']:<4} "
              f"{row['s_form']:>5.1f} {row['s_rating']:>5.1f} "
              f"{row['s_market']:>5.1f} {row['s_draw']:>5.1f} "
              f"{row['s_jockey']:>5.1f} {row['s_trainer']:>5.1f} "
              f"{row['s_h2h']:>5.1f} "
              f"{row['composite']:>7.3f} "
              f"{row['win_prob']:>6.1f}% "
              f"{row['calc_odds']:>6.1f}x")
    print(f"  ★ = top-{PLACES} prediction")


def print_day_summary(all_results: list):
    print(f"\n  {'═'*60}")
    print(f"  TOP-{PLACES} SUMMARY")
    print(f"  {'─'*60}")
    for item in all_results:
        top  = item["df"].head(PLACES)
        line = "  ".join(
            f"#{r['horse_no']} {r['horse_name']} ({r['win_prob']:.1f}%)"
            for _, r in top.iterrows()
        )
        print(f"  R{item['race_no']:>2}  {line}")
    print(f"  {'═'*60}")


def save_predictions(all_results: list, race_date: str,
                     venue: str, dirs: dict) -> Path:
    tag       = race_date.replace("/", "-")
    json_path = dirs["pred"] / f"predictions_{tag}_{venue}.json"
    csv_path  = dirs["pred"] / f"predictions_{tag}_{venue}.csv"
    out = [{"race_no": item["race_no"], "race_name": item.get("race_name", ""),
             "horses": item["df"].to_dict(orient="records")}
           for item in all_results]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    rows = []
    for item in out:
        for h in item["horses"]:
            h["race_no"] = item["race_no"]
            rows.append(h)
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8")
    print(f"\n  ✓ Saved → {json_path}")
    print(f"  ✓ Saved → {csv_path}")
    return json_path

# ═══════════════════════════════════════════════════════════════════════════════
# 7. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run(race_date: str, venue: str):
    dirs = session_dirs(race_date, venue)
    print(f"\n  {'═'*60}")
    print(f"  HKJC PREDICTION ENGINE")
    print(f"  Race date : {race_date}  |  Venue : {venue}")
    print(f"  Run time  : {datetime.now().strftime('%Y-%m-%d %H:%M HKT')}")
    print(f"  Data dir  : {dirs['raw'].parent.parent}")
    print(f"  {'═'*60}")

    print("\n  [1/5] Fetching race card...")
    races = fetch_race_card(race_date, venue, dirs)
    print(f"   → {len(races)} races found")
    for r in races:
        print(f"      Race {r['race_no']:>2}  {r['distance']}m "
              f"{r['surface']:<5}  {r['class_']:<10}  "
              f"{len(r['horses'])} runners")

    print("\n  [2/5] Fetching jockey stats...")
    jky_stats = fetch_jockey_stats(venue, dirs)

    print("\n  [3/5] Fetching trainer stats...")
    trn_stats = fetch_trainer_stats(dirs)

    print("\n  [4/5] Fetching horse histories...")
    history_cache = {}
    total = sum(len(r["horses"]) for r in races)
    done  = 0
    for race in races:
        for h in race["horses"]:
            hid = h.get("horse_id", "")
            if hid and hid not in history_cache:
                history_cache[hid] = fetch_horse_history(
                    hid, h.get("horse_name", ""), dirs)
                done += 1
                time.sleep(0.3)
                print(f"   {done}/{total}  {h.get('horse_name','')} "
                      f"→ {len(history_cache[hid])} runs", end="\r")
    print(f"   {done} horses fetched, "
          f"{total - done} without IDs skipped.      ")

    print("\n  [5/5] Scoring...")
    all_results = []
    for race in races:
        df = score_field(race, venue, jky_stats, trn_stats, history_cache)
        if df.empty:
            print(f"   ⚠ Race {race['race_no']}: no horses to score")
            continue
        all_results.append({
            "race_no":   race["race_no"],
            "race_name": race.get("race_name", ""),
            "df":        df,
        })
        print_race_table(df, race)

    print_day_summary(all_results)
    save_predictions(all_results, race_date, venue, dirs)
    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Race Predictor")
    parser.add_argument("--date",  default=datetime.now().strftime("%Y/%m/%d"))
    parser.add_argument("--venue", default="ST", choices=["ST", "HV"])
    args = parser.parse_args()
    run(args.date, args.venue)

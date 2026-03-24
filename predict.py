"""
predict.py — HKJC Race Prediction Engine
=========================================
Usage:
  python predict.py                            # today, ST
  python predict.py --date 2026/03/25          # specific date
  python predict.py --date 2026/03/25 --venue HV
"""

import argparse, json, os, re, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from config import (
    URLS, HEADERS,
    WEIGHTS_GENERAL, WEIGHTS_CLASSIC, CLASSIC_RACE_NAMES,
    JOCKEY_SCORES, TRAINER_SCORES,
    get_draw_bias,
    RAW_DIR, PRED_DIR, CACHE_DIR,
    PLACES, FORM_RUNS,
    RACE_CALENDAR,
)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

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
    except:
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# 0. SETUP DIRS
# ═══════════════════════════════════════════════════════════════════════════════

from config import (
    URLS, HEADERS,
    WEIGHTS_GENERAL, WEIGHTS_CLASSIC, CLASSIC_RACE_NAMES,
    JOCKEY_SCORES, TRAINER_SCORES,
    get_draw_bias,
    setup_dirs,
    PLACES, FORM_RUNS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PLAYWRIGHT RENDERER
# ═══════════════════════════════════════════════════════════════════════════════

def _render_page(url: str, wait_ms: int = 4000) -> str:
    """Render JS SPA page with Playwright, return full HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page(extra_http_headers=HEADERS)
        page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_timeout(wait_ms)
        html = page.content()
        browser.close()
    return html

# ═══════════════════════════════════════════════════════════════════════════════
# 2. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

# ── Race Card ─────────────────────────────────────────────────────────────────

def _extract_available_race_numbers(soup):
    nav = soup.select_one("div.racingNum.top_races.js_racecard_rt_num")
    if not nav:
        return [1]

    race_nos = set()
    # current race may be shown as image without link, e.g. racecard_rt_1_o.gif
    for img in nav.select("img"):
        src = img.get("src", "")
        m = re.search(r"racecard_rt_(\d+)(?:_o)?\.gif", src)
        if m:
            race_nos.add(int(m.group(1)))

    # all links show available races
    for a in nav.select("a[href*='RaceNo']"):
        href = a.get("href", "")
        m = re.search(r"RaceNo=(\d+)", href)
        if m:
            race_nos.add(int(m.group(1)))

    if not race_nos:
        return [1]
    return sorted(race_nos)


def fetch_race_card(race_date: str, venue: str, dirs: dict) -> list:
    """
    Fetch full race card from HKJC for given date and venue via Playwright.
    Returns list of race dicts. Caches 6h.
    """
    cache = dirs["cache"] / f"card_{race_date.replace('/', '-')}_{venue}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 6:
        print(f"   (cache hit)")
        return json.loads(cache.read_text())

    base_url = (f"{URLS['race_card']}"
                f"?racedate={race_date.replace('/', '%2F')}&Racecourse={venue}")

    races = []

    # initial page can include one race and navigation to others
    print(f"   Rendering: {base_url}")
    html = _render_page(base_url, wait_ms=5000)
    soup = BeautifulSoup(html, "html.parser")

    available_races = _extract_available_race_numbers(soup)
    parsed_races = {}

    def parse_page(race_no, page_soup):
        for block in page_soup.select("table.starter, table.drawtable"):
            race_block = _parse_race_block(block, race_no=race_no)
            if race_block and race_block.get("horses"):
                parsed_races[race_no] = race_block
                break

    parse_page(available_races[0], soup)

    for race_no in available_races:
        if race_no in parsed_races:
            continue
        race_url = f"{base_url}&RaceNo={race_no}"
        print(f"   Rendering: {race_url}")
        race_html = _render_page(race_url, wait_ms=5000)
        race_soup = BeautifulSoup(race_html, "html.parser")
        parse_page(race_no, race_soup)

    for race_no in sorted(parsed_races):
        races.append(parsed_races[race_no])

    if not races:
        raise ValueError(
            f"No races parsed for {race_date} {venue}.\n"
            f"  URL: {base_url}\n"
            f"  HTML length: {len(html)} chars\n"
            f"  → Inspect page and update CSS selectors in _parse_race_block()."
        )

    cache.write_text(json.dumps(races, ensure_ascii=False, indent=2))
    return races


def _parse_race_block(block, race_no: int = 1) -> dict:
    """Parse one race block from HKJC HTML. Returns None if unparseable."""
    try:
        horses = []
        rows = block.select("tr")[1:]  # Skip header row
        
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 12:  # Need at least 12 columns
                horses.append({
                    "horse_no":   cells[0],                            # 0: Horse No.
                    "horse_id":   cells[4],                            # 4: Brand No. (Horse ID)
                    "horse_name": cells[3],                            # 3: Horse
                    "draw":       int(cells[8]) if cells[8].isdigit() else 0,  # 8: Draw
                    "weight_lbs": int(cells[5]) if cells[5].isdigit() else 126, # 5: Wt.
                    "jockey":     cells[6],                            # 6: Jockey
                    "trainer":    cells[9],                            # 9: Trainer
                    "rating":     int(cells[11]) if cells[11].isdigit() else 50, # 11: Rtg.
                    "last6_runs": cells[1],                            # 1: Last 6 Runs
                    "gear":       cells[7] if len(cells) > 7 else "",  # 7: Over Wt.
                    "win_odds":   20.0,
                })
        return {
            "race_no":   race_no,
            "race_name": "",
            "class_":    "",
            "distance":  1200,
            "surface":   "Turf",
            "horses":    horses,
        } if horses else None
    except Exception:
        return None


# ── Horse History ─────────────────────────────────────────────────────────────

def fetch_horse_history(horse_id: str, dirs: dict, horse_name: str = "") -> list:
    """
    Fetch individual horse past performance. Caches 6h per horse.
    """
    cache = dirs["cache"] / f"horse_{horse_id}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 6:
        return json.loads(cache.read_text())

    url  = f"{URLS['horse_profile']}?HorseId={horse_id}"
    html = _render_page(url, wait_ms=3000)
    soup = BeautifulSoup(html, "html.parser")

    history = []
    for row in soup.select("table.horseProfile tr, .horse-past-record tr")[1:]:
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

    cache.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    return history


# ── Draw Stats (requests ✅) ───────────────────────────────────────────────────

def fetch_draw_stats(race_date: str, venue: str, dirs: dict) -> list:
    """
    Fetch live draw statistics from HKJC /information/draw.
    Returns list of {race_no, distance, surface, course, draws:[...]}.
    Caches 24h.
    """
    cache = dirs["cache"] / f"drawstats_{race_date.replace('/', '-')}_{venue}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 24:
        return json.loads(cache.read_text())

    resp = SESSION.get(URLS["draw_stats"], timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    all_races = []
    for table in soup.select("table"):
        headers = [th.get_text(strip=True)
                   for th in table.select("tr:first-child td, tr:first-child th")]
        if "Draw" not in headers:
            continue

        caption = ""
        prev = table.find_previous(string=re.compile(r"Race \d+"))
        if prev:
            caption = prev.strip()

        m        = re.match(r"Race\s+(\d+)\s+(\d+)m\s+(\w+)\s+(.*)", caption)
        race_no  = int(m.group(1)) if m else 0
        distance = int(m.group(2)) if m else 0
        surface  = m.group(3)      if m else ""
        course   = m.group(4)      if m else ""

        draws = []
        for row in table.select("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 8 and cells[0].isdigit():
                draws.append({
                    "draw":       int(cells[0]),
                    "runners":    _safe_int(cells[1]),
                    "win":        _safe_int(cells[2]),
                    "second":     _safe_int(cells[3]),
                    "third":      _safe_int(cells[4]),
                    "fourth":     _safe_int(cells[5]),
                    "win_pct":    _safe_float(cells[6]),
                    "q_pct":      _safe_float(cells[7]),
                    "place_pct":  _safe_float(cells[8]) if len(cells) > 8 else 0.0,
                    "first4_pct": _safe_float(cells[9]) if len(cells) > 9 else 0.0,
                })
        if draws:
            all_races.append({
                "race_no":  race_no,
                "distance": distance,
                "surface":  surface,
                "course":   course,
                "draws":    draws,
            })

    cache.write_text(json.dumps(all_races, ensure_ascii=False, indent=2))
    print(f"   → Draw stats: {len(all_races)} race tables")
    return all_races


# ── Jockey Challenge Stats (requests ✅) ──────────────────────────────────────

def fetch_jkc_stats(dirs: dict) -> list:
    """
    Jockey Challenge stats: last 10 meeting points + season avg.
    Caches 12h.
    """
    cache = dirs["cache"] / "jkc_stats.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 12:
        return json.loads(cache.read_text())

    resp = SESSION.get(URLS["jkc_stat"], timeout=15)
    resp.raise_for_status()
    soup  = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    rows  = table.select("tr")
    dates = [td.get_text(strip=True) for td in rows[2].select("td")][1:11]

    results = []
    for row in rows[3:]:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) < 3 or not cells[0]:
            continue
        pts = {}
        for i, d in enumerate(dates):
            val = cells[i + 1] if (i + 1) < len(cells) else ""
            pts[d] = _safe_float(val) if val not in ("", "-") else None
        results.append({
            "jockey":          cells[0],
            "pts_last10":      pts,
            "avg_pts_meeting": _safe_float(cells[11]) if len(cells) > 11 else 0.0,
            "season_current":  _safe_float(cells[13]) if len(cells) > 13 else 0.0,
            "season_prev":     _safe_float(cells[14]) if len(cells) > 14 else 0.0,
        })

    cache.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"   → JKC stats: {len(results)} jockeys")
    return results


# ── Trainer Challenge Stats (requests ✅) ─────────────────────────────────────

def fetch_tnc_stats(dirs: dict) -> list:
    """
    Trainer Challenge stats: last 10 meeting points + season avg.
    Caches 12h.
    """
    cache = dirs["cache"] / "tnc_stats.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 12:
        return json.loads(cache.read_text())

    resp = SESSION.get(URLS["tnc_stat"], timeout=15)
    resp.raise_for_status()
    soup  = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    rows  = table.select("tr")
    dates = [td.get_text(strip=True) for td in rows[2].select("td")][1:11]

    results = []
    for row in rows[3:]:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) < 3 or not cells[0]:
            continue
        pts = {}
        for i, d in enumerate(dates):
            val = cells[i + 1] if (i + 1) < len(cells) else ""
            pts[d] = _safe_float(val) if val not in ("", "-") else None
        results.append({
            "trainer":         cells[0],
            "pts_last10":      pts,
            "avg_pts_meeting": _safe_float(cells[11]) if len(cells) > 11 else 0.0,
            "season_current":  _safe_float(cells[13]) if len(cells) > 13 else 0.0,
            "season_prev":     _safe_float(cells[14]) if len(cells) > 14 else 0.0,
        })

    cache.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"   → TNC stats: {len(results)} trainers")
    return results


# ── Jockey Ranking (Playwright) ───────────────────────────────────────────────

def fetch_jockey_ranking(venue: str, dirs: dict, season: str = "Current") -> list:
    """
    Fetch jockey season ranking.
    venue options: ALL | STT | STA | HVT
    Caches 12h.
    """
    cache = dirs["cache"] / f"jockey_ranking_{venue}_{season}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 12:
        return json.loads(cache.read_text())

    url  = (f"{URLS['jockey_ranking']}"
            f"?season={season}&view=Numbers&racecourse={venue}")
    html = _render_page(url)
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for row in soup.select("table tr")[1:]:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) >= 5:
            results.append({
                "rank":     _safe_int(cells[0]),
                "jockey":   cells[1],
                "wins":     _safe_int(cells[2]),
                "seconds":  _safe_int(cells[3]),
                "thirds":   _safe_int(cells[4]),
                "rides":    _safe_int(cells[5]) if len(cells) > 5 else 0,
                "win_rate": _safe_float(cells[6]) if len(cells) > 6 else 0.0,
                "venue":    venue,
                "season":   season,
            })

    if results:
        cache.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"   → Jockey ranking ({venue}): {len(results)} entries")
    return results


# ── Trainer Ranking (Playwright) ──────────────────────────────────────────────

def fetch_trainer_ranking(dirs: dict, season: str = "Current") -> list:
    """
    Fetch trainer season ranking. Caches 12h.
    """
    cache = dirs["cache"] / f"trainer_ranking_{season}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 12:
        return json.loads(cache.read_text())

    url  = (f"{URLS['trainer_ranking']}"
            f"?season={season}&view=Numbers&racecourse=ALL")
    html = _render_page(url)
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for row in soup.select("table tr")[1:]:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) >= 4:
            results.append({
                "rank":     _safe_int(cells[0]),
                "trainer":  cells[1],
                "wins":     _safe_int(cells[2]),
                "seconds":  _safe_int(cells[3]),
                "thirds":   _safe_int(cells[4]) if len(cells) > 4 else 0,
                "rides":    _safe_int(cells[5]) if len(cells) > 5 else 0,
                "win_rate": _safe_float(cells[6]) if len(cells) > 6 else 0.0,
                "season":   season,
            })

    if results:
        cache.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"   → Trainer ranking: {len(results)} entries")
    return results


# ── Jockey Favourite Stats (Playwright) ───────────────────────────────────────

def fetch_jockey_favourite(dirs: dict, season: str = "Current") -> list:
    """
    Jockey favourite runner statistics. Caches 12h.
    """
    cache = dirs["cache"] / f"jockey_favourite_{season}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 12:
        return json.loads(cache.read_text())

    url  = f"{URLS['jockey_favourite']}?season={season}"
    html = _render_page(url)
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for row in soup.select("table tr")[1:]:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) >= 5:
            results.append({
                "jockey":    cells[0],
                "rides":     _safe_int(cells[1]),
                "wins":      _safe_int(cells[2]),
                "seconds":   _safe_int(cells[3]),
                "thirds":    _safe_int(cells[4]),
                "win_pct":   _safe_float(cells[5]) if len(cells) > 5 else 0.0,
                "place_pct": _safe_float(cells[6]) if len(cells) > 6 else 0.0,
            })

    if results:
        cache.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"   → Jockey favourite stats: {len(results)} jockeys")
    return results


# ── Trainer Favourite Stats (Playwright) ──────────────────────────────────────

def fetch_trainer_favourite(dirs: dict, season: str = "Current") -> list:
    """
    Trainer favourite runner statistics. Caches 12h.
    """
    cache = dirs["cache"] / f"trainer_favourite_{season}.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 3600 * 12:
        return json.loads(cache.read_text())

    url  = f"{URLS['trainer_favourite']}?season={season}"
    html = _render_page(url)
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for row in soup.select("table tr")[1:]:
        cells = [td.get_text(strip=True) for td in row.select("td")]
        if len(cells) >= 5:
            results.append({
                "trainer":   cells[0],
                "rides":     _safe_int(cells[1]),
                "wins":      _safe_int(cells[2]),
                "seconds":   _safe_int(cells[3]),
                "thirds":    _safe_int(cells[4]),
                "win_pct":   _safe_float(cells[5]) if len(cells) > 5 else 0.0,
                "place_pct": _safe_float(cells[6]) if len(cells) > 6 else 0.0,
            })

    if results:
        cache.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"   → Trainer favourite stats: {len(results)} trainers")
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SAVE RAW DATA → data/raw/YYYY-MM-DD_VN.xlsx
# ═══════════════════════════════════════════════════════════════════════════════

def save_raw_xlsx(races, draw_stats, jky_ranking, trn_ranking,
                  jkc_stats, tnc_stats, jky_fav, trn_fav,
                  history_cache, race_date, venue, dirs) -> Path:
    tag  = race_date.replace("/", "-")
    path = dirs["raw"] / f"{tag}_{venue}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:

        # ── RaceCard (all races flat) ──────────────────────────────────────────
        card_rows = []
        for race in races:
            for h in race["horses"]:
                card_rows.append({
                    "race_no":    race["race_no"],
                    "race_name":  race.get("race_name", ""),
                    "class":      race.get("class_", ""),
                    "distance":   race.get("distance", ""),
                    "surface":    race.get("surface", ""),
                    "horse_no":   h.get("horse_no", ""),
                    "horse_id":   h.get("horse_id", ""),
                    "horse_name": h.get("horse_name", ""),
                    "draw":       h.get("draw", ""),
                    "weight_lbs": h.get("weight_lbs", ""),
                    "jockey":     h.get("jockey", ""),
                    "trainer":    h.get("trainer", ""),
                    "rating":     h.get("rating", ""),
                    "last6_runs": h.get("last6_runs", ""),
                    "gear":       h.get("gear", ""),
                    "win_odds":   h.get("win_odds", ""),
                })
        pd.DataFrame(card_rows).to_excel(writer, sheet_name="RaceCard", index=False)

        # ── Per-race sheets R1…RN (with draw bias) ────────────────────────────
        for race in races:
            rows = []
            n = len(race["horses"])
            for h in race["horses"]:
                stall = int(h.get("draw", 0))
                bias  = get_draw_bias(
                    venue, race.get("surface", "Turf"),
                    int(race.get("distance", 1200)), stall)
                rows.append({**h, "field_size": n, "draw_bias": round(bias, 3)})
            pd.DataFrame(rows).to_excel(
                writer, sheet_name=f"R{race['race_no']}", index=False)

        # ── DrawStat (live HKJC data) ──────────────────────────────────────────
        draw_rows = []
        for r in draw_stats:
            for d in r.get("draws", []):
                draw_rows.append({
                    "race_no":    r["race_no"],
                    "distance":   r["distance"],
                    "surface":    r["surface"],
                    "course":     r["course"],
                    **d
                })
        if draw_rows:
            pd.DataFrame(draw_rows).to_excel(writer, sheet_name="DrawStat", index=False)

        # ── JockeyRanking ─────────────────────────────────────────────────────
        if jky_ranking:
            pd.DataFrame(jky_ranking).to_excel(
                writer, sheet_name="JockeyRanking", index=False)

        # ── TrainerRanking ────────────────────────────────────────────────────
        if trn_ranking:
            pd.DataFrame(trn_ranking).to_excel(
                writer, sheet_name="TrainerRanking", index=False)

        # ── JockeyChallenge (JKC) ─────────────────────────────────────────────
        if jkc_stats:
            jkc_rows = []
            for j in jkc_stats:
                row = {
                    "jockey":          j["jockey"],
                    "avg_pts":         j["avg_pts_meeting"],
                    "season_current":  j["season_current"],
                    "season_prev":     j["season_prev"],
                }
                row.update({f"pts_{k}": v for k, v in j["pts_last10"].items()})
                jkc_rows.append(row)
            pd.DataFrame(jkc_rows).to_excel(
                writer, sheet_name="JockeyChallenge", index=False)

        # ── TrainerChallenge (TNC) ────────────────────────────────────────────
        if tnc_stats:
            tnc_rows = []
            for t in tnc_stats:
                row = {
                    "trainer":         t["trainer"],
                    "avg_pts":         t["avg_pts_meeting"],
                    "season_current":  t["season_current"],
                    "season_prev":     t["season_prev"],
                }
                row.update({f"pts_{k}": v for k, v in t["pts_last10"].items()})
                tnc_rows.append(row)
            pd.DataFrame(tnc_rows).to_excel(
                writer, sheet_name="TrainerChallenge", index=False)

        # ── JockeyFavourite ───────────────────────────────────────────────────
        if jky_fav:
            pd.DataFrame(jky_fav).to_excel(
                writer, sheet_name="JockeyFavourite", index=False)

        # ── TrainerFavourite ──────────────────────────────────────────────────
        if trn_fav:
            pd.DataFrame(trn_fav).to_excel(
                writer, sheet_name="TrainerFavourite", index=False)

        # ── HorseHistory ──────────────────────────────────────────────────────
        hist_rows = []
        for hid, runs in history_cache.items():
            for run in runs:
                hist_rows.append({"horse_id": hid, **run})
        if hist_rows:
            pd.DataFrame(hist_rows).to_excel(
                writer, sheet_name="HorseHistory", index=False)

    print(f"  ✓ Raw XLSX    → {path}")
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_form_score(last6: str) -> float:
    if not isinstance(last6, str) or "/" not in last6:
        return 5.0
    vals = []
    for p in last6.split("/"):
        try:    v = int(re.sub(r"\D", "", p) or "99")
        except: v = 99
        vals.append(min(v, 20))
    vals = vals[-FORM_RUNS:]
    wts  = list(range(1, len(vals) + 1))
    score_map = {1:10,2:9,3:8,4:6,5:5,6:4,7:3,8:2,9:2,10:1,11:1,12:1}
    scored = [score_map.get(v, 1) * w for v, w in zip(vals, wts)]
    return round(sum(scored) / sum(wts), 2)


def compute_h2h_score(horse_id: str, field_ids: list,
                      history_cache: dict) -> float:
    my_hist = history_cache.get(horse_id, [])
    if not my_hist:
        return 5.0
    my_dates = {h["date"]: h["placing"] for h in my_hist}
    wins = total = 0
    for opp_id in field_ids:
        if opp_id == horse_id:
            continue
        for h in history_cache.get(opp_id, []):
            if h["date"] in my_dates:
                my_p  = _placing_int(my_dates[h["date"]])
                opp_p = _placing_int(h["placing"])
                if my_p and opp_p:
                    total += 1
                    if my_p < opp_p:
                        wins += 1
    return round(min(10.0, max(0.0, (wins / total) * 10)), 2) if total else 5.0


def compute_draw_score(stall: int, venue: str, surface: str,
                       distance: int, field_size: int) -> float:
    bias = get_draw_bias(venue, surface, distance, stall)
    return round(min(10.0, max(1.0, bias * 7.0)), 2)


def jockey_score(name: str, live_stats: dict) -> float:
    if name in live_stats:
        wr = live_stats[name].get("win_rate", 0) / 100.0
        return round(min(10.0, max(1.0, 1 + 9 * min(wr / 0.25, 1))), 2)
    return JOCKEY_SCORES.get(name, 6.5)


def trainer_score(name: str, live_stats: dict) -> float:
    if name in live_stats:
        wr = live_stats[name].get("win_rate", 0) / 100.0
        return round(min(10.0, max(1.0, 1 + 9 * min(wr / 0.22, 1))), 2)
    return TRAINER_SCORES.get(name, 6.5)


def score_field(race: dict, venue: str, jky_stats: dict,
                trn_stats: dict, history_cache: dict,
                weights: dict) -> pd.DataFrame:
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
            "gear":       h.get("gear", ""),
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
    df["s_market"] = 10- 9 * (df["odds"]        - min_o) / max(max_o - min_o, 1)
    df["s_weight"] = 1 + 9 * (max_w - df["weight_lbs"]) / max(max_w - min_w, 1)

    field_ids   = df["horse_id"].tolist()
    df["s_h2h"] = df["horse_id"].apply(
        lambda hid: compute_h2h_score(hid, field_ids, history_cache))

    w = weights
    df["composite"] = (
        w.get("form",    0) * df["s_form"]    +
        w.get("rating",  0) * df["s_rating"]  +
        w.get("market",  0) * df["s_market"]  +
        w.get("draw",    0) * df["s_draw"]    +
        w.get("jockey",  0) * df["s_jockey"]  +
        w.get("trainer", 0) * df["s_trainer"] +
        w.get("h2h",     0) * df["s_h2h"]     +
        w.get("weight",  0) * df["s_weight"]
    )

    exp_s           = np.exp(df["composite"] - df["composite"].max())
    df["win_prob"]  = (exp_s / exp_s.sum() * 100).round(1)
    df["calc_odds"] = (1.0 / (df["win_prob"] / 100) * 0.85).round(1)

    return df.sort_values("composite", ascending=False).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SAVE PREDICTIONS → data/predictions/YYYY-MM-DD_VN.xlsx
# ═══════════════════════════════════════════════════════════════════════════════

def save_predictions_xlsx(all_results: list, race_date: str,
                          venue: str, dirs: dict) -> Path:
    tag  = race_date.replace("/", "-")
    path = dirs["pred"] / f"{tag}_{venue}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:

        # ── Master Prediction Table ───────────────────────────────────────────
        # One row per horse (pos 1-4), grouped by race, ordered by win%
        master_rows = []
        for item in all_results:
            df = item["df"].sort_values("win_prob", ascending=False).reset_index(drop=True)
            for pos, (_, row) in enumerate(df.head(PLACES).iterrows(), 1):
                master_rows.append({
                    "race_no":    item["race_no"],
                    "race_name":  item.get("race_name", ""),
                    "pos":        pos,              # 1 = predicted winner
                    "horse_no":   row["horse_no"],
                    "horse":      row["horse_name"],
                    "draw":       row["draw"],
                    "jockey":     row["jockey"],
                    "trainer":    row["trainer"],
                    "last6":      row["last6_runs"],
                    "s_form":     row["s_form"],
                    "s_draw":     row["s_draw"],
                    "s_jockey":   row["s_jockey"],
                    "s_trainer":  row["s_trainer"],
                    "s_h2h":      row["s_h2h"],
                    "s_rating":   row["s_rating"],
                    "s_market":   row["s_market"],
                    "composite":  round(row["composite"], 4),
                    "win_prob%":  row["win_prob"],
                    "calc_odds":  row["calc_odds"],
                    "live_odds":  "",   # → filled by live_odds.py
                    "actual_pos": "",   # → filled by review.py after race
                    "hit":        "",   # → filled by review.py  (Y/N)
                })
        pd.DataFrame(master_rows).to_excel(
            writer, sheet_name="Predictions", index=False)

        # ── Wide Summary — one row per race, 4 picks side by side ─────────────
        wide_rows = []
        for item in all_results:
            df = item["df"].sort_values("win_prob", ascending=False).reset_index(drop=True)
            row_dict = {
                "race_no":   item["race_no"],
                "race_name": item.get("race_name", ""),
            }
            for pos, (_, row) in enumerate(df.head(PLACES).iterrows(), 1):
                row_dict[f"P{pos}_no"]       = row["horse_no"]
                row_dict[f"P{pos}_horse"]    = row["horse_name"]
                row_dict[f"P{pos}_draw"]     = row["draw"]
                row_dict[f"P{pos}_win%"]     = row["win_prob"]
                row_dict[f"P{pos}_calcodds"] = row["calc_odds"]
                row_dict[f"P{pos}_jockey"]   = row["jockey"]
            wide_rows.append(row_dict)
        pd.DataFrame(wide_rows).to_excel(
            writer, sheet_name="Summary", index=False)

        # ── Per-race full scored sheets R1…RN ──────────────────────────────────
        for item in all_results:
            df = item["df"].sort_values("win_prob", ascending=False).reset_index(drop=True)
            df.insert(0, "pos", range(1, len(df) + 1))
            df.to_excel(writer, sheet_name=f"R{item['race_no']}", index=False)

    print(f"  ✓ Predictions → {path}")
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CONSOLE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def print_race_table(df: pd.DataFrame, race: dict, race_no: int):
    name = race.get("race_name", f"Race {race_no}")
    cls  = race.get("class_", "")
    dist = race.get("distance", "")
    surf = race.get("surface", "")
    n    = len(df)

    print(f"\n{'═'*92}")
    print(f"  RACE {race_no} | {name} | {cls} | {dist}m {surf} | {n} runners")
    print(f"{'─'*92}")
    print(f"  {'Rnk':<4} {'#':<4} {'Horse':<22} {'Drw':<4} "
          f"{'Form':>5} {'Rtg':>5} {'Drw':>5} {'Jky':>5} "
          f"{'Trn':>5} {'H2H':>5} "
          f"{'MktOdds':>8} {'Score':>7} {'WinProb':>8} {'CalcOdds':>9}")
    print(f"  {'─'*89}")

    for i, row in df.iterrows():
        star = "★" if i < PLACES else " "
        print(
            f"  {star}{i+1:<3} #{str(row['horse_no']):<3} "
            f"{str(row['horse_name']):<22} {row['draw']:<4} "
            f"{row['s_form']:>5.1f} {row['rating']:>5.0f} "
            f"{row['s_draw']:>5.1f} {row['s_jockey']:>5.1f} "
            f"{row['s_trainer']:>5.1f} {row['s_h2h']:>5.1f} "
            f"{row['odds']:>8.1f} {row['composite']:>7.2f} "
            f"{row['win_prob']:>7.1f}% {row['calc_odds']:>8.1f}x"
        )
    print(f"  ★ = top-{PLACES} picks")


def print_summary(all_results: list):
    """Print final prediction table — all races, top 4 ordered 1>2>3>4."""
    print(f"\n{'═'*110}")
    print(f"  FINAL PREDICTION TABLE — {len(all_results)} RACES")
    print(f"{'═'*110}")
    print(f"  {'Race':<6} {'Pos':<5} {'#':<4} {'Horse':<22} {'Draw':<5} "
          f"{'Win%':>6} {'CalcOdds':>9} {'Jockey':<20} {'Trainer':<18} {'Last 6'}")
    print(f"  {'─'*107}")

    for item in all_results:
        df = item["df"].sort_values("win_prob", ascending=False).reset_index(drop=True)
        print(f"  {'─'*107}")
        print(f"  R{item['race_no']}  {item.get('race_name','')}")
        print(f"  {'─'*107}")
        for pos, (_, row) in enumerate(df.head(PLACES).iterrows(), 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉", 4: "4️⃣ "}.get(pos, f"{pos} ")
            print(
                f"  {medal}  {pos:<4} #{str(row['horse_no']):<3} "
                f"{str(row['horse_name']):<22} {row['draw']:<5} "
                f"{row['win_prob']:>5.1f}% {row['calc_odds']:>8.1f}x "
                f"  {str(row['jockey']):<20} {str(row['trainer']):<18} "
                f"{row['last6_runs']}"
            )

    print(f"\n{'═'*110}")

def print_top4_picks(all_results: list):
    print(f"\n{'═'*92}")
    print("  DETAILED TOP-4 PICKS PER RACE")
    print(f"{'─'*92}")
    for item in all_results:
        print(f"  Race {item['race_no']}: {item.get('race_name', '')}")
        for rank, (_, row) in enumerate(item['df'].head(PLACES).iterrows(), 1):
            print(
                f"    {rank}. #{row['horse_no']} {row['horse_name']} "
                f"({row['jockey']} / {row['trainer']}) -> "
                f"Win% {row['win_prob']:.1f}%, CalcOdds {row['calc_odds']:.1f}x"
            )
    print(f"{'═'*92}\n")

# ═══════════════════════════════════════════════════════════════════════════════
# 7. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _get_next_race_day():
    today = datetime.now().date()
    for rd in sorted(RACE_CALENDAR, key=lambda x: datetime.strptime(x['date'], '%Y/%m/%d')):
        rd_date = datetime.strptime(rd['date'], '%Y/%m/%d').date()
        if rd_date >= today:
            return rd
    return None


def run(race_date: str | None, venue: str | None):
    if not race_date or not venue:
        next_race = _get_next_race_day()
        if not next_race:
            raise ValueError('No upcoming race day found in RACE_CALENDAR.')
        race_date = race_date or next_race['date']
        venue = venue or next_race['venue']

    print(f"\n{'═'*60}")
    print(f"  HKJC PREDICTION ENGINE")
    print(f"  Race date : {race_date}  |  Venue : {venue}")
    print(f"  Run time  : {datetime.now().strftime('%Y-%m-%d %H:%M')} HKT")
    print(f"{'═'*60}\n")

    dirs = setup_dirs()

    # [1/7] Race card
    print("  [1/7] Fetching race card...")
    races = fetch_race_card(race_date, venue, dirs)
    print(f"   → {len(races)} races found")

    # [2/7] Draw stats
    print("  [2/7] Fetching draw stats...")
    draw_stats = fetch_draw_stats(race_date, venue, dirs)

    # [3/7] Jockey & trainer rankings
    print("  [3/7] Fetching jockey / trainer rankings...")
    jky_ranking = fetch_jockey_ranking("ALL", dirs)
    trn_ranking = fetch_trainer_ranking(dirs)
    jky_stats   = {r["jockey"]:  r for r in jky_ranking}
    trn_stats   = {r["trainer"]: r for r in trn_ranking}
    print(f"   → {len(jky_stats)} jockeys | {len(trn_stats)} trainers")

    # [4/7] Challenge & favourite stats
    print("  [4/7] Fetching challenge & favourite stats...")
    jkc_stats = fetch_jkc_stats(dirs)
    tnc_stats = fetch_tnc_stats(dirs)
    jky_fav   = fetch_jockey_favourite(dirs)
    trn_fav   = fetch_trainer_favourite(dirs)

    # [5/7] Horse histories
    print("  [5/7] Fetching horse histories (H2H)...")
    history_cache = {}
    for race in races:
        for h in race["horses"]:
            hid = h.get("horse_id", "")
            if hid and hid not in history_cache:
                try:
                    history_cache[hid] = fetch_horse_history(hid, dirs,
                                                              h.get("horse_name", ""))
                    time.sleep(0.3)
                except Exception as e:
                    print(f"   ⚠ {hid}: {e}")
                    history_cache[hid] = []
    print(f"   → {len(history_cache)} horses cached")

    # [6/7] Save all raw data to XLSX
    print("  [6/7] Saving raw XLSX...")
    save_raw_xlsx(
        races, draw_stats,
        jky_ranking, trn_ranking,
        jkc_stats, tnc_stats,
        jky_fav, trn_fav,
        history_cache,
        race_date, venue, dirs,
    )

    # [7/7] Score, print, save predictions
    print("  [7/7] Scoring and predicting...")
    all_results = []
    for race in races:
        rno  = race["race_no"]
        name = race.get("race_name", "").upper()
        w    = (WEIGHTS_CLASSIC
                if any(c in name for c in CLASSIC_RACE_NAMES)
                else WEIGHTS_GENERAL)
        df   = score_field(race, venue, jky_stats, trn_stats, history_cache, w)
        all_results.append({"race_no": rno, "race_name": name, "df": df})
        print_race_table(df, race, rno)

    print_summary(all_results)
    print_top4_picks(all_results)
    save_predictions_xlsx(all_results, race_date, venue, dirs)

    print(f"\n{'═'*60}")
    tag = race_date.replace('/', '-')
    print(f"  Files saved:")
    print(f"    data/raw/{tag}_{venue}.xlsx")
    print(f"    data/predictions/{tag}_{venue}.xlsx")
    print(f"{'═'*60}\n")

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Race Predictor")
    parser.add_argument("--date",  default=None,
                        help="Race date YYYY/MM/DD (default: next scheduled race day)")
    parser.add_argument("--venue", default=None, choices=["ST", "HV"],
                        help="Venue: ST or HV (default: from next scheduled race day)")
    args = parser.parse_args()
    run(args.date, args.venue)

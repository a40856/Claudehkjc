"""
review.py — Fetch actual results and score predictions
=======================================================
Usage:
  python review.py                            # today
  python review.py --date 2026/03/25 --venue HV
"""

import argparse, json, re, sys, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from config import (
    URLS, HEADERS,
    RAW_DIR, PRED_DIR, CACHE_DIR,
    setup_dirs, PLACES,
)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_int(s) -> int:
    try:    return int(re.sub(r"\D", "", str(s)))
    except: return 0

def _safe_float(s) -> float:
    try:    return float(re.sub(r"[^\d.]", "", str(s)))
    except: return 0.0

def _render_page(url: str, wait_ms: int = 4000, retries: int = 3, timeout: int = 60000) -> str:
    """Render a page with Playwright using load state and retry on timeout/failures."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(extra_http_headers=HEADERS)
                page.goto(url, wait_until="load", timeout=timeout)
                page.wait_for_timeout(wait_ms)
                html = page.content()
                browser.close()
            return html
        except Exception as exc:
            last_err = exc
            print(f"   ⚠ Render attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(2)
                print("   ↻ Retrying...")
            else:
                print("   ✗ All render attempts failed; raising.")
                raise
    raise last_err

# ═══════════════════════════════════════════════════════════════════════════════
# 1. FETCH RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_results(race_date: str, venue: str, dirs: dict) -> list:
    """
    Fetch full race results from HKJC results page.
    Each race is on a separate page, so we fetch them individually.
    Returns list of race result dicts:
    [
      {
        race_no, race_name, distance, surface, class_,
        places: [
          {pos, horse_no, horse_name, jockey, trainer,
           win_odds, time, margin},
          ...
        ],
        dividends: {
          win:    [{combo, dividend}],
          place:  [{combo, dividend}],
          quinella: [...],
          forecast: [...],
          tierce:  [...],
          trio:    [...],
          first4:  [...],
          quartet: [...],
        }
      },
      ...
    ]
    Caches to data/cache/ for 30 days (results never change).
    """
    cache = dirs["cache"] / f"results_{race_date.replace('/', '-')}_{venue}.json"
    if cache.exists():
        print(f"   (cache hit — results)")
        return json.loads(cache.read_text())

    all_races = []
    
    # Get the number of races from predictions file
    pred_path = dirs["pred"] / f"{race_date.replace('/', '-')}_{venue}.xlsx"
    if pred_path.exists():
        pred_df = pd.read_excel(pred_path, sheet_name="Predictions")
        race_numbers = sorted(pred_df["race_no"].unique())
        # Convert numpy types to Python types
        race_numbers = [int(r) for r in race_numbers]
        print(f"   Found {len(race_numbers)} races to fetch: {race_numbers}")
    else:
        # Fallback: try races 1-12
        race_numbers = list(range(1, 13))
        print(f"   No predictions file found, trying races 1-12")

    for race_no in race_numbers:
        try:
            print(f"   Fetching Race {race_no}...")
            race_result = fetch_single_race(race_date, venue, race_no, dirs)
            if race_result:
                all_races.append(race_result)
            else:
                print(f"   ⚠ No results found for Race {race_no}")
        except Exception as e:
            print(f"   ⚠ Error fetching Race {race_no}: {e}")
            continue

    if not all_races:
        raise ValueError(
            f"No race results found for {race_date} {venue}.\n"
            f"  Checked {len(race_numbers)} races individually.\n"
            f"  Results may not be available yet."
        )

    cache.write_text(json.dumps(all_races, ensure_ascii=False, indent=2))
    print(f"   → {len(all_races)} race results fetched")
    return all_races


def fetch_single_race(race_date: str, venue: str, race_no: int, dirs: dict) -> dict:
    """
    Fetch results for a single race using raceno parameter.
    """
    url = (f"{URLS['race_results']}"
           f"?racedate={race_date.replace('/', '%2F')}&Racecourse={venue}&raceno={race_no}")
    
    html = _render_page(url, wait_ms=3000)
    soup = BeautifulSoup(html, "html.parser")
    
    # Find the results table
    tables = soup.select("table")
    results_table = None
    
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.select("tr th, tr td")]
        header_text = " ".join(headers)
        if (("Pla." in header_text and "Horse No." in header_text)
                or ("名次" in header_text and "馬號" in header_text)):
            results_table = table
            break
    
    if not results_table:
        return None
    
    # Parse race info from page title or headers
    race_info = _parse_race_info(soup, race_no)
    
    # Parse results
    result = _parse_result_block(results_table)
    if result:
        result.update(race_info)
        result["race_no"] = race_no
        return result
    
    return None


def _parse_race_info(soup, race_no: int) -> dict:
    """Extract race information from the page."""
    info = {
        "race_name": "",
        "distance": 0,
        "surface": "",
        "class_": "",
    }
    
    # Look for race header text
    text_content = soup.get_text()
    
    # Extract race name
    if "Handicap" in text_content:
        # Find text before "Handicap"
        parts = text_content.split("Handicap")
        if len(parts) > 1:
            info["race_name"] = parts[0].strip().split("RACE")[-1].strip()
    elif "Cup" in text_content:
        parts = text_content.split("Cup")
        if len(parts) > 1:
            info["race_name"] = parts[0].strip().split("RACE")[-1].strip() + "Cup"
    
    # Extract class
    import re
    class_match = re.search(r'Class (\d+)', text_content)
    if class_match:
        info["class_"] = f"Class {class_match.group(1)}"
    
    # Extract distance
    dist_match = re.search(r'(\d+)M', text_content)
    if dist_match:
        info["distance"] = int(dist_match.group(1))
    
    # Extract surface
    if "TURF" in text_content:
        info["surface"] = "Turf"
    elif "ALL WEATHER" in text_content:
        info["surface"] = "All Weather"
    
    return info


def _parse_result_block(block) -> dict:
    """Parse one race result block. Returns None if unparseable."""
    try:
        places = []
        rows = block.select("tr")
        
        # Find the header row (might be th or td)
        header_row = None
        for row in rows:
            cells = [cell.get_text(strip=True) for cell in row.select("th, td")]
            if cells and "Pla." in cells[0] and "Horse No." in cells[1]:
                header_row = row
                break
        
        if not header_row:
            return None
            
        # Process data rows after header
        header_index = rows.index(header_row)
        for row in rows[header_index + 1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 12:  # Need at least 12 columns for full data
                places.append({
                    "pos":        _safe_int(cells[0]),
                    "horse_no":   cells[1],
                    "horse_name": cells[2],
                    "jockey":     cells[3],
                    "trainer":    cells[4],
                    "win_odds":   _safe_float(cells[11]),
                    "time":       cells[10] if len(cells) > 10 else "",
                    "margin":     cells[8] if len(cells) > 8 else "",
                })
            elif len(cells) >= 11:
                places.append({
                    "pos":        _safe_int(cells[0]),
                    "horse_no":   cells[1],
                    "horse_name": cells[2],
                    "jockey":     cells[3],
                    "trainer":    cells[4],
                    "win_odds":   _safe_float(cells[-1]),
                    "time":       cells[-2] if len(cells) > 10 else "",
                    "margin":     cells[8] if len(cells) > 8 else "",
                })

        # Parse dividends table if present (look for dividend tables nearby)
        dividends = _parse_dividends(block)

        return {
            "race_no":   1,  # Will be updated by caller if needed
            "race_name": "",
            "distance":  0,
            "surface":   "",
            "class_":    "",
            "places":    places,
            "dividends": dividends,
        } if places else None
    except Exception as e:
        print(f"   ⚠ Parse error: {e}")
        return None


def _parse_dividends(block) -> dict:
    """Extract dividend payouts from result block."""
    divs = {
        "win": [], "place": [], "quinella": [],
        "forecast": [], "tierce": [], "trio": [],
        "first4": [], "quartet": [],
    }
    try:
        dividend_map = {
            "win":     ["win", "獨贏"],
            "place":   ["place", "位置"],
            "quinella": ["quinella", "連贏", "位置q", "位置Q"],
            "forecast": ["forecast", "二重彩"],
            "tierce":  ["tierce", "三重彩"],
            "trio":    ["trio", "三連環"],
            "first4":  ["first4", "四連環", "四連贏"],
            "quartet": ["quartet", "四重彩"],
        }

        tables = [block] if getattr(block, "name", "") == "table" else block.select("table")
        for table in tables:
            header_text = " ".join(
                th.get_text(strip=True) for th in table.select("thead tr th, thead tr td")
            ).strip().lower()
            if "dividend" not in header_text and "派彩" not in header_text:
                continue

            current_key = None
            for row in table.select("tbody tr"):
                cells = [td.get_text(strip=True) for td in row.select("td")]
                if not cells:
                    continue

                candidate = cells[0].strip()
                if candidate:
                    norm = candidate.lower().replace(" ", "")
                    match_key = None
                    for key, aliases in dividend_map.items():
                        if any(alias in norm for alias in aliases):
                            match_key = key
                            break
                    if match_key:
                        current_key = match_key

                if current_key is None:
                    continue

                if len(cells) >= 3:
                    combo = cells[-2].strip()
                    dividend = _safe_float(cells[-1])
                elif len(cells) == 2:
                    combo = cells[0].strip()
                    dividend = _safe_float(cells[1])
                else:
                    continue

                divs[current_key].append({
                    "combo": combo,
                    "dividend": dividend,
                })
    except Exception:
        pass
    return divs

# ═══════════════════════════════════════════════════════════════════════════════
# 2. SAVE RESULTS XLSX → data/results/YYYY-MM-DD_VN.xlsx
# ═══════════════════════════════════════════════════════════════════════════════

def save_results_xlsx(all_results: list, race_date: str,
                      venue: str, dirs: dict) -> Path:
    """
    Save full race results to XLSX.
    Sheets:
      - Results    (all finishers flat, all races)
      - R1…RN      (per-race finishing order)
      - Dividends  (all bet types, all races)
    """
    tag  = race_date.replace("/", "-")
    path = dirs["results"] / f"{tag}_{venue}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:

        # ── Results flat ──────────────────────────────────────────────────────
        flat_rows = []
        for race in all_results:
            for h in race["places"]:
                flat_rows.append({
                    "race_no":    race["race_no"],
                    "race_name":  race.get("race_name", ""),
                    "distance":   race.get("distance", ""),
                    "surface":    race.get("surface", ""),
                    "class_":     race.get("class_", ""),
                    **h,
                })
        if flat_rows:
            pd.DataFrame(flat_rows).to_excel(
                writer, sheet_name="Results", index=False)

        # ── Per-race sheets ───────────────────────────────────────────────────
        for race in all_results:
            if race["places"]:
                rows = []
                for h in race["places"]:
                    rows.append({
                        "pos":       h.get("pos", ""),
                        "horse_no":  h.get("horse_no", ""),
                        "horse_name":h.get("horse_name", ""),
                        "jockey":    h.get("jockey", ""),
                        "trainer":   h.get("trainer", ""),
                        "win_odds":  h.get("win_odds", ""),
                        "time":      h.get("time", ""),
                        "margin":    h.get("margin", ""),
                        "bet_type":  "",
                        "combo":     "",
                        "dividend":  "",
                    })

                if race.get("dividends"):
                    for bet_type, payouts in race["dividends"].items():
                        for p in payouts:
                            rows.append({
                                "pos":       "",
                                "horse_no":  "",
                                "horse_name": "",
                                "jockey":    "",
                                "trainer":   "",
                                "win_odds":  "",
                                "time":      "",
                                "margin":    "",
                                "bet_type":  bet_type,
                                "combo":     p.get("combo", ""),
                                "dividend":  p.get("dividend", ""),
                            })

                pd.DataFrame(rows).to_excel(
                    writer, sheet_name=f"R{race['race_no']}", index=False)

        # ── Dividends ─────────────────────────────────────────────────────────
        div_rows = []
        for race in all_results:
            for bet_type, payouts in race["dividends"].items():
                for p in payouts:
                    div_rows.append({
                        "race_no":  race["race_no"],
                        "bet_type": bet_type,
                        **p,
                    })
        if div_rows:
            pd.DataFrame(div_rows).to_excel(
                writer, sheet_name="Dividends", index=False)

    print(f"  ✓ Results XLSX  → {path}")
    return path

# ═══════════════════════════════════════════════════════════════════════════════
# 3. SAVE CROSSCHECK JSON
# ═══════════════════════════════════════════════════════════════════════════════

def save_crosscheck_json(all_results: list, race_date: str,
                          venue: str, dirs: dict) -> Path:
    tag        = race_date.replace("/", "-")
    pred_path  = dirs["pred"] / f"{tag}_{venue}.xlsx"
    cross_path = dirs["results"] / f"{tag}_{venue}_crosscheck.json"

    if not pred_path.exists():
        print(f"  ⚠ No predictions file found: {pred_path} — skipping crosscheck JSON")
        return None

    try:
        summary_df = pd.read_excel(pred_path, sheet_name="Summary", dtype=str)
    except Exception as exc:
        print(f"  ⚠ Failed to read prediction summary: {exc}")
        return None

    results_map = {}
    for race in all_results:
        valid_places = [
            h for h in race["places"]
            if str(h.get("horse_no", "")).strip() != ""
            and str(h.get("horse_no", "")).lower() != "nan"
            and int(h.get("pos", 0)) > 0
        ]
        results_map[race["race_no"]] = sorted(valid_places, key=lambda h: int(h.get("pos", 0)))

    rows = []
    total_hits = 0
    total_picks = 0

    for _, row in summary_df.iterrows():
        race_no = int(row["race_no"])
        prediction = [str(row.get(f"P{i}_no", "")).strip() for i in range(1, PLACES + 1)]
        prediction_string = "-".join(prediction)

        actual_entries = []
        race_hits = 0
        for place in results_map.get(race_no, [])[:PLACES]:
            horse_no = str(place.get("horse_no", "")).strip()
            hit = horse_no != "" and horse_no in prediction
            if hit:
                race_hits += 1
            actual_entries.append({
                "pos": int(place.get("pos", 0)),
                "horse_no": horse_no,
                "horse_name": place.get("horse_name", ""),
                "hit": hit,
            })

        total_hits += race_hits
        total_picks += PLACES

        actual_string = "-".join([
            f"{entry['horse_no']}{'✅' if entry['hit'] else ''}"
            for entry in actual_entries
        ])

        rows.append({
            "race_no": race_no,
            "prediction": prediction,
            "prediction_string": prediction_string,
            "actual": actual_entries,
            "actual_string": actual_string,
            "hits": race_hits,
            "total": PLACES,
        })

    summary = {
        "total_hits": total_hits,
        "total_picks": total_picks,
        "hit_rate": round(total_hits / total_picks * 100, 1) if total_picks else 0,
    }

    crosscheck_data = {
        "date": race_date,
        "venue": venue,
        "file": f"{tag}_{venue}.xlsx",
        "summary": summary,
        "races": rows,
    }

    cross_path.parent.mkdir(parents=True, exist_ok=True)
    cross_path.write_text(json.dumps(crosscheck_data, ensure_ascii=False, indent=2))
    print(f"  ✓ Crosscheck JSON → {cross_path}")
    return cross_path

# ═══════════════════════════════════════════════════════════════════════════════
# 4. MERGE — fill actual_pos + hit into predictions XLSX
# ═══════════════════════════════════════════════════════════════════════════════

def merge_predictions(race_date: str, venue: str,
                      all_results: list, dirs: dict) -> Path:
    """
    Load predictions XLSX → fill actual_pos + hit columns → save back.
    hit = Y  if horse finished in top PLACES
    hit = N  if horse did not finish in top PLACES
    """
    tag       = race_date.replace("/", "-")
    pred_path = dirs["pred"] / f"{tag}_{venue}.xlsx"

    if not pred_path.exists():
        print(f"  ⚠ No predictions file found: {pred_path}")
        return None

    # Build results lookup: {race_no: {horse_no: actual_pos}}
    results_map = {}
    for race in all_results:
        rno = race["race_no"]
        results_map[rno] = {
            str(h["horse_no"]): h["pos"]
            for h in race["places"]
        }

    # Load predictions sheet
    df = pd.read_excel(pred_path, sheet_name="Predictions", dtype=str)

    def get_actual(row):
        rno    = str(row["race_no"])
        hno    = str(row["horse_no"])
        rmap   = results_map.get(int(rno), {})
        return str(rmap.get(hno, ""))

    def get_hit(row):
        actual = row["actual_pos"]
        if actual == "" or actual == "nan":
            return ""
        try:
            return "Y" if int(actual) <= PLACES else "N"
        except:
            return ""

    df["actual_pos"] = df.apply(get_actual, axis=1)
    df["hit"]        = df.apply(get_hit,    axis=1)

    # Write back — preserve all other sheets
    with pd.ExcelWriter(pred_path, engine="openpyxl", mode="a",
                        if_sheet_exists="replace") as writer:
        df.to_excel(writer, sheet_name="Predictions", index=False)

    print(f"  ✓ Predictions updated → {pred_path}")
    return pred_path

# ═══════════════════════════════════════════════════════════════════════════════
# 4. ACCURACY REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def print_accuracy_report(race_date: str, venue: str, dirs: dict):
    """
    Print hit rate summary for today's meeting.
    Also appends one row to data/backtest_log.csv.
    """
    tag       = race_date.replace("/", "-")
    pred_path = dirs["pred"] / f"{tag}_{venue}.xlsx"

    if not pred_path.exists():
        return

    df = pd.read_excel(pred_path, sheet_name="Predictions", dtype=str)

    total = len(df)
    hits  = len(df[df["hit"] == "Y"])
    p1    = len(df[(df["pos"].astype(str) == "1") & (df["hit"] == "Y")])
    races = df["race_no"].nunique()

    hit_rate = round(hits / total * 100, 1) if total else 0

    print(f"\n{'═'*55}")
    print(f"  ACCURACY REPORT — {race_date} {venue}")
    print(f"{'═'*55}")
    print(f"  Races          : {races}")
    print(f"  Total picks    : {total}  ({races} races × {PLACES} picks)")
    print(f"  Hits (top {PLACES})   : {hits} / {total}  ({hit_rate}%)")
    print(f"  P1 winners     : {p1} / {races}")
    print(f"{'═'*55}")

    # Per-race breakdown
    print(f"\n  {'Race':<8} {'P1':>4} {'P2':>4} {'P3':>4} {'P4':>4}  {'Hits'}")
    print(f"  {'─'*40}")
    for rno in sorted(df["race_no"].unique()):
        rdf = df[df["race_no"] == rno]
        row_hits = []
        for pos in range(1, PLACES + 1):
            sub = rdf[rdf["pos"].astype(str) == str(pos)]
            if sub.empty:
                row_hits.append("─")
            else:
                h = sub.iloc[0]["hit"]
                row_hits.append("✓" if h == "Y" else "✗")
        hits_str = "  ".join(row_hits)
        print(f"  R{str(rno):<7} {hits_str}")

    # Append to backtest log
    log_path = Path("data/backtest_log.csv")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_row = pd.DataFrame([{
        "date":      race_date,
        "venue":     venue,
        "races":     races,
        "picks":     total,
        "hits":      hits,
        "hit_rate%": hit_rate,
        "p1_wins":   p1,
        "run_at":    datetime.now().strftime("%Y-%m-%d %H:%M"),
    }])
    write_header = not log_path.exists()
    log_row.to_csv(log_path, mode="a", index=False, header=write_header)
    print(f"\n  ✓ Backtest log updated → {log_path}")

def print_crosscheck_table(race_date: str, venue: str, dirs: dict):
    """Print crosscheck table comparing predictions vs actual results."""
    import json
    from pathlib import Path

    tag = race_date.replace("/", "-")
    cross_path = dirs["results"] / f"{tag}_{venue}_crosscheck.json"

    if not cross_path.exists():
        return  # No crosscheck data available

    try:
        crosscheck_data = json.loads(cross_path.read_text())
    except Exception as e:
        print(f"  ⚠ Failed to load crosscheck data: {e}")
        return

    print(f"\n🏇 CROSSCHECK - {tag} {venue}")
    print(f"=================================================================\n")

    print(f"R    Prediction     Actual                 Hits")
    print(f"-----------------------------------------------------------------")

    for race in crosscheck_data["races"]:
        race_no = race["race_no"]
        prediction = race["prediction_string"]
        actual = race["actual_string"]
        hits = race["hits"]
        total = race["total"]

        print(f"R{race_no:<2} {prediction:<15} {actual:<22} {hits}/{total}")

    print(f"-----------------------------------------------------------------")
    summary = crosscheck_data["summary"]
    total_hits = summary["total_hits"]
    total_picks = summary["total_picks"]
    hit_rate = summary["hit_rate"]
    print(f"Total: {total_hits}/{total_picks} = {hit_rate}%")
    print()

# ═══════════════════════════════════════════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run(race_date: str, venue: str):
    print(f"\n{'═'*55}")
    print(f"  HKJC REVIEW ENGINE")
    print(f"  Race date : {race_date}  |  Venue : {venue}")
    print(f"  Run time  : {datetime.now().strftime('%Y-%m-%d %H:%M')} HKT")
    print(f"{'═'*55}\n")

    dirs = setup_dirs()

    # Add results folder
    dirs["results"] = Path("data/results")
    dirs["results"].mkdir(parents=True, exist_ok=True)

    # [1/4] Fetch results
    print("  [1/4] Fetching race results...")
    try:
        all_results = fetch_results(race_date, venue, dirs)
    except ValueError as exc:
        msg = str(exc)
        if "No race results yet" in msg or "No results parsed for" in msg:
            print(f"  ⚠ Review skipped: {msg}")
            return
        raise

    # [2/4] Save results XLSX
    print("  [2/4] Saving results XLSX...")
    save_results_xlsx(all_results, race_date, venue, dirs)

    # [3/4] Merge into predictions
    print("  [3/4] Merging actuals into predictions XLSX...")
    merge_predictions(race_date, venue, all_results, dirs)

    # [3.1/4] Save cross-check data for web
    print("  [3.1/4] Saving crosscheck JSON for dashboard...")
    save_crosscheck_json(all_results, race_date, venue, dirs)

    # [4/4] Print crosscheck table and accuracy report
    print("  [4/4] Crosscheck and accuracy report...")
    print_crosscheck_table(race_date, venue, dirs)
    print_accuracy_report(race_date, venue, dirs)

    print(f"\n{'═'*55}")
    tag = race_date.replace('/', '-')
    print(f"  Files updated:")
    print(f"    data/results/{tag}_{venue}.xlsx")
    print(f"    data/predictions/{tag}_{venue}.xlsx")
    print(f"    data/backtest_log.csv")
    print(f"{'═'*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Review Engine")
    parser.add_argument("--date",  default=datetime.now().strftime("%Y/%m/%d"),
                        help="Race date YYYY/MM/DD (default: today)")
    parser.add_argument("--venue", default="ST", choices=["ST", "HV"],
                        help="Venue: ST or HV (default: ST)")
    args = parser.parse_args()
    run(args.date, args.venue)
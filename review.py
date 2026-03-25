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

    url  = (f"{URLS['race_results']}"
            f"?racedate={race_date.replace('/', '%2F')}&Racecourse={venue}")
    print(f"   Rendering results: {url}")
    html = _render_page(url, wait_ms=5000)
    soup = BeautifulSoup(html, "html.parser")

    all_races = []
    race_blocks = soup.select(".race-result, .raceResult, table.resultTable")

    for block in race_blocks:
        result = _parse_result_block(block)
        if result:
            all_races.append(result)

    if not all_races:
        if any(msg in html for msg in [
            "For the actual race results, the customers should refer to Real Replay videos",
            "No results available",
            "To keep pace with the latest results",
            "No results found"
        ]):
            raise ValueError(
                f"No race results yet for {race_date} {venue} (page loaded, but no data ready).\n"
                f"  URL: {url}\n"
                f"  HTML length: {len(html)} chars\n"
            )

        raise ValueError(
            f"No results parsed for {race_date} {venue}.\n"
            f"  URL: {url}\n"
            f"  HTML length: {len(html)} chars\n"
            f"  → Check CSS selectors in _parse_result_block()."
        )

    cache.write_text(json.dumps(all_races, ensure_ascii=False, indent=2))
    print(f"   → {len(all_races)} race results fetched")
    return all_races


def _parse_result_block(block) -> dict:
    """Parse one race result block. Returns None if unparseable."""
    try:
        places = []
        for row in block.select("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 6:
                places.append({
                    "pos":        _safe_int(cells[0]),
                    "horse_no":   cells[1],
                    "horse_name": cells[2],
                    "jockey":     cells[3],
                    "trainer":    cells[4],
                    "win_odds":   _safe_float(cells[5]),
                    "time":       cells[6] if len(cells) > 6 else "",
                    "margin":     cells[7] if len(cells) > 7 else "",
                })

        # Parse dividends table if present
        dividends = _parse_dividends(block)

        return {
            "race_no":   1,
            "race_name": "",
            "distance":  0,
            "surface":   "",
            "class_":    "",
            "places":    places,
            "dividends": dividends,
        } if places else None
    except Exception:
        return None


def _parse_dividends(block) -> dict:
    """Extract dividend payouts from result block."""
    divs = {
        "win": [], "place": [], "quinella": [],
        "forecast": [], "tierce": [], "trio": [],
        "first4": [], "quartet": [],
    }
    try:
        for row in block.select(".dividend tr, table.divTable tr"):
            cells = [td.get_text(strip=True) for td in row.select("td")]
            if len(cells) >= 2:
                bet_type = cells[0].lower().replace(" ", "")
                for key in divs:
                    if key in bet_type:
                        divs[key].append({
                            "combo":    cells[1] if len(cells) > 1 else "",
                            "dividend": _safe_float(cells[2]) if len(cells) > 2 else 0.0,
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
                pd.DataFrame(race["places"]).to_excel(
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
# 3. MERGE — fill actual_pos + hit into predictions XLSX
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

    # [4/4] Print accuracy report
    print("  [4/4] Accuracy report...")
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
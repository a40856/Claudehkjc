#!/usr/bin/env python3
"""
scripts/fetch_dividends.py — Scrape post-race dividends from HKJC results pages

For each (date, venue), scrape every race's dividend table:
  - WIN (獨贏)              — single dividend
  - PLACE (位置)            — up to 3 dividends (one per top-3 finisher)
  - QUINELLA (連贏)         — one dividend per winning pair
  - QUINELLA PLACE (位置Q)  — up to 3 dividends
  - FORECAST (二重彩)       — one dividend per ordered pair
  - TIERCE (三重彩)         — one dividend per ordered triple
  - TRIO (三連環)           — one dividend per unordered triple
  - FIRST 4 (四連環)        — one dividend per ordered quadruple
  - QUARTET (四重彩)        — one dividend per unordered quadruple

Output: data/odds/<tag>_post.json
  {
    "tag": "2026-04-29_HV",
    "venue": "HV",
    "race_date": "2026/04/29",
    "fetched_at": "...",
    "races": {
      "1": {
        "win":     [{"combo": "4", "dividend": 54.00}],
        "place":   [{"combo": "4", "dividend": 20.50}, {"combo": "3", ...}, ...],
        "quinella":[{"combo": "3,4", "dividend": 505.00}],
        ...
      }
    }
  }

Usage:
    python scripts/fetch_dividends.py --date 2026/04/29 --venue HV
    python scripts/fetch_dividends.py --all       # every meeting with stored results
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # make config.py importable

from config import HEADERS

ROOT = Path(__file__).resolve().parent.parent  # re-define after sys.path edit
RAW_DIR     = ROOT / "data" / "raw"
RESULTS_DIR = ROOT / "data" / "results"
ODDS_DIR    = ROOT / "data" / "odds"
ODDS_DIR.mkdir(parents=True, exist_ok=True)

# Pool label → internal key. Only WIN / PLACE / QUINELLA / QUINELLA_PLACE
# (forecast / tierce / trio / first4 / quartet intentionally skipped — too noisy
# and we only have data on 4 horses per race anyway).
POOL_MAP = {
    "獨贏":   "win",
    "位置":   "place",
    "連贏":   "quinella",
    "位置Q": "quinella_place",
}


# ── Per-race scraping ────────────────────────────────────────────────────────

def fetch_one_race_dividends(race_date: str, venue: str, race_no: int,
                             page=None) -> dict:
    """
    Load one race result page from HKJC and parse the dividend table.
    If `page` is given (a Playwright page), reuse it instead of launching
    a fresh browser — caller is responsible for closing the browser.
    Returns dict keyed by pool name (win/place/quinella/...) with lists of
    {"combo": str, "dividend": float}.
    """
    from playwright.sync_api import sync_playwright
    url = (f"https://racing.hkjc.com/zh-hk/local/information/localresults"
           f"?racedate={race_date.replace('/', '%2F')}&Racecourse={venue}&RaceNo={race_no}")

    close_after = False
    if page is None:
        ctx = sync_playwright().start()
        browser = ctx.chromium.launch(headless=True)
        page = browser.new_page(extra_http_headers=HEADERS)
        close_after = True

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try: page.wait_for_load_state("networkidle", timeout=10000)
        except: pass
        time.sleep(0.8)
        html = page.content()
    finally:
        if close_after:
            page.context.browser.close()

    return _parse_dividends_html(html)


def _parse_dividends_html(html: str) -> dict:
    """
    Parse the dividend table from the HKJC results page HTML.

    Structure (2026+):
      <table> <tr><td colspan="3">派彩</td></tr>
              <tr><td>彩池</td><td>勝出組合</td><td>派彩 (HK$)</td></tr>
              <tr><td rowspan="1">獨贏</td><td>4</td><td class="f_tar">54.00</td></tr>
              <tr><td rowspan="3">位置</td><td>4</td><td>20.50</td></tr>
              <tr>                <td>3</td><td>45.50</td></tr>
              <tr>                <td>5</td><td>30.50</td></tr>
              <tr><td rowspan="1">連贏</td><td>3,4</td><td>505.00</td></tr>
              ...
      </table>
    """
    out = {k: [] for k in set(POOL_MAP.values())}

    # Bail on bot wall
    if "system not ready" in html.lower():
        return out

    # Walk every <tr> in the document; only those that contain "派彩" context
    # (combo + dividend cells) are dividend rows.
    # We'll detect a "dividend row" by: it has at least 2 cells AND either
    # (a) the first cell is a known pool label, or
    # (b) we are currently inside a multi-row pool block.

    current_pool = None
    for row_match in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, flags=re.DOTALL):
        row_html = row_match.group(1)
        cells_raw = re.findall(r'<td[^>]*>(.*?)</td>', row_html, flags=re.DOTALL)
        if len(cells_raw) < 2:
            continue
        cells_text = [re.sub(r'<[^>]+>', '', c).strip() for c in cells_raw]

        # Skip header row: "彩池 / 勝出組合 / 派彩"
        if cells_text[0] in ("彩池", "Pool"):
            current_pool = None
            continue

        # If first cell is a known pool label, switch context
        if cells_text[0] in POOL_MAP:
            current_pool = POOL_MAP[cells_text[0]]
            cells_text = cells_text[1:]
        elif cells_text[0] in {"二重彩", "三重彩", "單T", "三連環", "四連環", "四重彩"}:
            # Unknown pool (we don't capture forecast / tierce / trio / f4 / quartet).
            # Stop absorbing data — the next rows are for those pools.
            current_pool = None
            continue

        if current_pool is None or len(cells_text) < 2:
            continue

        # Last two cells = combo, dividend
        combo = cells_text[-2].strip()
        div_raw = cells_text[-1].strip()
        try:
            dividend = float(div_raw.replace(",", ""))
        except ValueError:
            continue

        if combo:
            out[current_pool].append({"combo": combo, "dividend": dividend})

    return out


# ── Meeting-level fetch ──────────────────────────────────────────────────────

def fetch_meeting_dividends(race_date: str, venue: str,
                            race_nos: list[int] | None = None) -> dict:
    """
    Fetch dividend data for every race in a meeting. Discovers race count
    from existing raw/ directory (or uses race_nos if given).

    Reuses one Playwright browser instance across all races in the meeting
    (launches once per meeting, not once per race) — saves ~3-5s/race.
    """
    if race_nos is None:
        raw_p = RAW_DIR / f"{race_date.replace('/', '-')}_{venue}.xlsx"
        if raw_p.exists():
            try:
                import pandas as pd
                rdf = pd.read_excel(raw_p, sheet_name="RaceCard")
                race_nos = sorted(int(r) for r in rdf["race_no"].unique())
            except ImportError:
                # No pandas — fall back to default 1-12 (most meetings are 9-11 races)
                race_nos = list(range(1, 12))
        else:
            race_nos = list(range(1, 12))

    result = {
        "tag": race_date.replace("/", "-") + "_" + venue,
        "venue": venue,
        "race_date": race_date,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "races": {},
    }

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(extra_http_headers=HEADERS)
            for race_no in race_nos:
                print(f"  R{race_no}...", end="", flush=True)
                divs = fetch_one_race_dividends(race_date, venue, race_no, page=page)
                any_data = any(divs[k] for k in divs)
                result["races"][str(race_no)] = divs
                if any_data:
                    pools = [k for k, v in divs.items() if v]
                    print(f" ✓ {', '.join(pools)}")
                else:
                    print(" (no dividends)")
        finally:
            browser.close()
    return result


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Fetch HKJC post-race dividends.")
    ap.add_argument("--date",  help="Race date YYYY/MM/DD")
    ap.add_argument("--venue", help="ST or HV")
    ap.add_argument("--all",   action="store_true",
                    help="Fetch dividends for every meeting with results XLSX")
    ap.add_argument("--out",   help="Output JSON path (default: data/odds/<tag>_post.json)")
    args = ap.parse_args()

    if args.all:
        # Every meeting that has both raw and results
        meetings = sorted({p.stem for p in RAW_DIR.glob("*.xlsx")}
                          & {p.stem for p in RESULTS_DIR.glob("*.xlsx")})
        targets = [(m.split("_")[0].replace("-", "/"), m.split("_")[1]) for m in meetings]
    elif args.date and args.venue:
        targets = [(args.date, args.venue)]
    else:
        ap.error("Provide --date + --venue, or --all")

    ok = 0
    for date_str, venue in targets:
        tag = date_str.replace("/", "-") + "_" + venue
        out = Path(args.out) if args.out else (ODDS_DIR / f"{tag}_post.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f"\nFetching dividends: {tag}")
        try:
            res = fetch_meeting_dividends(date_str, venue)
            out.write_text(json.dumps(res, indent=2, ensure_ascii=False))
            print(f"  ✓ saved → {out}")
            ok += 1
        except Exception as e:
            print(f"  ✗ error: {e}")
    print(f"\nDone: {ok}/{len(targets)} meetings")


if __name__ == "__main__":
    main()

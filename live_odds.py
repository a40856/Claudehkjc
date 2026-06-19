"""
live_odds.py — Pre-race odds fetcher using Playwright (HKJC JSON API is JS-rendered)

Fetches WIN / PLA / QIN / QPL odds for every race in a meeting.
Saves one JSON per meeting at data/odds/<tag>_pre.json.

Why Playwright (not requests):
  HKJC's old /racing/getJSON.aspx endpoint now returns a Cloudflare "system not
  ready" page for non-browser User-Agents. The actual odds live behind a
  client-rendered SPA, so we use the same headless Chromium stack as predict.py.

Usage:
    python live_odds.py --date 2026/06/21 --venue ST
    python live_odds.py --date 2026/06/21 --venue ST --races 1 2 3    # only specific races
    python live_odds.py --date 2026/06/21 --venue ST --out data/odds/test.json

The resulting JSON shape:
{
  "tag": "2026-06-21_ST",
  "venue": "ST",
  "race_date": "2026/06/21",
  "fetched_at": "2026-06-19T15:30:00",
  "races": {
    "1": {
      "win": {"1": 5.4, "2": 8.0, "3": 12.0, ...},
      "pla": {"1": 1.9, "2": 2.5, ...},
      "qin": {"1,2": 24.5, "1,3": 35.0, ...},
      "qpl": {"1,2": 5.2, "1,3": 7.1, ...},
      "race_name": "...",
      "race_time": "16:00"
    },
    "2": {...}
  }
}
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from config import HEADERS, OUTPUT_DIR

ROOT          = Path(__file__).resolve().parent
ODDS_DIR      = ROOT / "data" / "odds"

ODDS_DIR.mkdir(parents=True, exist_ok=True)

# HKJC pages we hit (per pool type). Each returns a single SPA-rendered page
# that loads the JSON via XHR; we let Playwright wait for the data to populate.
WPQ_URL = "https://bet.hkjc.com/ch/racing/wpq/{date}/{venue}/{race_no}"

# HKJC pool shorthand → human label
POOL_LABEL = {
    "win": "WIN",
    "pla": "PLA",
    "qin": "QIN",
    "qpl": "QPL",
}


# ── Low-level: load one race page and extract odds from the rendered DOM ─────

def _fetch_one_race_page(race_date: str, venue: str, race_no: int, pool: str):
    """
    Open the WPQ page for one (date, venue, race, pool) and extract the odds table.

    HKJC renders a different table per pool (WIN/PLA vs QIN/QPL) on different
    tabs. We click the right tab and read the cells.

    Returns dict: {horse_no: float_odds} for WIN/PLA, or {"a,b": float_odds} for QIN/QPL.
    The special key "__horse_names__" maps to {horse_no: name} if extracted.
    """
    from playwright.sync_api import sync_playwright

    date_compact = race_date.replace("/", "")
    url = WPQ_URL.format(date=date_compact, venue=venue, race_no=race_no)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(extra_http_headers=HEADERS)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("table.oddsTable, .race-odds-table, [data-pool], .rc-odds-row-m",
                                        timeout=15000)
            except Exception:
                pass
            # Click the pool tab if it exists. HKJC 2026 SPA tabs:
            #  - "獨贏" / "位置"   (default visible)     — WIN/PLA
            #  - "連贏及位置Q"      (combined view)       — QIN + QPL both render
            #  - "獨贏及位置"       (combined WIN+PLA)    — sometimes default
            pool_tab_labels = {
                "win":  ["獨贏",  "獨贏及位置"],
                "pla":  ["位置",  "獨贏及位置"],
                "qin":  ["連贏及位置Q", "連贏"],
                "qpl":  ["連贏及位置Q", "位置Q"],
            }
            for tab_label in pool_tab_labels.get(pool, []):
                try:
                    page.get_by_role("link", name=tab_label).first.click(timeout=2000)
                    time.sleep(0.5)
                    break
                except Exception:
                    try:
                        page.get_by_text(tab_label, exact=False).first.click(timeout=2000)
                        time.sleep(0.5)
                        break
                    except Exception:
                        pass
            time.sleep(1.0)
            html = page.content()
        finally:
            browser.close()

    return _parse_odds_html(html, pool)


def _parse_odds_html(html: str, pool: str) -> dict:
    """
    Extract {horse_no: odds} (WIN/PLA) or {"a,b": odds} (QIN/QPL) from rendered HTML.
    Falls back to empty dict if the page didn't render (system not ready, race cancelled, etc.)

    HKJC page structure (2026+ SPA):

    WIN / PLA (default tab):
      <tr class="rc-odds-row-m ...">
        <td class="no" id="runnerNo_R_H">H</td>
        <td id="horseName_R_H"><a>NAME</a></td>
        <td class="rc-checkbox rc-odds-m">
          <div id="odds_WIN_R_H"><a>5.3</a></div>
          <div id="odds_PLA_R_H"><a>2.1</a></div>
        </td>
      </tr>

    QIN / QPL (after clicking the "連贏及位置Q" tab — both tables render):
      <header>連贏 最高20</header>
      <table class="wpq-odds-table-top20">
        <tr>
          <td><div class="cell-label">14-21</div>
              <div class="cell-value"><span class="table-odds">16</span></div></td>
          ...
        </tr>
      </table>
      <header>位置Q 最高20</header>
      <table class="wpq-odds-table-top20">... (same format) ...</table>
    """
    import re

    # Bail if we got the bot-wall page
    if "system not ready" in html.lower() or "please try again later" in html.lower():
        return {}

    pool_upper = pool.upper()
    out = {}

    # Always extract horse names from the rendered page (regardless of pool).
    # <td id="horseName_R_H"><a>NAME</a></td>
    name_pat = re.compile(
        r'<td id="horseName_\d+_(\d+)"[^>]*>\s*<a[^>]*>([^<]+)</a>',
    )
    horse_names = {}
    for nm in name_pat.finditer(html):
        try:
            horse_names[nm.group(1)] = nm.group(2).strip()
        except Exception:
            pass

    if pool in ("win", "pla"):
        # Pattern: id="odds_WIN_1_1" ... <a>5.3</a>
        pat = re.compile(
            rf'id="odds_{pool_upper}_\d+_(\d+)".{{0,200}}?<a[^>]*>([^<]+)</a>',
            flags=re.DOTALL,
        )
        out = {}
        for m in pat.finditer(html):
            horse_no = m.group(1)
            try:
                out[horse_no] = float(m.group(2).strip())
            except ValueError:
                pass
        # Attach horse names so caller can persist them
        out["__horse_names__"] = horse_names
        return out

    # QIN / QPL — find the table whose preceding header matches this pool.
    # Headers are "連贏 最高20" (QIN) and "位置Q 最高20" (QPL).
    pool_header = "連贏" if pool == "qin" else "位置Q"
    # Walk through document finding each <table class="wpq-odds-table-top20">
    # and only collect from the one whose preceding <header> matches.
    out = {}
    for table_match in re.finditer(
        r'<header>' + re.escape(pool_header) + r'[^<]*</header>'
        r'.*?<table class="wpq-odds-table-top20">(.*?)</table>',
        html, flags=re.DOTALL,
    ):
        block = table_match.group(1)
        # Each cell has <div class="cell-label">a-b</div>...<span class="table-odds">X</span>
        for cell in re.finditer(
            r'<div class="cell-label">(\d+)-(\d+)</div>.*?'
            r'<span class="[^"]*table-odds[^"]*">([^<]+)</span>',
            block, flags=re.DOTALL,
        ):
            a, b, odds = cell.group(1), cell.group(2), cell.group(3).strip()
            try:
                out[f"{a},{b}"] = float(odds)
            except ValueError:
                pass
        if out:
            return out
    return out


# ── Main fetch: pull all races × all pools for one meeting ──────────────────

def fetch_meeting_pre_odds(race_date: str, venue: str,
                           race_nos: list[int] | None = None,
                           pools: list[str] = ("win", "pla", "qin", "qpl"),
                           page=None) -> dict:
    """
    Fetch pre-race odds for every race in a meeting, for the given pools.
    If a race_no list is given, only fetch those races.

    Slow: ~2-3s per page × N races × M pools. For a 9-race meeting with all
    4 pools = ~60-90s.
    """
    if race_nos is None:
        # Try to discover race numbers by scraping the meeting index page once.
        race_nos = _discover_race_numbers(race_date, venue, page=page) or list(range(1, 10))

    result = {
        "tag": race_date.replace("/", "-") + "_" + venue,
        "venue": venue,
        "race_date": race_date,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "pools": list(pools),
        "races": {},
    }

    for race_no in race_nos:
        race_block = {"race_no": race_no}
        any_data = False
        horse_names = {}  # collect once (same on every pool page)
        for pool in pools:
            odds = _fetch_one_race_page(race_date, venue, race_no, pool)
            # Pull horse names out of the special key if present
            if isinstance(odds, dict) and "__horse_names__" in odds:
                horse_names.update(odds.pop("__horse_names__"))
            race_block[pool] = odds
            if odds:
                any_data = True
        race_block["horse_names"] = horse_names
        result["races"][str(race_no)] = race_block
        if not any_data:
            print(f"  ⚠ R{race_no}: no odds returned for any pool "
                  f"(meeting not yet open / race cancelled / bot wall)")

    return result


def _discover_race_numbers(race_date: str, venue: str, page=None) -> list[int] | None:
    """Try to figure out how many races the meeting has. Returns None on failure."""
    from playwright.sync_api import sync_playwright
    url = f"https://bet.hkjc.com/ch/racing/wpq/{race_date.replace('/','')}/{venue}/1"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                p_ = page or browser.new_page(extra_http_headers=HEADERS)
                p_.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
                # Race numbers appear as tabs/buttons (田, S2-S5, then 1..11)
                html = p_.content()
            finally:
                if page is None:
                    browser.close()
        nums = []
        for m in re.finditer(r">(S?[2-9]|1[0-2])</", html):
            try:
                n = int(m.group(1).lstrip("S"))
                if 1 <= n <= 14:
                    nums.append(n)
            except ValueError:
                pass
        return sorted(set(nums)) if nums else None
    except Exception:
        return None


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Fetch HKJC pre-race odds for a meeting.")
    ap.add_argument("--date",  required=True, help="Race date YYYY/MM/DD")
    ap.add_argument("--venue", required=True, choices=["ST", "HV"])
    ap.add_argument("--races", nargs="*", type=int,
                    help="Specific race numbers (default: all)")
    ap.add_argument("--pools", nargs="*",
                    choices=list(POOL_LABEL.keys()),
                    default=list(POOL_LABEL.keys()),
                    help="Odds pools to fetch (default: all)")
    ap.add_argument("--out", help="Output JSON path (default: data/odds/<tag>_pre.json)")
    ap.add_argument("--print", action="store_true",
                    help="Print fetched odds to stdout")
    args = ap.parse_args()

    tag = args.date.replace("/", "-") + "_" + args.venue
    out = Path(args.out) if args.out else (ODDS_DIR / f"{tag}_pre.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Fetching pre-race odds: {tag}")
    print(f"  pools: {args.pools}")
    print(f"  races: {args.races or '(auto-discover)'}")
    print()

    t0 = time.time()
    result = fetch_meeting_pre_odds(args.date, args.venue,
                                    race_nos=args.races, pools=args.pools)
    elapsed = time.time() - t0

    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n✓ Saved {out} ({elapsed:.0f}s)")

    if args.print:
        # pretty print a summary
        for rno, rblock in result["races"].items():
            any_data = any(rblock.get(p) for p in args.pools)
            if not any_data:
                print(f"\nR{rno}: (no odds)")
                continue
            print(f"\nR{rno}:")
            for p in args.pools:
                odds = rblock.get(p, {})
                if not odds:
                    continue
                label = POOL_LABEL[p]
                if p in ("win", "pla"):
                    preview = ", ".join(f"#{h}={o:.1f}" for h, o in list(odds.items())[:6])
                else:
                    preview = ", ".join(f"({k})={o:.1f}" for k, o in list(odds.items())[:6])
                print(f"  {label:<4} ({len(odds):>3}): {preview}{' ...' if len(odds) > 6 else ''}")


if __name__ == "__main__":
    main()

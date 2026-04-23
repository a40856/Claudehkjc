"""
HKJC Live Odds Scraper — Module 3 (hkjc_scraper.py)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Polls live win odds from HKJC race card page during race meetings.
Calculates EV (Expected Value) by comparing model win% vs market odds.
Writes race_data.json for the war-room dashboard.

Usage:
  python hkjc_scraper.py                      # auto-detect meeting + loop all races
  python hkjc_scraper.py --race 3             # single race
  python hkjc_scraper.py --interval 60        # poll every 60s (default)
  python hkjc_scraper.py --once               # single fetch, no loop

Requirements:
  race_card.json must exist (run race_card_scraper.py + v85_model.py first)
"""
from __future__ import annotations
import argparse, json, re, time, sys
from datetime import datetime
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[ERROR] Run: pip install requests beautifulsoup4")
    sys.exit(1)

# ─── CONFIG ──────────────────────────────────────────────────────
BASE         = "https://racing.hkjc.com"
RACE_CARD_F  = Path("race_card.json")
OUTPUT_F     = Path("race_data.json")
DEFAULT_POLL = 60   # seconds between polls

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://racing.hkjc.com/",
}


# ─── HTTP ─────────────────────────────────────────────────────────
def get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.text
            print(f"  [HTTP {r.status_code}] {url}")
        except Exception as e:
            print(f"  [ERROR] {e} (attempt {attempt+1}/{retries})")
        time.sleep(2)
    return None

def soup(html):
    return BeautifulSoup(html, 'html.parser') if html else None


# ─── LOAD MODEL DATA ──────────────────────────────────────────────
def load_race_card():
    if not RACE_CARD_F.exists():
        print(f"[ERROR] {RACE_CARD_F} not found.")
        print("  Run: python race_card_scraper.py")
        print("  Then: python v85_model.py")
        sys.exit(1)
    return json.loads(RACE_CARD_F.read_text())


# ─── LIVE ODDS SCRAPER ────────────────────────────────────────────
def fetch_live_odds(date_str, venue, race_no):
    """
    Scrape live win + place odds from HKJC race card page.
    During active racing the horse table includes Win Odds and Place Odds columns.
    Returns dict: { horse_no -> {"win_odds": float, "place_odds": float} }
    """
    html = get(f"{BASE}/en-us/local/information/racecard",
               params={"racedate": date_str, "Racecourse": venue, "RaceNo": str(race_no)})
    s = soup(html)
    if not s:
        return {}

    odds = {}
    for t in s.find_all('table'):
        hr = t.find('tr')
        if not hr:
            continue
        headers = [c.get_text(strip=True) for c in hr.find_all(['th', 'td'])]

        # During live racing, "Win Odds" column appears in the horse table
        if 'Draw' not in headers or 'Jockey' not in headers:
            continue

        win_idx   = headers.index('Win Odds')   if 'Win Odds'   in headers else None
        place_idx = headers.index('Place Odds') if 'Place Odds' in headers else None
        no_idx    = headers.index('Horse No.')  if 'Horse No.'  in headers else 0

        for row in t.find_all('tr')[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if not cells or not cells[0]:
                continue
            horse_no = cells[no_idx] if no_idx < len(cells) else ''
            if not horse_no:
                continue

            win_odds   = None
            place_odds = None

            if win_idx is not None and win_idx < len(cells):
                try:
                    win_odds = float(cells[win_idx])
                except:
                    pass

            if place_idx is not None and place_idx < len(cells):
                try:
                    place_odds = float(cells[place_idx])
                except:
                    pass

            # Fallback: scan all cells for decimal odds pattern (e.g. 4.5, 12.0)
            if win_odds is None:
                for i, cell in enumerate(cells):
                    if re.match(r'^\d{1,3}\.\d$', cell.strip()):
                        try:
                            val = float(cell)
                            if 1.0 <= val <= 999:
                                win_odds = val
                                break
                        except:
                            pass

            if horse_no:
                odds[horse_no] = {
                    "win_odds":   win_odds,
                    "place_odds": place_odds,
                }
        break

    return odds


# ─── EV CALCULATION ───────────────────────────────────────────────
def calc_ev(model_win_pct: float, win_odds: float) -> float:
    """
    EV = (model_win_pct * win_odds) - 1
    Positive EV = value bet (model says horse is underpriced by market)
    e.g. model=25%, odds=6.0 → EV = (0.25 * 6.0) - 1 = 0.50 = +50%
    """
    if not win_odds or win_odds <= 0:
        return None
    return round((model_win_pct / 100) * win_odds - 1, 4)


def implied_prob(win_odds: float) -> float:
    """Convert decimal odds to implied win probability %"""
    if not win_odds or win_odds <= 0:
        return None
    return round(100 / win_odds, 2)


# ─── PROCESS ONE RACE ─────────────────────────────────────────────
def process_race_odds(race: dict, date_str: str, venue: str) -> dict:
    """
    Merge live odds into race data. Calculate EV for each horse.
    Returns enriched race dict ready for race_data.json.
    """
    meta     = race.get('race_meta', {})
    horses   = race.get('horses', [])
    race_no  = meta.get('race_no', 0)

    live_odds = fetch_live_odds(date_str, venue, race_no)
    has_odds  = any(v.get('win_odds') for v in live_odds.values())

    result_horses = []
    for h in horses:
        if h.get('scratched'):
            result_horses.append({
                "no":        h.get('no'),
                "name":      h.get('name'),
                "scratched": True,
            })
            continue

        horse_no   = h.get('no', '')
        model_pct  = h.get('win_pct', 0)
        tier       = h.get('tier', 'D')
        raw_odds   = live_odds.get(str(horse_no), {})
        win_odds   = raw_odds.get('win_odds')
        place_odds = raw_odds.get('place_odds')

        ev          = calc_ev(model_pct, win_odds) if win_odds else None
        implied_pct = implied_prob(win_odds) if win_odds else None
        value_flag  = (ev is not None and ev > 0.15)   # EV > 15% = value

        result_horses.append({
            "no":           horse_no,
            "name":         h.get('name'),
            "draw":         h.get('draw'),
            "jockey":       h.get('jockey'),
            "trainer":      h.get('trainer'),
            "rating":       h.get('rating'),
            "carried_wt":   h.get('carried_wt'),
            "gear":         h.get('gear'),
            "last_6_runs":  h.get('last_6_runs'),
            "days_since_run": h.get('days_since_run'),
            # Model output
            "model_win_pct":    round(model_pct, 2),
            "tier":             tier,
            "score_breakdown":  h.get('score_breakdown', {}),
            # Live odds
            "win_odds":         win_odds,
            "place_odds":       place_odds,
            "implied_win_pct":  implied_pct,
            # EV
            "ev":               ev,
            "value_flag":       value_flag,
            "scratched":        False,
        })

    # Sort: value bets first, then by model win%
    active = [h for h in result_horses if not h.get('scratched')]
    active.sort(key=lambda x: (-(x.get('ev') or -99), -x.get('model_win_pct', 0)))
    scratched = [h for h in result_horses if h.get('scratched')]

    return {
        "race_no":    race_no,
        "race_name":  meta.get('race_name', ''),
        "start_time": meta.get('start_time', ''),
        "distance":   meta.get('distance', ''),
        "surface":    meta.get('surface', ''),
        "course_config": meta.get('course_config', ''),
        "going":      meta.get('going', ''),
        "race_class": meta.get('race_class', ''),
        "has_live_odds": has_odds,
        "horses":     active + scratched,
        "updated_at": datetime.now().isoformat(),
    }


# ─── PRINT RACE SUMMARY ───────────────────────────────────────────
def print_race_summary(race_out: dict):
    rn   = race_out['race_no']
    name = race_out['race_name']
    dist = race_out['distance']
    surf = race_out['surface']
    has_odds = race_out['has_live_odds']
    horses   = [h for h in race_out['horses'] if not h.get('scratched')]

    odds_tag = "📡 LIVE ODDS" if has_odds else "⏳ no odds yet"
    print(f"\n  Race {rn} — {name} ({dist}m {surf})  {odds_tag}")
    print(f"  {'#':>3}  {'Horse':<22} {'Model%':>7}  {'Odds':>6}  {'Impl%':>6}  {'EV':>7}  {'Flag'}")
    print(f"  {'-'*65}")

    for h in horses[:8]:
        ev_str    = f"{h['ev']:+.0%}" if h.get('ev') is not None else "   —  "
        odds_str  = f"{h['win_odds']:.1f}" if h.get('win_odds') else "  —  "
        impl_str  = f"{h['implied_win_pct']:.1f}%" if h.get('implied_win_pct') else "   —  "
        flag      = "⭐ VALUE" if h.get('value_flag') else ""
        print(f"  #{h['no']:>2}  {h['name']:<22} {h['model_win_pct']:>6.1f}%  "
              f"{odds_str:>6}  {impl_str:>6}  {ev_str:>7}  {flag}")


# ─── WRITE OUTPUT ─────────────────────────────────────────────────
def write_output(data: dict):
    OUTPUT_F.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


# ─── MAIN LOOP ────────────────────────────────────────────────────
def run(race_filter=None, interval=DEFAULT_POLL, once=False):
    card     = load_race_card()
    date_str = card.get('date', '')
    venue    = card.get('venue', '')
    races    = card.get('races', [])

    if race_filter:
        races = [r for r in races if r['race_meta']['race_no'] in race_filter]

    total = len(races)
    print(f"\n{'='*60}")
    print(f"  HKJC Live Scraper  |  {date_str}  {venue}  ({total} races)")
    print(f"  Poll interval: {interval}s  |  Output: {OUTPUT_F}")
    print(f"{'='*60}")

    poll_count = 0
    while True:
        poll_count += 1
        now = datetime.now().strftime('%H:%M:%S')
        print(f"\n[Poll #{poll_count}  {now}]")

        race_outputs = []
        for race in races:
            out = process_race_odds(race, date_str, venue)
            race_outputs.append(out)
            print_race_summary(out)
            time.sleep(1.5)   # polite delay between races

        # Write output for dashboard
        output = {
            "updated_at": datetime.now().isoformat(),
            "date":       date_str,
            "venue":      venue,
            "poll_count": poll_count,
            "races":      race_outputs,
        }
        write_output(output)
        print(f"\n  ✅ race_data.json updated ({now})")

        if once:
            print("  [--once mode] Done.")
            break

        print(f"  Next poll in {interval}s... (Ctrl+C to stop)")
        time.sleep(interval)


# ─── CLI ──────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="HKJC Live Odds Scraper — Module 3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python hkjc_scraper.py                  # all races, poll every 60s
  python hkjc_scraper.py --race 3         # only race 3
  python hkjc_scraper.py --race 1,2,3     # races 1-3
  python hkjc_scraper.py --interval 30    # poll every 30s
  python hkjc_scraper.py --once           # single fetch
        """
    )
    ap.add_argument("--race",     default=None, help="Race number(s) e.g. 1 or 1,2,3")
    ap.add_argument("--interval", default=60,   type=int, help="Poll interval in seconds")
    ap.add_argument("--once",     action="store_true",    help="Fetch once and exit")
    ap.add_argument("--input",    default="race_card.json", help="race_card.json path")
    ap.add_argument("--output",   default="race_data.json", help="Output JSON path")
    args = ap.parse_args()

    global RACE_CARD_F, OUTPUT_F
    RACE_CARD_F = Path(args.input)
    OUTPUT_F    = Path(args.output)

    race_filter = None
    if args.race:
        race_filter = [int(x.strip()) for x in args.race.split(',')]

    try:
        run(race_filter=race_filter, interval=args.interval, once=args.once)
    except KeyboardInterrupt:
        print("\n\n  Stopped by user. race_data.json is up to date.")

if __name__ == "__main__":
    main()

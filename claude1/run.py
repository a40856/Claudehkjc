# run.py — Master Orchestrator
# Usage: python run.py
# Chains: race_card_scraper → v85_model → db_logger → hkjc_scraper (live loop)

import json
import sys
import time
import subprocess
from datetime import datetime, date
from pathlib import Path

import db_logger
from hkjc_scraper import load_race_card, process_race_odds, write_output, fetch_live_odds

# ── Paths ──────────────────────────────────────────────────
RACE_CARD_F = Path("race_card.json")
RACE_DATA_F = Path("race_data.json")
POLL_INTERVAL = 60  # seconds

# ══════════════════════════════════════════════════════════
# STEP 1 — Run race_card_scraper.py as subprocess
# ══════════════════════════════════════════════════════════

def step1_scrape_race_card():
    print("\n[RUN] ── Step 1: Scraping race card...")
    result = subprocess.run(
        [sys.executable, "race_card_scraper.py"],
        capture_output=False
    )
    if result.returncode != 0:
        print("[RUN] ❌ race_card_scraper.py failed. Exiting.")
        sys.exit(1)
    if not RACE_CARD_F.exists():
        print("[RUN] ❌ race_card.json not created. Exiting.")
        sys.exit(1)
    print("[RUN] ✅ race_card.json ready")


# ══════════════════════════════════════════════════════════
# STEP 2 — Run v85_model.py as subprocess
# ══════════════════════════════════════════════════════════

def step2_run_model():
    print("\n[RUN] ── Step 2: Running v85 model...")
    result = subprocess.run(
        [sys.executable, "v85_model.py"],
        capture_output=False
    )
    if result.returncode != 0:
        print("[RUN] ⚠️  v85_model.py had errors — continuing anyway")
    else:
        print("[RUN] ✅ v85 scores written to race_card.json")


# ══════════════════════════════════════════════════════════
# STEP 3 — Initialise DB and save race card
# ══════════════════════════════════════════════════════════

def step3_init_db(card: dict) -> tuple:
    print("\n[RUN] ── Step 3: Initialising database...")
    db_logger.init_db()

    today  = card.get("date", date.today().strftime("%Y-%m-%d"))
    venue  = card.get("venue", "ST")
    going  = card.get("going")
    races  = card.get("races", [])

    meeting_id = db_logger.save_meeting(today, venue, going)

    # Build a flat race list compatible with db_logger
    # hkjc_scraper nests metadata under race_meta — flatten it here
    flat_races = []
    for r in races:
        meta   = r.get("race_meta", {})
        horses = r.get("horses", [])
        flat_races.append({
            "race_no":    meta.get("race_no"),
            "name":       meta.get("race_name"),
            "distance":   meta.get("distance"),
            "surface":    meta.get("surface"),
            "race_class": meta.get("race_class"),
            "start_time": meta.get("start_time"),
            "horses":     horses,
        })

    race_ids = db_logger.save_full_race_card(meeting_id, flat_races)
    print(f"[RUN] ✅ DB ready — meeting_id={meeting_id} | {len(races)} races saved")
    return today, venue, meeting_id, race_ids


# ══════════════════════════════════════════════════════════
# STEP 4 — Live odds polling loop (reuses hkjc_scraper logic)
# ══════════════════════════════════════════════════════════

def step4_poll_loop(card: dict, today: str, venue: str, race_ids: dict):
    races = card.get("races", [])
    total = len(races)
    results_scraped = set()

    print(f"\n[RUN] ── Step 4: Live polling ({total} races, every {POLL_INTERVAL}s)")
    print("[RUN]    Press Ctrl+C to stop.\n")

    poll_count = 0

    try:
        while True:
            poll_count += 1
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n[Poll #{poll_count}  {now}]")

            race_outputs = []

            for race in races:
                meta    = race.get("race_meta", {})
                race_no = meta.get("race_no")

                # Use hkjc_scraper's own process_race_odds — already handles EV etc.
                out = process_race_odds(race, today, venue)
                race_outputs.append(out)

                # Log odds snapshot to DB (silent)
                race_id = race_ids.get(race_no)
                if race_id:
                    horses_for_db = _map_horses_for_db(out.get("horses", []))
                    db_logger.log_odds_snapshot(race_id, horses_for_db)

                # Print value picks
                value = [h for h in out["horses"] if h.get("value_flag")]
                if value:
                    print(f"  💰 R{race_no} value: " + ", ".join(
                        f"#{h['no']} {h['name']} EV={h['ev']:+.0%} @{h.get('win_odds','?')}"
                        for h in value
                    ))

                time.sleep(1.5)  # polite delay between races

            # Write race_data.json for dashboard
            output = {
                "updated_at": datetime.now().isoformat(),
                "date":       today,
                "venue":      venue,
                "poll_count": poll_count,
                "races":      race_outputs,
            }
            write_output(output)
            print(f"  ✅ race_data.json updated ({now})")

            # Try scraping results for races that should be done
            _try_scrape_all_results(today, venue, races, race_ids, results_scraped)

            if len(results_scraped) >= total:
                print(f"\n[RUN] 🏁 All {total} races done. Exiting loop.")
                break

            print(f"  Next poll in {POLL_INTERVAL}s... (Ctrl+C to stop)")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[RUN] Stopped by user.")

    # Final EV summary
    _print_final_summary(today, venue)


def _map_horses_for_db(horses: list) -> list:
    """Remap hkjc_scraper horse keys to db_logger expected keys."""
    out = []
    for h in horses:
        if h.get("scratched"):
            continue
        out.append({
            "horse_no":   h.get("no"),
            "horse_name": h.get("name"),
            "win_odds":   h.get("win_odds"),
            "place_odds": h.get("place_odds"),
            "ev":         h.get("ev"),
        })
    return out


def _try_scrape_all_results(today, venue, races, race_ids, results_scraped):
    """Check each race — if start_time + 5min has passed, try scraping results."""
    now = datetime.now()
    for race in races:
        meta    = race.get("race_meta", {})
        race_no = meta.get("race_no")
        start   = meta.get("start_time", "")

        if race_no in results_scraped or not start:
            continue

        try:
            race_dt = datetime.strptime(f"{today} {start}", "%Y-%m-%d %H:%M")
            if (now - race_dt).total_seconds() < 300:  # 5 min buffer
                continue
        except Exception:
            continue

        # Try to get results from HKJC results page
        finishing_order = _scrape_results(today, venue, race_no)
        if finishing_order:
            race_id = race_ids.get(race_no)
            if race_id:
                db_logger.save_results(race_id, finishing_order)
            results_scraped.add(race_no)
            print(f"  🏆 R{race_no} result saved — Winner: #{finishing_order[0]}")


def _scrape_results(today: str, venue: str, race_no: int) -> list:
    """
    Scrape finishing order from HKJC results page.
    Returns list of horse numbers in finishing order e.g. [7, 3, 11, 1]
    """
    import requests
    from bs4 import BeautifulSoup

    url = "https://racing.hkjc.com/en-us/local/information/results"
    params = {"racedate": today, "Racecourse": venue, "RaceNo": str(race_no)}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://racing.hkjc.com/",
    }

    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            return []

        s = BeautifulSoup(r.text, "html.parser")
        finishing = []

        # HKJC results table: look for placement column + horse number
        for table in s.find_all("table"):
            headers_row = table.find("tr")
            if not headers_row:
                continue
            cols = [c.get_text(strip=True) for c in headers_row.find_all(["th", "td"])]

            if "Plc" not in cols and "Place" not in cols:
                continue

            no_idx  = next((i for i, c in enumerate(cols) if c in ("No.", "Horse No.")), None)
            plc_idx = next((i for i, c in enumerate(cols) if c in ("Plc", "Place")), 0)

            if no_idx is None:
                continue

            rows = sorted(
                table.find_all("tr")[1:],
                key=lambda row: _parse_place(
                    row.find_all("td")[plc_idx].get_text(strip=True)
                    if len(row.find_all("td")) > plc_idx else "99"
                )
            )

            for row in rows:
                cells = row.find_all("td")
                if len(cells) <= no_idx:
                    continue
                try:
                    no = int(cells[no_idx].get_text(strip=True))
                    finishing.append(no)
                except ValueError:
                    continue

            if finishing:
                break

        return finishing

    except Exception as e:
        print(f"  ⚠️  Results scrape error R{race_no}: {e}")
        return []


def _parse_place(place_str: str) -> int:
    try:
        return int(place_str)
    except Exception:
        return 99


def _print_final_summary(today: str, venue: str):
    print(f"\n[RUN] 📊 EV picks summary — {today} {venue}:")
    picks = db_logger.get_ev_summary(today, venue)
    if picks:
        for p in picks:
            won = "✅ WON" if p[7] and p[1] == p[7] else "❌"
            print(f"  R{p[0]} #{p[1]} {str(p[2]):<20} Peak EV={p[5]:.1%}  {won}")
    else:
        print("  No EV value picks today.")

    roi = db_logger.get_roi_summary()
    if "roi_pct" in roi:
        print(f"\n[RUN] 💰 ROI: {roi['roi_pct']}% | "
              f"Bets: {roi['total_bets']} | Profit: HK${roi['total_profit']:.0f}")

    print("\n[RUN] ✅ All data saved → war_room.db")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  🏇 HKJC WAR ROOM — run.py")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S HKT')}")
    print("=" * 60)

    # Step 1 — Scrape race card
    step1_scrape_race_card()

    # Step 2 — Score with v85 model
    step2_run_model()

    # Load the fully scored race card
    card = load_race_card()

    # Step 3 — Save to DB
    today, venue, meeting_id, race_ids = step3_init_db(card)

    # Step 4 — Live polling loop
    step4_poll_loop(card, today, venue, race_ids)


if __name__ == "__main__":
    main()
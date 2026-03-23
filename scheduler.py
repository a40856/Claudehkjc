"""
scheduler.py — HKJC Race Day Scheduler
========================================
TWO modes:

  LOCAL LOOP  (--loop)
    Runs as background process on your Mac. Checks every 30s.
    Usage:  python scheduler.py --loop

  GITHUB ACTIONS  (--check)
    Called once per cron trigger. Checks time vs race calendar, runs task, exits.
    Usage:  python scheduler.py --check

Other commands:
    python scheduler.py --status
    python scheduler.py --run predict --date 2026/03/25 --venue HV
    python scheduler.py --run live    --date 2026/03/25 --venue HV
    python scheduler.py --run review  --date 2026/03/25 --venue HV
"""

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from config import (
    RACE_CALENDAR, LOG_DIR,
    get_race_day, next_race_day, scheduled_times
)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("scheduler")

# How many minutes either side of a trigger time counts as "in window"
CHECK_WINDOW_MIN = 20

# ── Helpers ───────────────────────────────────────────────────────────────────

def hhmm_now() -> str:
    return datetime.now().strftime("%H:%M")

def hhmm_to_dt(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)

def mins_until(hhmm: str) -> float:
    return (hhmm_to_dt(hhmm) - datetime.now()).total_seconds() / 60

def within_window(trigger_hhmm: str, window: int = CHECK_WINDOW_MIN) -> bool:
    """True if now is within ±window minutes of trigger_hhmm."""
    diff = abs((hhmm_to_dt(trigger_hhmm) - datetime.now()).total_seconds() / 60)
    return diff <= window

def run_script(script: str, date: str, venue: str,
               extra_args: list = None) -> bool:
    cmd = [sys.executable, script, "--date", date, "--venue", venue]
    if extra_args:
        cmd.extend(extra_args)
    log.info("▶ RUN  %s  date=%s  venue=%s  %s",
             script, date, venue, " ".join(extra_args or []))
    try:
        result = subprocess.run(cmd, timeout=600, check=False)
        ok = result.returncode == 0
        log.info("%s %s  (exit %d)", "✓" if ok else "✗", script, result.returncode)
        return ok
    except subprocess.TimeoutExpired:
        log.error("✗ TIMEOUT  %s  (>10 min)", script)
        return False
    except Exception as e:
        log.error("✗ ERROR    %s  — %s", script, e)
        return False

# ── Status display ────────────────────────────────────────────────────────────

def show_status():
    today = datetime.now().strftime("%Y/%m/%d")
    print(f"\n{'═'*72}")
    print(f"  HKJC Scheduler — Race Calendar")
    print(f"  Now: {datetime.now().strftime('%Y-%m-%d %H:%M')}  (HKT)")
    print(f"{'═'*72}")
    print(f"  {'Date':<12} {'Day':<4} {'Venue':<5} "
          f"{'Predict':>8} {'LiveOdds':>9} {'Review':>8}  Note")
    print(f"  {'─'*68}")
    for r in RACE_CALENDAR:
        if r["date"] < today:
            continue
        t   = scheduled_times(r)
        dow = datetime.strptime(r["date"], "%Y/%m/%d").strftime("%a")
        note = "★ G1" if r.get("g1") else ""
        tag  = "◀ TODAY" if r["date"] == today else ""
        print(f"  {r['date']:<12} {dow:<4} {r['venue']:<5} "
              f"{t['predict']:>8} {t['live_odds']:>9} {t['review']:>8}  "
              f"{note} {tag}")
    nrd = next_race_day()
    if nrd:
        t    = scheduled_times(nrd)
        dow  = datetime.strptime(nrd["date"], "%Y/%m/%d").strftime("%a")
        days = (datetime.strptime(nrd["date"], "%Y/%m/%d").date()
                - datetime.now().date()).days
        print(f"\n  Next race: {nrd['date']} ({dow}) {nrd['venue']}  "
              f"[{days} day(s) away]")
        print(f"    predict.py    →  {t['predict']} HKT")
        print(f"    live_odds.py  →  {t['live_odds']} HKT")
        print(f"    review.py     →  {t['review']} HKT")
    print()

# ── GitHub Actions: one-shot check ───────────────────────────────────────────

def check_and_run():
    """
    Called once per GitHub Actions cron trigger.
    Figures out which task belongs to the current time window and runs it.
    Does nothing if today is not a race day or time is outside all windows.
    """
    today    = datetime.now().strftime("%Y/%m/%d")
    race_day = get_race_day(today)

    if not race_day:
        log.info("No race today (%s) — skipping.", today)
        return

    date  = race_day["date"]
    venue = race_day["venue"]
    t     = scheduled_times(race_day)

    log.info("Race day: %s %s | predict@%s  live@%s  review@%s",
             date, venue, t["predict"], t["live_odds"], t["review"])

    # Check windows in reverse order (review first) to avoid overlap conflicts
    if within_window(t["review"]):
        log.info("→ REVIEW window  (%s ± %d min)", t["review"], CHECK_WINDOW_MIN)
        run_script("review.py", date, venue)

    elif within_window(t["live_odds"]):
        log.info("→ LIVE ODDS window  (%s ± %d min)", t["live_odds"], CHECK_WINDOW_MIN)
        # No --watch in GitHub Actions — workflow re-triggers every 30 min
        run_script("live_odds.py", date, venue)

    elif within_window(t["predict"]):
        log.info("→ PREDICT window  (%s ± %d min)", t["predict"], CHECK_WINDOW_MIN)
        run_script("predict.py", date, venue)

    else:
        log.info("Time %s is outside all task windows — no action.", hhmm_now())

# ── Local background loop ─────────────────────────────────────────────────────

def loop_mode():
    log.info("═" * 65)
    log.info("HKJC Scheduler — LOCAL LOOP MODE  (Ctrl+C to stop)")
    log.info("═" * 65)
    fired     = {"predict": False, "live_odds": False, "review": False}
    last_date = None

    while True:
        today    = datetime.now().strftime("%Y/%m/%d")
        race_day = get_race_day(today)

        # Reset flags at midnight
        if today != last_date:
            fired     = {"predict": False, "live_odds": False, "review": False}
            last_date = today
            log.info("── New day: %s ──", today)

        if race_day:
            date  = race_day["date"]
            venue = race_day["venue"]
            t     = scheduled_times(race_day)
            now   = hhmm_now()

            if not fired["predict"] and now >= t["predict"]:
                fired["predict"] = True
                run_script("predict.py", date, venue)

            elif not fired["live_odds"] and now >= t["live_odds"]:
                if not fired["predict"]:
                    log.warning("predict.py missed — running it now first")
                    run_script("predict.py", date, venue)
                    fired["predict"] = True
                fired["live_odds"] = True
                # --watch keeps running until Ctrl+C or last race finishes
                run_script("live_odds.py", date, venue, extra_args=["--watch"])

            elif not fired["review"] and now >= t["review"]:
                fired["review"] = True
                run_script("review.py", date, venue)
                log.info("✓ All tasks complete for %s %s", date, venue)

            else:
                # Print next pending task countdown (once per minute)
                if datetime.now().second < 30:
                    pending = [
                        (t["predict"],   "predict.py",   fired["predict"]),
                        (t["live_odds"], "live_odds.py", fired["live_odds"]),
                        (t["review"],    "review.py",    fired["review"]),
                    ]
                    for trigger, name, done in pending:
                        if not done:
                            log.info("⌛ Next: %-14s @ %s HKT  (%.0f min away)",
                                     name, trigger, mins_until(trigger))
                            break
        else:
            nrd = next_race_day()
            if nrd and datetime.now().minute == 0:
                days = (datetime.strptime(nrd["date"], "%Y/%m/%d").date()
                        - datetime.now().date()).days
                log.info("No race today. Next: %s %s  (%d day(s))",
                         nrd["date"], nrd["venue"], days)

        time.sleep(30)

# ── Force-run a specific task ─────────────────────────────────────────────────

def force_run(task: str, date: str, venue: str):
    mapping = {
        "predict": ("predict.py",   []),
        "live":    ("live_odds.py", []),
        "review":  ("review.py",    []),
    }
    if task not in mapping:
        print(f"Unknown task '{task}'. Use: predict | live | review")
        sys.exit(1)
    script, extra = mapping[task]
    run_script(script, date, venue, extra_args=extra)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Scheduler")
    parser.add_argument("--loop",   action="store_true",
                        help="Run as background loop (local Mac mode)")
    parser.add_argument("--check",  action="store_true",
                        help="One-shot check (GitHub Actions mode)")
    parser.add_argument("--status", action="store_true",
                        help="Print upcoming schedule and exit")
    parser.add_argument("--run",    choices=["predict", "live", "review"],
                        help="Force-run a specific task right now")
    parser.add_argument("--date",
                        default=datetime.now().strftime("%Y/%m/%d"),
                        help="Date for --run  YYYY/MM/DD")
    parser.add_argument("--venue",  default=None,
                        help="Venue for --run  ST or HV")
    args = parser.parse_args()

    if args.status:
        show_status()

    elif args.check:
        show_status()
        check_and_run()

    elif args.run:
        rd    = get_race_day(args.date)
        venue = args.venue or (rd["venue"] if rd else "ST")
        force_run(args.run, args.date, venue)

    elif args.loop:
        show_status()
        loop_mode()

    else:
        parser.print_help()

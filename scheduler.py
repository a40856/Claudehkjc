"""
scheduler.py — HKJC Race Day Scheduler
=======================================
Usage:
  python scheduler.py --check-and-run      # predict if tomorrow is race day
  python scheduler.py --check-and-review   # review if today was race day
  python scheduler.py --list               # list all remaining race days
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta

from config import RACE_CALENDAR, get_race_day, predict_date, review_time

# ── Helpers ───────────────────────────────────────────────────────────────────

def today_hkt() -> str:
    return datetime.now().strftime("%Y/%m/%d")

def tomorrow_hkt() -> str:
    return (datetime.now() + timedelta(days=1)).strftime("%Y/%m/%d")

def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")

def run_cmd(cmd: list):
    print(f"  → Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"  ✗ Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)
    print(f"  ✓ Done")

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK AND RUN — predict.py before a race day
# ═══════════════════════════════════════════════════════════════════════════════

def _is_before_race_end(race_day: dict) -> bool:
    race_datetime = datetime.strptime(
        f"{race_day['date']} {race_day['last_race_time']}", "%Y/%m/%d %H:%M"
    )
    return datetime.now() < race_datetime


def check_and_run():
    """
    Called by predict-schedule.yml at 09:00 HKT daily.
    Checks if tomorrow is a race day, or if today is a race day before races finish.
    Outputs RACE_PRE_RACE environment variable for GitHub Actions.
    """
    tmr = tomorrow_hkt()
    rd  = get_race_day(tmr)
    today = today_hkt()

    print(f"\n{'═'*55}")
    print(f"  HKJC Scheduler — Check & Run")
    print(f"  Now      : {today} {now_hhmm()} HKT")
    print(f"  Tomorrow : {tmr}")
    print(f"{'═'*55}")

    if rd is None:
        rd = get_race_day(today)
        if rd is None or not _is_before_race_end(rd):
            print(f"  ✓ No race tomorrow or today before races finish — nothing to do.")
            print(f"  RACE_PRE_RACE=false")
            sys.exit(0)
        run_target = 'today'
        print(f"  ✓ Today is a race day and still before races finish.")
    else:
        run_target = 'tomorrow'
        print(f"  ✓ Race day found for tomorrow: {rd['date']} @ {rd['venue']}")

    if "note" in rd:
        print(f"  ⚠ Note: {rd['note']}")

    race_tag = f"{rd['date'].replace('/', '-')}_{rd['venue']}"
    print(f"\n  → Setting environment: RACE_PRE_RACE=true")
    print(f"  → RACE_TAG={race_tag}")
    print(f"  → RACE_TARGET={run_target}")
    
    # Output to GitHub Actions step output
    print(f"\nRACE_PRE_RACE=true")
    print(f"RACE_TAG={race_tag}")
    print(f"RACE_DATE={rd['date']}")
    print(f"RACE_VENUE={rd['venue']}")
    print(f"RACE_TARGET={run_target}")

    # Run predict.py
    run_cmd(["python", "predict.py", "--date", rd["date"], "--venue", rd["venue"]])

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK AND REVIEW — review.py after today's races finish
# ═══════════════════════════════════════════════════════════════════════════════

def check_and_review():
    """
    Called by review-schedule.yml at 23:30 HKT daily.
    Checks if today was a race day AND enough time has passed → runs review.py.
    Outputs RACE_TODAY environment variable for GitHub Actions.
    """
    tod = today_hkt()
    rd  = get_race_day(tod)

    print(f"\n{'═'*55}")
    print(f"  HKJC Scheduler — Check & Review")
    print(f"  Now  : {today_hkt()} {now_hhmm()} HKT")
    print(f"{'═'*55}")

    if rd is None:
        print(f"  ✓ No race today — nothing to review.")
        print(f"  RACE_TODAY=false")
        sys.exit(0)

    # Check if last race + 65 min has passed
    rev_dt = review_time(rd)
    now_dt = datetime.now()

    print(f"  Race day    : {rd['date']} @ {rd['venue']}")
    print(f"  Last race   : {rd['last_race_time']} HKT")
    print(f"  Review time : {rev_dt.strftime('%Y/%m/%d %H:%M')} HKT")
    print(f"  Current time: {now_dt.strftime('%Y/%m/%d %H:%M')} HKT")

    if now_dt < rev_dt:
        print(f"  ⏳ Too early — races may not be finished yet. Skipping.")
        print(f"  RACE_TODAY=false")
        sys.exit(0)

    print(f"  ✓ Races finished — running review.py")
    
    # Generate RACE_TAG for GitHub Actions (e.g., 2026-03-25_HV)
    race_tag = f"{rd['date'].replace('/', '-')}_{rd['venue']}"
    print(f"\n  → Setting environment: RACE_TODAY=true")
    print(f"  → RACE_TAG={race_tag}")
    
    # Output to GitHub Actions step output
    print(f"\nRACE_TODAY=true")
    print(f"RACE_TAG={race_tag}")
    print(f"RACE_DATE={rd['date']}")
    print(f"RACE_VENUE={rd['venue']}")

    run_cmd(["python", "review.py", "--date", rd["date"], "--venue", rd["venue"]])

# ═══════════════════════════════════════════════════════════════════════════════
# LIST — show remaining race days this season
# ═══════════════════════════════════════════════════════════════════════════════

def list_remaining():
    tod = today_hkt()
    upcoming = [rd for rd in RACE_CALENDAR if rd["date"] >= tod]

    print(f"\n{'═'*60}")
    print(f"  HKJC Race Calendar — {len(upcoming)} remaining meetings")
    print(f"{'═'*60}")
    print(f"  {'Date':<14} {'Venue':<6} {'Last Race':<12} {'Note'}")
    print(f"  {'─'*57}")
    for rd in upcoming:
        note = rd.get("note", "")
        print(f"  {rd['date']:<14} {rd['venue']:<6} "
              f"{rd['last_race_time']:<12} {note}")
    print(f"{'═'*60}")

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HKJC Race Scheduler")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-and-run",    action="store_true",
                       help="Run predict.py if tomorrow is a race day")
    group.add_argument("--check-and-review", action="store_true",
                       help="Run review.py if today was a race day and races finished")
    group.add_argument("--list",             action="store_true",
                       help="List all remaining race days this season")
    args = parser.parse_args()

    if args.check_and_run:
        check_and_run()
    elif args.check_and_review:
        check_and_review()
    elif args.list:
        list_remaining()
#!/usr/bin/env python3
"""
backfill_results.py — Backfill Race Results for Past Races
===========================================================
Automatically fetch results for all past races that don't have results yet.

Usage:
    python backfill_results.py                    # Backfill all past races
    python backfill_results.py --date 2026/04/22 # Backfill specific race
    python backfill_results.py --dry-run         # Show what would be done
"""

import argparse, os, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

from config import RACE_CALENDAR, get_race_day

def get_past_races():
    """Get all races that have already happened."""
    today = datetime.now().strftime("%Y/%m/%d")
    past_races = []

    for race in RACE_CALENDAR:
        if race["date"] < today:
            past_races.append(race)

    return past_races

def has_results(race_date: str, venue: str) -> bool:
    """Check if we already have results for this race."""
    date_str = race_date.replace("/", "-")
    results_dir = Path("data/results")

    # Look for any XLSX file with this date/venue
    pattern = f"{date_str}_{venue}.xlsx"
    return (results_dir / pattern).exists()

def run_review(race_date: str, venue: str, dry_run: bool = False):
    """Run review.py for a specific race."""
    cmd = ["python", "review.py", "--date", race_date, "--venue", venue]

    if dry_run:
        print(f"  Would run: {' '.join(cmd)}")
        return True

    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"  ✓ Success: {race_date} {venue}")
        return True
    else:
        # Check if it's a "results not available" error vs. a real error
        error_msg = result.stderr.strip()
        if "No race results found" in error_msg and "Results may not be available yet" in error_msg:
            print(f"  ⏭️  Skipped: {race_date} {venue} (results not available on HKJC)")
            return True  # Don't count as failure
        else:
            print(f"  ✗ Failed: {race_date} {venue}")
            print(f"    Error: {error_msg}")
            return False

def main():
    parser = argparse.ArgumentParser(description="Backfill race results")
    parser.add_argument("--date", help="Specific date YYYY/MM/DD")
    parser.add_argument("--venue", help="Specific venue ST or HV")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be done without running")
    args = parser.parse_args()

    if args.date and args.venue:
        # Single race backfill
        races_to_process = [{"date": args.date, "venue": args.venue}]
    else:
        # All past races
        races_to_process = get_past_races()

    print(f"\n{'═'*60}")
    print(f"  HKJC Results Backfill")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"  Races to check: {len(races_to_process)}")
    print(f"{'═'*60}")

    processed = 0
    successful = 0

    for race in races_to_process:
        race_date = race["date"]
        venue = race["venue"]

        processed += 1

        # Check if we already have results
        if has_results(race_date, venue):
            print(f"  ⏭️  Skip: {race_date} {venue} (already has results)")
            continue

        # Run review for this race
        if run_review(race_date, venue, args.dry_run):
            successful += 1

    print(f"\n{'═'*60}")
    print(f"  Summary: {successful}/{processed} races processed")
    if not args.dry_run:
        print(f"  Results saved to: data/results/")
        print(f"  Updated predictions: data/predictions/")
    print(f"{'═'*60}")

if __name__ == "__main__":
    main()
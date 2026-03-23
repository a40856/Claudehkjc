"""
scheduler.py — Automated Task Scheduler for HKJC Prediction System
====================================================================
Reads RACE_CALENDAR from config.py and registers two cron-style jobs
for every race day:

  Job A — predict.py  @ 18:00 HKT the evening BEFORE race day
  Job B — review.py   @ last_race + 65 min on race day

Supported backends (auto-detected):
  1. macOS launchd   → ~/Library/LaunchAgents/com.hkjc.<tag>.plist
  2. Linux cron      → appended to user crontab
  3. APScheduler     → in-process fallback (keep terminal open)

Usage:
    python scheduler.py install
    python scheduler.py uninstall
    python scheduler.py status
    python scheduler.py run-now predict --date 2026/03/25 --venue HV
    python scheduler.py run-now review  --date 2026/03/25 --venue HV
"""

import argparse, os, platform, re, shutil, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path
from config import (RACE_CALENDAR, PREDICT_TIME, review_time,
                    predict_date, get_race_day)

HERE   = Path(__file__).parent.resolve()
PYTHON = sys.executable
SYSTEM = platform.system()     # "Darwin" | "Linux" | "Windows"

# ── Job list (built from RACE_CALENDAR) ───────────────────────────────────────
def _job_list() -> list:
    """
    Returns one predict + one review job per race day.
    predict runs at 18:00 the day before.
    review runs last_race + 65 min on race day.
    """

# ── Status ────────────────────────────────────────────────────────────────────
def cmd_status():
    """Print full schedule table — past jobs marked ✓, future marked ⏳"""

# ── macOS launchd ─────────────────────────────────────────────────────────────
def install_macos(jobs):
    """Write .plist per job → ~/Library/LaunchAgents/ → launchctl load"""

def uninstall_macos(jobs):
    """launchctl unload + delete all com.hkjc.* plists"""

# ── Linux cron ────────────────────────────────────────────────────────────────
CRON_MARKER = "# HKJC-SCHEDULER"

def install_linux(jobs):
    """Append tagged cron lines to user crontab"""

def uninstall_linux():
    """Strip all HKJC-SCHEDULER lines from crontab"""

# ── APScheduler fallback (in-process) ────────────────────────────────────────
def install_apscheduler(jobs):
    """Register DateTrigger jobs — terminal must stay open"""

# ── Install / Uninstall dispatcher ───────────────────────────────────────────
def cmd_install():
    jobs = _job_list()
    if   SYSTEM == "Darwin": install_macos(jobs)
    elif SYSTEM == "Linux":  install_linux(jobs)
    else:                    install_apscheduler(jobs)

def cmd_uninstall():
    jobs = _job_list()
    if   SYSTEM == "Darwin": uninstall_macos(jobs)
    elif SYSTEM == "Linux":  uninstall_linux()

# ── Run-now (manual trigger) ──────────────────────────────────────────────────
def cmd_run_now(script_name, extra_args):
    """Immediately run predict.py or review.py with given args"""
    subprocess.run([PYTHON, str(HERE / f"{script_name}.py")] + extra_args)

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub    = parser.add_subparsers(dest="cmd")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    sub.add_parser("status")
    rn = sub.add_parser("run-now")
    rn.add_argument("script",  choices=["predict","review"])
    rn.add_argument("--date",  required=True)
    rn.add_argument("--venue", required=True, choices=["ST","HV"])
    args, unknown = parser.parse_known_args()
    ...

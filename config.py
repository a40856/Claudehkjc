"""
config.py — Shared constants for HKJC Horse Racing Prediction System
=====================================================================
Never run this file directly. It is imported by predict.py and review.py.

Option B (distance suitability + pace via ML) → planned for future release
Current model: Option A — Scenario E scaled to 1.000
"""
from pathlib import Path
from datetime import datetime, timedelta

# ── Root data layout ──────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
RAW_DIR      = DATA_DIR / "raw"
PRED_DIR     = DATA_DIR / "predictions"
RESULTS_DIR  = DATA_DIR / "results"
COMBINED_DIR = DATA_DIR / "combined"
LOG_DIR      = BASE_DIR / "logs"
BACKTEST_LOG = DATA_DIR / "backtest_log.csv"

def session_dirs(race_date: str, venue: str) -> dict:
    """Auto-create and return per-session subdirectories tagged by date+venue."""
    tag  = race_date.replace("/", "-") + "_" + venue
    dirs = {
        "raw":      RAW_DIR      / tag,
        "pred":     PRED_DIR     / tag,
        "results":  RESULTS_DIR  / tag,
        "combined": COMBINED_DIR / tag,
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs

def init_data_dirs():
    """Create all top-level data directories on first import."""
    for d in [RAW_DIR, PRED_DIR, RESULTS_DIR, COMBINED_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)

init_data_dirs()

# ─────────────────────────────────────────────────────────────────────────────
# RACE CALENDAR  (Mar – Apr 2026)
# Update monthly from:
#   https://racing.hkjc.com/racing/information/English/Racing/Fixture.aspx
#
# first_race / last_race = HH:MM 24-hr HKT
# races  = expected number of races (used by review.py)
# g1     = True → uses WEIGHTS_CLASSIC instead of WEIGHTS_GENERAL
# ─────────────────────────────────────────────────────────────────────────────
RACE_CALENDAR = [
    {"date": "2026/03/25", "venue": "HV", "first_race": "18:40", "last_race": "22:50", "races": 9,  "g1": False},
    {"date": "2026/03/29", "venue": "ST", "first_race": "13:00", "last_race": "17:55", "races": 10, "g1": False},
    {"date": "2026/04/01", "venue": "HV", "first_race": "18:40", "last_race": "22:30", "races": 7,  "g1": False},
    {"date": "2026/04/06", "venue": "ST", "first_race": "13:00", "last_race": "17:55", "races": 11, "g1": False},
    {"date": "2026/04/08", "venue": "HV", "first_race": "18:40", "last_race": "22:30", "races": 8,  "g1": False},
    {"date": "2026/04/12", "venue": "ST", "first_race": "13:00", "last_race": "17:55", "races": 9,  "g1": False},
    {"date": "2026/04/15", "venue": "HV", "first_race": "18:40", "last_race": "22:30", "races": 7,  "g1": False},
    {"date": "2026/04/19", "venue": "ST", "first_race": "13:00", "last_race": "17:55", "races": 10, "g1": False},
    {"date": "2026/04/22", "venue": "HV", "first_race": "18:40", "last_race": "22:30", "races": 8,  "g1": False},
    {"date": "2026/04/26", "venue": "ST", "first_race": "13:00", "last_race": "18:00", "races": 10, "g1": True},   # QEII Cup + Champions Mile + Chairman's Sprint
    {"date": "2026/04/29", "venue": "HV", "first_race": "18:40", "last_race": "22:30", "races": 7,  "g1": False},
]

# ── Scheduling ────────────────────────────────────────────────────────────────
# predict.py  → 18:00 HKT the evening BEFORE race day
# review.py   → REVIEW_OFFSET_MIN after last race on race day
PREDICT_TIME      = "18:00"
REVIEW_OFFSET_MIN = 65

def review_time(race_day: dict) -> str:
    """Return review.py trigger time HH:MM for a given race day dict."""
    h, m = map(int, race_day["last_race"].split(":"))
    dt   = datetime(2000, 1, 1, h, m) + timedelta(minutes=REVIEW_OFFSET_MIN)
    return dt.strftime("%H:%M")

def predict_date(race_date: str) -> str:
    """Return the date predict.py should run (day before race)."""
    dt = datetime.strptime(race_date, "%Y/%m/%d") - timedelta(days=1)
    return dt.strftime("%Y/%m/%d")

def get_race_day(date_str: str) -> dict | None:
    """Return race day dict for YYYY/MM/DD, or None if not a race day."""
    return next((r for r in RACE_CALENDAR if r["date"] == date_str), None)

def get_predict_day(date_str: str) -> dict | None:
    """Return race day dict if predict.py should run today (race is tomorrow)."""
    tomorrow = (datetime.strptime(date_str, "%Y/%m/%d")
                + timedelta(days=1)).strftime("%Y/%m/%d")
    return get_race_day(tomorrow)

def next_race_day(from_date: str = None) -> dict | None:
    """Return the next upcoming race day from today or from_date."""
    ref = from_date or datetime.now().strftime("%Y/%m/%d")
    return next(
        (r for r in sorted(RACE_CALENDAR, key=lambda x: x["date"])
         if r["date"] >= ref), None
    )

# ── HKJC URLs ─────────────────────────────────────────────────────────────────
URLS = {
    "race_card":      "https://racing.hkjc.com/racing/information/English/Racing/LocalEntries.aspx",
    "race_results":   "https://racing.hkjc.com/racing/information/English/Racing/ResultsAll.aspx",
    "dividends":      "https://racing.hkjc.com/racing/information/English/Racing/ResultsAllDividend.aspx",
    "horse_profile":  "https://racing.hkjc.com/racing/information/English/Horse/horse.aspx",
    "jockey_rank_st": "https://racing.hkjc.com/racing/information/English/Jockey/JockeyRanking.aspx?RaceCourse=ST",
    "jockey_rank_hv": "https://racing.hkjc.com/racing/information/English/Jockey/JockeyRanking.aspx?RaceCourse=HV",
    "trainer_rank":   "https://racing.hkjc.com/racing/information/English/Trainer/TrainerRanking.aspx",
    "scmp_result":    "https://www.scmp.com/sport/racing/race-result",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept":          "text/html,application/xhtml+xml,application/json,*/*",
}

# ── Model weights — Option A (Scenario E scaled to 1.000) ────────────────────
# Derivation: each value = Scenario E weight ÷ 0.860
# Ranking order is identical to Scenario E — only composite magnitude changes
# Future Option B adds distance_suitability (0.07) + pace_scenario (0.07) via ML
# ─────────────────────────────────────────────────────────────────────────────
WEIGHTS_GENERAL = {
    "form":    0.325,   # recent finishing positions (last 6 runs, recency weighted)
    "h2h":     0.128,   # head-to-head record vs horses in today's field
    "rating":  0.128,   # HKJC official handicap rating
    "market":  0.116,   # pre-race win odds (lower = stronger market confidence)
    "draw":    0.105,   # barrier draw bias (venue + distance + surface specific)
    "jockey":  0.081,   # jockey season win rate (live HKJC stats, fallback table below)
    "trainer": 0.070,   # trainer season win rate (live HKJC stats, fallback table below)
    "weight":  0.047,   # carried weight in lbs (heavier = slight disadvantage)
}
assert abs(sum(WEIGHTS_GENERAL.values()) - 1.0) < 0.001, "WEIGHTS_GENERAL must sum to 1.0"

# ── Model weights — Classic / G1 races ───────────────────────────────────────
WEIGHTS_CLASSIC = {
    "form":     0.26,
    "speed":    0.11,
    "rating":   0.11,
    "distance": 0.09,
    "pedigree": 0.08,
    "draw":     0.08,
    "market":   0.08,
    "jockey":   0.07,
    "trainer":  0.06,
    "pace":     0.03,
    "h2h":      0.02,
    "weight":   0.01,
}
assert abs(sum(WEIGHTS_CLASSIC.values()) - 1.0) < 0.001, "WEIGHTS_CLASSIC must sum to 1.0"

CLASSIC_RACE_NAMES = [
    "HONG KONG DERBY",        "HONG KONG OAKS",
    "HONG KONG MILE",         "HONG KONG CUP",
    "HONG KONG VASE",         "HONG KONG SPRINT",
    "CHAMPIONS & CHATER CUP", "FWD CHAMPIONS MILE",
    "FWD QEII CUP",           "CHAIRMAN'S SPRINT PRIZE",
]

# ── Draw bias tables ──────────────────────────────────────────────────────────
# Format: (venue, distance_str): {stall_number: bias_multiplier}
# Bias > 1.0 = favourable, < 1.0 = unfavourable
# ─────────────────────────────────────────────────────────────────────────────
DRAW_BIAS = {
    # ── Happy Valley ─────────────────────────────────────────────────────────
    ("HV", "1000"): {
        1:1.25, 2:1.20, 3:1.15, 4:1.10, 5:1.05,
        6:1.00, 7:0.95, 8:0.90, 9:0.85, 10:0.80, 11:0.75, 12:0.70,
    },
    ("HV", "1200"): {                            # tight home turn — inside critical
        1:1.30, 2:1.22, 3:1.15, 4:1.08, 5:1.02,
        6:0.97, 7:0.90, 8:0.83, 9:0.76, 10:0.70, 11:0.64, 12:0.58,
    },
    ("HV", "1650"): {                            # full circuit — strongest inside bias
        1:1.35, 2:1.28, 3:1.20, 4:1.12, 5:1.05,
        6:0.98, 7:0.90, 8:0.82, 9:0.75, 10:0.68, 11:0.62, 12:0.55,
    },
    ("HV", "1800"): {
        1:1.30, 2:1.22, 3:1.15, 4:1.08, 5:1.02,
        6:0.97, 7:0.90, 8:0.83, 9:0.76, 10:0.70, 11:0.64, 12:0.58,
    },
    # ── Sha Tin ───────────────────────────────────────────────────────────────
    ("ST", "1000"): {
        1:1.20, 2:1.15, 3:1.12, 4:1.08, 5:1.05, 6:1.02,
        7:1.00, 8:0.98, 9:0.95, 10:0.92, 11:0.88, 12:0.85, 13:0.82, 14:0.80,
    },
    ("ST", "1200"): {
        1:1.18, 2:1.14, 3:1.10, 4:1.07, 5:1.04, 6:1.02,
        7:1.00, 8:0.98, 9:0.96, 10:0.93, 11:0.90, 12:0.87, 13:0.84, 14:0.82,
    },
    ("ST", "1400"): {
        1:1.10, 2:1.08, 3:1.06, 4:1.04, 5:1.03, 6:1.02,
        7:1.01, 8:1.00, 9:0.99, 10:0.97, 11:0.95, 12:0.93, 13:0.91, 14:0.89,
    },
    ("ST", "1600"): {
        1:1.08, 2:1.06, 3:1.05, 4:1.04, 5:1.03, 6:1.02,
        7:1.01, 8:1.00, 9:0.99, 10:0.97, 11:0.96, 12:0.94, 13:0.92, 14:0.91,
    },
    ("ST", "1800"): {
        1:1.05, 2:1.04, 3:1.04, 4:1.03, 5:1.02, 6:1.02,
        7:1.01, 8:1.00, 9:0.99, 10:0.98, 11:0.97, 12:0.96, 13:0.95, 14:0.94,
    },
    ("ST", "2000"): {
        1:1.04, 2:1.03, 3:1.03, 4:1.02, 5:1.02, 6:1.01,
        7:1.01, 8:1.00, 9:0.99, 10:0.99, 11:0.98, 12:0.97, 13:0.96, 14:0.95,
    },
    ("ST", "2400"): {
        1:1.02, 2:1.02, 3:1.02, 4:1.01, 5:1.01, 6:1.01,
        7:1.00, 8:1.00, 9:1.00, 10:0.99, 11:0.99, 12:0.98, 13:0.98, 14:0.97,
    },
    # ── All Weather Track ─────────────────────────────────────────────────────
    ("AWT", "1200"): {
        1:1.22, 2:1.18, 3:1.14, 4:1.10, 5:1.06, 6:1.03,
        7:1.00, 8:0.97, 9:0.94, 10:0.91, 11:0.88, 12:0.85, 13:0.82, 14:0.80,
    },
    ("AWT", "1650"): {
        1:1.28, 2:1.22, 3:1.16, 4:1.10, 5:1.05, 6:1.00,
        7:0.96, 8:0.92, 9:0.88, 10:0.84, 11:0.80, 12:0.77, 13:0.74, 14:0.71,
    },
}

def get_draw_bias(venue: str, surface: str, distance: int, stall: int) -> float:
    """
    Return draw bias multiplier for venue/surface/distance/stall.
    Falls back to nearest distance if exact match not found.
    Returns 1.0 (neutral) if no data available.
    """
    surface_key = "AWT" if ("AWT" in surface.upper() or "全" in surface) else venue
    key         = (surface_key, str(distance))
    table       = DRAW_BIAS.get(key)
    if table is None:
        candidates = [(k, abs(int(k[1]) - distance))
                      for k in DRAW_BIAS if k[0] == surface_key]
        if candidates:
            table = DRAW_BIAS[min(candidates, key=lambda x: x[1])[0]]
    if table is None:
        return 1.0
    return table.get(
        stall,
        table.get(min(table.keys(), key=lambda s: abs(s - stall)), 1.0)
    )

# ── Jockey scores — FALLBACK ONLY ────────────────────────────────────────────
# Primary: live win rate fetched from HKJC inside predict.py
# Formula: score = 1 + 9 × min(win_rate / 0.25, 1.0)
# These values = 2025/26 mid-season estimates
# ─────────────────────────────────────────────────────────────────────────────
JOCKEY_SCORES = {
    "Z. Purton":      10.0,   # ~28% win rate  → score 10.0
    "H. Bowman":       9.5,
    "D.B. McMonagle":  9.0,
    "K.C. Leung":      8.5,
    "K. Teetan":       8.5,
    "C.L. Chau":       8.0,
    "M.F. Poon":       8.0,
    "R. Kingscote":    7.5,
    "A. Badel":        7.5,
    "A. Atzeni":       7.5,
    "D. Probert":      7.0,
    "M. Guyon":        7.0,
    "J. McDonald":     7.0,
    "L. Ferraris":     7.0,
    "J. Orman":        7.0,
    "L. Hewitson":     7.0,
    "H. Bentley":      7.0,
    "B. Avdulla":      6.5,
    "M. Chadwick":     6.5,
    "M.L. Yeung":      6.0,
    "Y.L. Chung":      6.0,
    "C.Y. Ho":         5.5,
    "E.C.W. Wong":     5.5,
    "H.T. Mo":         5.0,
}
JOCKEY_DEFAULT = 6.0   # any jockey not in table

# ── Trainer scores — FALLBACK ONLY ───────────────────────────────────────────
# Formula: score = 1 + 9 × min(win_rate / 0.22, 1.0)
# ─────────────────────────────────────────────────────────────────────────────
TRAINER_SCORES = {
    "F.C. Lor":    10.0,   # ~22%+ win rate → score 10.0
    "John Size":    9.5,
    "C. Fownes":    9.0,
    "D.A. Hayes":   8.5,
    "D. Whyte":     8.5,
    "M. Newnham":   8.0,
    "D. Eustace":   8.0,
    "P.F. Yiu":     7.5,
    "J. Richards":  7.0,
    "K.W. Lui":     7.0,
    "A.S. Cruz":    7.0,
    "Y.S. Tsui":    7.0,
    "P.C. Ng":      6.5,
    "D. Hall":      6.5,
    "K.H. Ting":    6.5,
    "K.L. Man":     6.0,
    "C.W. Chang":   6.0,
    "W.Y. So":      6.0,
    "C.S. Shum":    5.5,
    "C.H. Yip":     5.5,
    "B. Crawford":  5.5,
    "W.K. Mo":      5.0,
}
TRAINER_DEFAULT = 5.5   # any trainer not in table

# ── Global constants ──────────────────────────────────────────────────────────
PLACES    = 4   # top N horses to predict
FORM_RUNS = 6   # number of recent runs used for form score

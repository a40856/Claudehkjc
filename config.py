"""
config.py — Shared constants for HKJC Horse Racing Prediction System
"""

from datetime import datetime, timedelta
from pathlib import Path

# ── HKJC URLs ─────────────────────────────────────────────────────────────────
URLS = {
    "race_card":      "https://racing.hkjc.com/racing/information/English/Racing/LocalEntries.aspx",
    "race_results":   "https://racing.hkjc.com/racing/information/English/Racing/ResultsAll.aspx",
    "dividends":      "https://racing.hkjc.com/racing/information/English/Racing/ResultsAll.aspx",
    "horse_profile":  "https://racing.hkjc.com/racing/information/English/Horse/horse.aspx",
    "jockey_rank_st": "https://racing.hkjc.com/racing/information/English/Jockey/JockeyRanking.aspx?RaceCourse=ST",
    "jockey_rank_hv": "https://racing.hkjc.com/racing/information/English/Jockey/JockeyRanking.aspx?RaceCourse=HV",
    "trainer_rank":   "https://racing.hkjc.com/racing/information/English/Trainer/TrainerRanking.aspx",
    "draw_stats":     "https://racing.hkjc.com/racing/information/English/Draw/DrawStats.aspx",
    "live_odds_win":  "https://bet.hkjc.com/racing/pages/racecard.aspx",
    "odds_api":       "https://bet.hkjc.com/racing/getJSON.aspx",
    "scmp_result":    "https://www.scmp.com/sport/racing/race-result",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
}

# ── Model weights ─────────────────────────────────────────────────────────────
# Scenario E — General model (regular handicap races, ST + HV)
WEIGHTS_GENERAL = {
    "form":    0.28,
    "rating":  0.11,
    "market":  0.10,
    "draw":    0.09,
    "jockey":  0.07,
    "trainer": 0.06,
    "h2h":     0.11,
    "weight":  0.04,
}

# Scenario D — Classic/G1 model (Derby, Oaks, HK Mile etc.)
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

CLASSIC_RACE_NAMES = [
    "HONG KONG DERBY", "HONG KONG OAKS", "HONG KONG MILE",
    "HONG KONG CUP", "HONG KONG VASE", "HONG KONG SPRINT",
    "CHAMPIONS & CHATER CUP", "STANDARD CHARTERED",
]

# ── Draw bias tables ──────────────────────────────────────────────────────────
DRAW_BIAS = {
    ("HV",  "1650"): {1:1.35,2:1.28,3:1.20,4:1.12,5:1.05,6:0.98,7:0.90,8:0.82,9:0.75,10:0.68,11:0.62,12:0.55},
    ("HV",  "1800"): {1:1.30,2:1.22,3:1.15,4:1.08,5:1.02,6:0.97,7:0.90,8:0.83,9:0.76,10:0.70,11:0.64,12:0.58},
    ("HV",  "1000"): {1:1.25,2:1.20,3:1.15,4:1.10,5:1.05,6:1.00,7:0.95,8:0.90,9:0.85,10:0.80,11:0.75,12:0.70},
    ("ST",  "1000"): {1:1.20,2:1.15,3:1.12,4:1.08,5:1.05,6:1.02,7:1.00,8:0.98,9:0.95,10:0.92,11:0.88,12:0.85,13:0.82,14:0.80},
    ("ST",  "1200"): {1:1.18,2:1.14,3:1.10,4:1.07,5:1.04,6:1.02,7:1.00,8:0.98,9:0.96,10:0.93,11:0.90,12:0.87,13:0.84,14:0.82},
    ("ST",  "1400"): {1:1.10,2:1.08,3:1.06,4:1.04,5:1.03,6:1.02,7:1.01,8:1.00,9:0.99,10:0.97,11:0.95,12:0.93,13:0.91,14:0.89},
    ("ST",  "1600"): {1:1.08,2:1.06,3:1.05,4:1.04,5:1.03,6:1.02,7:1.01,8:1.00,9:0.99,10:0.97,11:0.96,12:0.94,13:0.92,14:0.91},
    ("ST",  "1800"): {1:1.05,2:1.04,3:1.04,4:1.03,5:1.02,6:1.02,7:1.01,8:1.00,9:0.99,10:0.98,11:0.97,12:0.96,13:0.95,14:0.94},
    ("ST",  "2000"): {1:1.04,2:1.03,3:1.03,4:1.02,5:1.02,6:1.01,7:1.01,8:1.00,9:0.99,10:0.99,11:0.98,12:0.97,13:0.96,14:0.95},
    ("ST",  "2400"): {1:1.02,2:1.02,3:1.02,4:1.01,5:1.01,6:1.01,7:1.00,8:1.00,9:1.00,10:0.99,11:0.99,12:0.98,13:0.98,14:0.97},
    ("AWT", "1200"): {1:1.22,2:1.18,3:1.14,4:1.10,5:1.06,6:1.03,7:1.00,8:0.97,9:0.94,10:0.91,11:0.88,12:0.85,13:0.82,14:0.80},
    ("AWT", "1650"): {1:1.28,2:1.22,3:1.16,4:1.10,5:1.05,6:1.00,7:0.96,8:0.92,9:0.88,10:0.84,11:0.80,12:0.77,13:0.74,14:0.71},
}

def get_draw_bias(venue: str, surface: str, distance: int, stall: int) -> float:
    dist_str    = str(distance)
    surface_key = "AWT" if "全" in surface or "AWT" in surface.upper() else venue
    key         = (surface_key, dist_str)
    table       = DRAW_BIAS.get(key)
    if table is None:
        candidates = [(k, abs(int(k[1]) - distance)) for k in DRAW_BIAS if k[0] == surface_key]
        if candidates:
            best_key = min(candidates, key=lambda x: x[1])[0]
            table    = DRAW_BIAS[best_key]
    if table is None:
        return 1.0
    return table.get(stall, table.get(min(table.keys(), key=lambda s: abs(s - stall)), 1.0))

# ── Jockey scores ─────────────────────────────────────────────────────────────
JOCKEY_SCORES = {
    "Z. Purton":10.0, "H. Bowman":9.5,   "D.B. McMonagle":9.0, "K.C. Leung":8.5,
    "K. Teetan":8.5,  "C.L. Chau":8.0,   "M.F. Poon":8.0,      "R. Kingscote":7.5,
    "A. Badel":7.5,   "A. Atzeni":7.5,   "L. Ferraris":7.0,    "J. Orman":7.0,
    "L. Hewitson":7.0,"H. Bentley":7.0,  "B. Avdulla":6.5,     "M. Guyon":7.0,
    "J. McDonald":7.0,"M. Chadwick":6.5, "M.L. Yeung":6.0,     "Y.L. Chung":6.0,
    "C.Y. Ho":5.5,    "E.C.W. Wong":5.5, "H.T. Mo":5.0,        "D. Probert":7.0,
    # Chinese names
    "莫雷拉":10.0, "何澤堯":9.5,  "麥道朗":9.0,  "梁家俊":8.5,  "田泰安":8.5,
    "朱敬倫":8.0,  "潘明輝":8.0,  "金鑫":7.5,    "巴度":7.5,    "阿諾迪":7.5,
    "費路":7.0,    "奧寬":7.0,    "希威森":7.0,  "班圖利":7.0,  "楊明綸":6.5,
    "鍾易禮":6.5,  "阿韋達":6.5,  "格尹":7.0,    "麥當奴":7.0,  "查德域":6.5,
    "霍宏聲":7.0,  "周俊樂":6.5,  "麥文堅":8.0,  "蔡明紹":6.0,
}
JOCKEY_DEFAULT = 5.0

# ── Trainer scores ────────────────────────────────────────────────────────────
TRAINER_SCORES = {
    "F.C. Lor":10.0,  "John Size":9.5,  "C. Fownes":9.0,  "D.A. Hayes":8.5,
    "D. Whyte":8.5,   "M. Newnham":8.0, "D. Eustace":8.0, "P.F. Yiu":7.5,
    "K.W. Lui":7.0,   "A.S. Cruz":7.0,  "Y.S. Tsui":7.0,  "D. Hall":6.5,
    "K.H. Ting":6.5,  "K.L. Man":6.0,   "C.W. Chang":6.0, "W.Y. So":6.0,
    "C.S. Shum":5.5,  "C.H. Yip":5.5,   "B. Crawford":5.5,"P.C. Ng":6.0,
    "W.K. Mo":5.0,    "J. Richards":7.0,
    # Chinese names
    "呂健威":10.0, "沈集成":9.5,  "方嘉柏":9.0,  "賀賢":8.5,   "韋達":8.5,
    "巫偉傑":8.0,  "鄧肇慶":8.0,  "姚本輝":7.5,  "呂慶強":7.0, "何良":7.0,
    "徐雨石":7.0,  "丁冠豪":6.5,  "文家良":6.0,  "張志洪":6.0, "蘇偉賢":6.0,
    "沈藍":5.5,    "蔡約翰":5.5,  "游達榮":6.0,  "葉楚航":6.0,
}
TRAINER_DEFAULT = 5.0

# ── Misc constants ────────────────────────────────────────────────────────────
PLACES     = 4
FORM_RUNS  = 6
OUTPUT_DIR = "output"

# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULING & DIRECTORY HELPERS
# Required by scheduler.py, predict.py, review.py
# ══════════════════════════════════════════════════════════════════════════════

DATA_DIR     = Path("data")
BACKTEST_LOG = DATA_DIR / "backtest_log.csv"
PREDICT_TIME = "18:00"

# ── Race Calendar — 2025/26 season (from 23 Mar 2026) ────────────────────────
#   HV night             →  "23:00"
#   ST day (Sun/Sat/Mon) →  "18:00"
#   ST AWT night ⚠       →  "22:00"   Wed Sha Tin night meetings
#   ST Twilight ⚠        →  "19:00"   T = Twilight, June–July

RACE_CALENDAR = [
    # March
    {"date": "2026/03/25", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/03/29", "venue": "ST", "last_race_time": "18:00"},
    # April
    {"date": "2026/04/01", "venue": "ST", "last_race_time": "22:00", "note": "⚠ AWT night (Wed)"},
    {"date": "2026/04/06", "venue": "ST", "last_race_time": "18:00", "note": "Easter Monday"},
    {"date": "2026/04/08", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/04/12", "venue": "ST", "last_race_time": "18:00"},
    {"date": "2026/04/15", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/04/19", "venue": "ST", "last_race_time": "18:00"},
    {"date": "2026/04/22", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/04/26", "venue": "ST", "last_race_time": "18:00", "note": "FWD Champions Day"},
    {"date": "2026/04/29", "venue": "HV", "last_race_time": "23:00"},
    # May
    {"date": "2026/05/03", "venue": "ST", "last_race_time": "18:00"},
    {"date": "2026/05/06", "venue": "ST", "last_race_time": "22:00", "note": "⚠ AWT night (Wed)"},
    {"date": "2026/05/09", "venue": "ST", "last_race_time": "18:00"},
    {"date": "2026/05/13", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/05/17", "venue": "ST", "last_race_time": "18:00"},
    {"date": "2026/05/20", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/05/24", "venue": "ST", "last_race_time": "18:00", "note": "Champions & Chater"},
    {"date": "2026/05/27", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/05/31", "venue": "ST", "last_race_time": "18:00"},
    # June
    {"date": "2026/06/03", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/06/07", "venue": "ST", "last_race_time": "19:00", "note": "⚠ T=Twilight"},
    {"date": "2026/06/10", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/06/13", "venue": "ST", "last_race_time": "19:00", "note": "⚠ T=Twilight"},
    {"date": "2026/06/21", "venue": "ST", "last_race_time": "19:00", "note": "⚠ T=Twilight"},
    {"date": "2026/06/24", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/06/27", "venue": "ST", "last_race_time": "19:00", "note": "⚠ T=Twilight"},
    # July
    {"date": "2026/07/01", "venue": "ST", "last_race_time": "19:00", "note": "⚠ T=Twilight (Wed)"},
    {"date": "2026/07/04", "venue": "ST", "last_race_time": "19:00", "note": "⚠ T=Twilight"},
    {"date": "2026/07/08", "venue": "HV", "last_race_time": "23:00"},
    {"date": "2026/07/12", "venue": "ST", "last_race_time": "19:00", "note": "⚠ T=Twilight — season finale"},
]


def predict_date(race_date: str) -> str:
    """Evening before race day — when predict.py runs."""
    dt = datetime.strptime(race_date, "%Y/%m/%d") - timedelta(days=1)
    return dt.strftime("%Y/%m/%d")

def review_time(race_day: dict) -> str:
    """last_race_time + 65 min → HH:MM for review.py to run."""
    dt = datetime.strptime(race_day["last_race_time"], "%H:%M")
    return (dt + timedelta(minutes=65)).strftime("%H:%M")

def get_race_day(race_date: str) -> dict | None:
    """Look up RACE_CALENDAR entry by date string YYYY/MM/DD."""
    for rd in RACE_CALENDAR:
        if rd["date"] == race_date:
            return rd
    return None

def session_dirs(race_date: str, venue: str) -> dict:
    """
    Create and return all data sub-directories for one race session.
      data/raw/         — raw HTML / API cache
      data/predictions/ — prediction JSON + CSV
      data/results/     — result + dividends JSON, raw HTML
      data/combined/    — merged prediction + result CSV
    """
    tag  = race_date.replace("/", "-") + "_" + venue
    base = Path("data")
    dirs = {
        "raw":      base / "raw"         / tag,
        "pred":     base / "predictions" / tag,
        "results":  base / "results"     / tag,
        "combined": base / "combined"    / tag,
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs
# db_logger.py — Module 5: Persistent SQLite Database Logger
# Saves all race card, model scores, live odds, and results to war_room.db
# Called automatically by run.py — no manual execution needed

import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path("war_room.db")

# ─────────────────────────────────────────────
# 1. INITIALISE DATABASE — create tables if not exist
# ─────────────────────────────────────────────

def init_db():
    """Create all tables on first run. Safe to call every time."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # --- Meetings ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            venue       TEXT NOT NULL,
            going       TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, venue)
        )
    """)

    # --- Races ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS races (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id  INTEGER NOT NULL,
            race_no     INTEGER NOT NULL,
            name        TEXT,
            distance    INTEGER,
            surface     TEXT,
            race_class  TEXT,
            start_time  TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (meeting_id) REFERENCES meetings(id),
            UNIQUE(meeting_id, race_no)
        )
    """)

    # --- Runners (horse + model scores per race) ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS runners (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id         INTEGER NOT NULL,
            horse_no        INTEGER,
            horse_name      TEXT,
            horse_name_ch   TEXT,
            draw            INTEGER,
            jockey          TEXT,
            trainer         TEXT,
            rating          INTEGER,
            weight          INTEGER,
            gear            TEXT,
            last_6_runs     TEXT,
            days_since_run  INTEGER,
            -- v85 model output
            v85_score       REAL,
            v50_score       REAL,
            win_pct         REAL,
            tier            TEXT,
            -- individual factor scores (11 factors)
            score_recent_form       REAL,
            score_draw_bias         REAL,
            score_jockey_form       REAL,
            score_trainer_form      REAL,
            score_class_rating      REAL,
            score_weight            REAL,
            score_distance          REAL,
            score_surface           REAL,
            score_days_rest         REAL,
            score_barrier_trial     REAL,
            score_gear_change       REAL,
            created_at      TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (race_id) REFERENCES races(id),
            UNIQUE(race_id, horse_no)
        )
    """)

    # --- Odds Log (snapshot every 60s) ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS odds_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id     INTEGER NOT NULL,
            horse_no    INTEGER NOT NULL,
            horse_name  TEXT,
            win_odds    REAL,
            place_odds  REAL,
            implied_pct REAL,
            ev          REAL,
            is_value    INTEGER DEFAULT 0,   -- 1 if EV > 15%
            polled_at   TEXT NOT NULL,
            FOREIGN KEY (race_id) REFERENCES races(id)
        )
    """)

    # --- Results (post-race finishing positions) ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id     INTEGER NOT NULL UNIQUE,
            winner_no   INTEGER,
            winner_name TEXT,
            second_no   INTEGER,
            third_no    INTEGER,
            fourth_no   INTEGER,
            finish_json TEXT,   -- full finishing order as JSON array
            scraped_at  TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (race_id) REFERENCES races(id)
        )
    """)

    # --- Bets Log (optional manual entry) ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS bets_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id     INTEGER NOT NULL,
            horse_no    INTEGER,
            horse_name  TEXT,
            bet_type    TEXT,       -- WIN / PLACE / EW
            stake       REAL,
            odds_taken  REAL,
            return_amt  REAL DEFAULT 0,
            profit      REAL DEFAULT 0,
            result      TEXT,       -- WON / LOST / VOID
            logged_at   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (race_id) REFERENCES races(id)
        )
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Initialised → {DB_PATH.resolve()}")


# ─────────────────────────────────────────────
# 2. SAVE MEETING
# ─────────────────────────────────────────────

def save_meeting(date: str, venue: str, going: str = None) -> int:
    """
    Insert or update a meeting. Returns meeting_id.
    date format: 'YYYY-MM-DD'
    venue: 'ST' or 'HV'
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO meetings (date, venue, going)
        VALUES (?, ?, ?)
        ON CONFLICT(date, venue) DO UPDATE SET going=excluded.going
    """, (date, venue, going))
    conn.commit()
    c.execute("SELECT id FROM meetings WHERE date=? AND venue=?", (date, venue))
    meeting_id = c.fetchone()[0]
    conn.close()
    print(f"[DB] Meeting saved → id={meeting_id} | {date} {venue}")
    return meeting_id


# ─────────────────────────────────────────────
# 3. SAVE RACE CARD (from Module 1 + Module 2)
# ─────────────────────────────────────────────

def save_race_card(meeting_id: int, race_data: dict):
    """
    Save a single race + all its runners.
    race_data is one race object from race_card.json / race_data.json
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Insert race
    c.execute("""
        INSERT INTO races (meeting_id, race_no, name, distance, surface, race_class, start_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(meeting_id, race_no) DO UPDATE SET
            name=excluded.name,
            distance=excluded.distance,
            surface=excluded.surface,
            race_class=excluded.race_class,
            start_time=excluded.start_time
    """, (
        meeting_id,
        race_data.get("race_no"),
        race_data.get("race_name") or race_data.get("name"),
        race_data.get("distance"),
        race_data.get("surface"),
        race_data.get("race_class") or race_data.get("class"),
        race_data.get("start_time")
    ))
    conn.commit()

    c.execute("SELECT id FROM races WHERE meeting_id=? AND race_no=?",
              (meeting_id, race_data.get("race_no")))
    race_id = c.fetchone()[0]

    # Insert runners
    horses = race_data.get("horses") or race_data.get("runners") or []
    for h in horses:
        factors = h.get("factors") or h.get("scores") or {}
        c.execute("""
            INSERT INTO runners (
                race_id, horse_no, horse_name, horse_name_ch,
                draw, jockey, trainer, rating, weight, gear,
                last_6_runs, days_since_run,
                v85_score, v50_score, win_pct, tier,
                score_recent_form, score_draw_bias, score_jockey_form,
                score_trainer_form, score_class_rating, score_weight,
                score_distance, score_surface, score_days_rest,
                score_barrier_trial, score_gear_change
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(race_id, horse_no) DO UPDATE SET
                v85_score=excluded.v85_score,
                v50_score=excluded.v50_score,
                win_pct=excluded.win_pct,
                tier=excluded.tier,
                score_recent_form=excluded.score_recent_form,
                score_draw_bias=excluded.score_draw_bias,
                score_jockey_form=excluded.score_jockey_form,
                score_trainer_form=excluded.score_trainer_form,
                score_class_rating=excluded.score_class_rating,
                score_weight=excluded.score_weight,
                score_distance=excluded.score_distance,
                score_surface=excluded.score_surface,
                score_days_rest=excluded.score_days_rest,
                score_barrier_trial=excluded.score_barrier_trial,
                score_gear_change=excluded.score_gear_change
        """, (
            race_id,
            h.get("horse_no") or h.get("no"),
            h.get("horse_name") or h.get("name_en"),
            h.get("horse_name_ch") or h.get("name_ch"),
            h.get("draw"),
            h.get("jockey"),
            h.get("trainer"),
            h.get("rating"),
            h.get("weight"),
            h.get("gear"),
            json.dumps(h.get("last_6_runs") or h.get("recent_runs") or []),
            h.get("days_since_run"),
            h.get("v85") or h.get("v85_score"),
            h.get("v50") or h.get("v50_score"),
            h.get("win_pct"),
            h.get("tier"),
            factors.get("recent_form"),
            factors.get("draw_bias"),
            factors.get("jockey_form"),
            factors.get("trainer_form"),
            factors.get("class_rating"),
            factors.get("weight"),
            factors.get("distance"),
            factors.get("surface"),
            factors.get("days_rest"),
            factors.get("barrier_trial"),
            factors.get("gear_change"),
        ))

    conn.commit()
    conn.close()
    runners_count = len(horses)
    print(f"[DB] Race {race_data.get('race_no')} saved → race_id={race_id} | {runners_count} runners")
    return race_id


def save_full_race_card(meeting_id: int, races: list):
    """Convenience: save all races from race_card.json at once."""
    race_ids = {}
    for race in races:
        rid = save_race_card(meeting_id, race)
        race_ids[race.get("race_no")] = rid
    print(f"[DB] Full race card saved → {len(races)} races")
    return race_ids


# ─────────────────────────────────────────────
# 4. LOG ODDS SNAPSHOT (called every 60s from Module 3)
# ─────────────────────────────────────────────

def log_odds_snapshot(race_id: int, horses: list):
    """
    Log one odds snapshot for all horses in a race.
    Called every polling cycle from Module 3 (hkjc_scraper.py).
    horses: list of dicts with horse_no, win_odds, place_odds, ev, etc.
    """
    if not horses:
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = []
    for h in horses:
        win_odds   = h.get("win_odds") or h.get("odds")
        place_odds = h.get("place_odds")
        implied    = round(1 / win_odds, 4) if win_odds and win_odds > 0 else None
        ev         = h.get("ev")
        is_value   = 1 if (ev and ev > 0.15) else 0

        rows.append((
            race_id,
            h.get("horse_no") or h.get("no"),
            h.get("horse_name") or h.get("name_en"),
            win_odds,
            place_odds,
            implied,
            ev,
            is_value,
            now
        ))

    c.executemany("""
        INSERT INTO odds_log
            (race_id, horse_no, horse_name, win_odds, place_odds,
             implied_pct, ev, is_value, polled_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, rows)

    conn.commit()
    conn.close()
    # Silent — don't spam console every 60s


# ─────────────────────────────────────────────
# 5. SAVE RESULTS (post-race, auto-scraped)
# ─────────────────────────────────────────────

def save_results(race_id: int, finishing_order: list):
    """
    Save post-race finishing order.
    finishing_order: list of horse_no in finishing position
    e.g. [7, 3, 11, 1, ...] → 7 won, 3 second, etc.
    """
    if not finishing_order:
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    def safe_get(lst, idx):
        return lst[idx] if len(lst) > idx else None

    # Also look up horse names from runners table
    placeholders = {
        1: safe_get(finishing_order, 0),
        2: safe_get(finishing_order, 1),
        3: safe_get(finishing_order, 2),
        4: safe_get(finishing_order, 3),
    }

    winner_name = None
    if placeholders[1]:
        c.execute("SELECT horse_name FROM runners WHERE race_id=? AND horse_no=?",
                  (race_id, placeholders[1]))
        row = c.fetchone()
        winner_name = row[0] if row else None

    c.execute("""
        INSERT INTO results
            (race_id, winner_no, winner_name, second_no, third_no, fourth_no, finish_json)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(race_id) DO UPDATE SET
            winner_no=excluded.winner_no,
            winner_name=excluded.winner_name,
            second_no=excluded.second_no,
            third_no=excluded.third_no,
            fourth_no=excluded.fourth_no,
            finish_json=excluded.finish_json,
            scraped_at=datetime('now','localtime')
    """, (
        race_id,
        placeholders[1],
        winner_name,
        placeholders[2],
        placeholders[3],
        placeholders[4],
        json.dumps(finishing_order)
    ))

    conn.commit()
    conn.close()
    print(f"[DB] Results saved → race_id={race_id} | Winner: #{placeholders[1]} {winner_name}")


# ─────────────────────────────────────────────
# 6. LOG BET (optional manual or auto entry)
# ─────────────────────────────────────────────

def log_bet(race_id: int, horse_no: int, bet_type: str,
            stake: float, odds_taken: float,
            horse_name: str = None):
    """
    Log a bet before the race.
    Result and return are updated later via update_bet_result().
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO bets_log (race_id, horse_no, horse_name, bet_type, stake, odds_taken)
        VALUES (?,?,?,?,?,?)
    """, (race_id, horse_no, horse_name, bet_type, stake, odds_taken))
    conn.commit()
    bet_id = c.lastrowid
    conn.close()
    print(f"[DB] Bet logged → #{horse_no} {horse_name} | {bet_type} HK${stake} @ {odds_taken}")
    return bet_id


def update_bet_result(bet_id: int, result: str, return_amt: float):
    """
    Update a bet after the race with WON/LOST and return amount.
    result: 'WON' | 'LOST' | 'VOID'
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE bets_log
        SET result=?, return_amt=?, profit=? - stake
        WHERE id=?
    """, (result, return_amt, return_amt, bet_id))
    conn.commit()
    conn.close()
    print(f"[DB] Bet #{bet_id} updated → {result} | Return: HK${return_amt}")


# ─────────────────────────────────────────────
# 7. QUERY HELPERS (for backtesting / review)
# ─────────────────────────────────────────────

def get_race_id(date: str, venue: str, race_no: int) -> int | None:
    """Look up race_id given date, venue, race_no."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT r.id FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.date=? AND m.venue=? AND r.race_no=?
    """, (date, venue, race_no))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def get_ev_summary(date: str, venue: str) -> list:
    """
    Return all EV value picks logged for a meeting day.
    Useful for post-meeting review.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT
            r.race_no,
            ol.horse_no,
            ol.horse_name,
            MAX(ol.win_odds)   AS peak_odds,
            MIN(ol.win_odds)   AS min_odds,
            MAX(ol.ev)         AS peak_ev,
            COUNT(*)           AS snapshots,
            res.winner_no
        FROM odds_log ol
        JOIN races r ON ol.race_id = r.id
        JOIN meetings m ON r.meeting_id = m.id
        LEFT JOIN results res ON res.race_id = r.id
        WHERE m.date=? AND m.venue=? AND ol.is_value=1
        GROUP BY r.race_no, ol.horse_no
        ORDER BY r.race_no, peak_ev DESC
    """, (date, venue))
    rows = c.fetchall()
    conn.close()
    return rows


def get_roi_summary() -> dict:
    """Calculate overall ROI from bets_log."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT
            COUNT(*)                            AS total_bets,
            SUM(stake)                          AS total_staked,
            SUM(return_amt)                     AS total_returned,
            SUM(profit)                         AS total_profit,
            ROUND(SUM(profit)/SUM(stake)*100,2) AS roi_pct,
            SUM(CASE WHEN result='WON' THEN 1 ELSE 0 END) AS wins
        FROM bets_log
        WHERE result IS NOT NULL AND result != 'VOID'
    """)
    row = c.fetchone()
    conn.close()
    if not row or not row[1]:
        return {"message": "No settled bets yet"}
    keys = ["total_bets","total_staked","total_returned","total_profit","roi_pct","wins"]
    return dict(zip(keys, row))


# ─────────────────────────────────────────────
# 8. LOAD FROM JSON FILES (bulk import helper)
# ─────────────────────────────────────────────

def import_from_json(race_card_path="race_card.json",
                     race_data_path="race_data.json",
                     date: str = None,
                     venue: str = None):
    """
    Bulk import from existing JSON files.
    Called once at startup by run.py after scraping completes.
    """
    # Load race card JSON
    if not os.path.exists(race_card_path):
        print(f"[DB] {race_card_path} not found — skipping import")
        return

    with open(race_card_path, "r", encoding="utf-8") as f:
        card = json.load(f)

    # Auto-detect date and venue from JSON if not provided
    if not date:
        date = card.get("date") or datetime.now().strftime("%Y-%m-%d")
    if not venue:
        venue = card.get("venue") or "ST"

    going   = card.get("going")
    races   = card.get("races") or card.get("race_list") or []

    if not races:
        print(f"[DB] No races found in {race_card_path}")
        return

    # Merge model scores from race_data.json if available
    if os.path.exists(race_data_path):
        with open(race_data_path, "r", encoding="utf-8") as f:
            live = json.load(f)
        live_races = {r.get("race_no"): r
                      for r in (live.get("races") or live.get("race_list") or [])}

        for race in races:
            rno = race.get("race_no")
            if rno in live_races:
                live_r = live_races[rno]
                # Merge live model scores into race card runners
                live_horses = {
                    h.get("horse_no") or h.get("no"): h
                    for h in (live_r.get("horses") or live_r.get("runners") or [])
                }
                for h in (race.get("horses") or race.get("runners") or []):
                    hno = h.get("horse_no") or h.get("no")
                    if hno in live_horses:
                        lh = live_horses[hno]
                        h.setdefault("v85_score", lh.get("v85") or lh.get("v85_score"))
                        h.setdefault("v50_score", lh.get("v50") or lh.get("v50_score"))
                        h.setdefault("win_pct",   lh.get("win_pct"))
                        h.setdefault("tier",      lh.get("tier"))
                        h.setdefault("factors",   lh.get("factors") or lh.get("scores") or {})

    # Save to DB
    meeting_id = save_meeting(date, venue, going)
    save_full_race_card(meeting_id, races)
    print(f"[DB] Import complete → {date} {venue} | {len(races)} races")
    return meeting_id
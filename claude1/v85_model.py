"""
HKJC Win Rate Model — Module 2 (v85)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reads race_card.json (from Module 1), calculates win probability
for each horse, writes scores back into race_card.json.

Usage:
  python v85_model.py                         # uses race_card.json
  python v85_model.py --input race_card.json  # explicit path

Output:
  race_card.json — each horse gains:
    win_score       float   raw model score (0-100)
    win_pct         float   normalised probability per race (sums to 100)
    tier            str     A (≥25%) / B (≥15%) / C (≥8%) / D (<8%)
    value_flag      bool    True after Module 3 compares to live odds
    score_breakdown dict    each factor's contribution
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from datetime import datetime

# ─── WEIGHTS (sum must equal 100) ────────────────────────────────
W = {
    "recent_form":         22,  # last 6 avg position + top3 rate
    "class_rating":        16,  # current rating vs field
    "draw_bias":           12,  # gate win% this course/distance
    "jockey_form":         10,  # jkc rank + avg pts
    "trainer_form":         8,  # tnc rank + avg pts
    "distance_profile":     8,  # horse win% at today's distance
    "venue_profile":        6,  # horse win% at today's venue
    "days_since_run":       6,  # freshness curve
    "horse_weight":         5,  # carried wt vs field avg
    "gear_change":          4,  # gear on/off flag
    "jockey_trainer_combo": 3,  # historical j/t combo on this horse
}
assert sum(W.values()) == 100, f"Weights must sum to 100, got {sum(W.values())}"


# ─── FACTOR FUNCTIONS ────────────────────────────────────────────

def score_recent_form(h: dict, max_pts: int) -> float:
    rf        = h.get('recent_form', {})
    positions = rf.get('last_6_positions', [])
    top3_rate = rf.get('top3_rate_pct', 0) or 0
    avg_pos   = rf.get('avg_position') or 0
    if not positions:
        return max_pts * 0.3
    pos_score  = max(0, (14 - avg_pos) / 13)   # 1=avg pos 1, 0=avg pos 14
    top3_score = min(top3_rate / 100, 1.0)
    return round((0.6 * pos_score + 0.4 * top3_score) * max_pts, 3)


def score_class_rating(h: dict, horses: list, max_pts: int) -> float:
    ratings = [x.get('rating') for x in horses
               if x.get('rating') and not x.get('scratched')]
    if not ratings or not h.get('rating'):
        return max_pts * 0.5
    lo, hi = min(ratings), max(ratings)
    spread = hi - lo if hi != lo else 1
    return round(((h['rating'] - lo) / spread) * max_pts, 3)


def score_draw_bias(h: dict, max_pts: int) -> float:
    win_pct   = h.get('draw_stats', {}).get('win_pct', 0) or 0
    race_draw = h.get('_race_draw_bias', {})
    all_pcts  = [g.get('win_pct', 0) for g in race_draw.values() if g.get('win_pct')]
    avg_pct   = sum(all_pcts) / len(all_pcts) if all_pcts else 10
    if avg_pct == 0:
        return max_pts * 0.5
    ratio = win_pct / avg_pct
    return round(min(ratio / 2, 1.0) * max_pts, 3)


def score_jockey_form(h: dict, max_pts: int) -> float:
    js   = h.get('jockey_stats', {})
    rank = js.get('jkc_rank') or 20
    avg  = js.get('avg_pts')  or 0
    return round((0.5 * max(0, (20 - rank) / 19) + 0.5 * min(avg / 40, 1.0)) * max_pts, 3)


def score_trainer_form(h: dict, max_pts: int) -> float:
    ts   = h.get('trainer_stats', {})
    rank = ts.get('tnc_rank') or 25
    avg  = ts.get('avg_pts')  or 0
    return round((0.5 * max(0, (25 - rank) / 24) + 0.5 * min(avg / 30, 1.0)) * max_pts, 3)


def score_distance_profile(h: dict, distance: int, max_pts: int) -> float:
    prof    = h.get('profile_distance', {})
    starts  = prof.get('starts', 0)
    win_pct = prof.get('win_pct', 0) or 0
    if starts == 0:
        return max_pts * 0.35
    score = min(win_pct / 33, 1.0)
    if starts >= 5:
        score = min(score * 1.1, 1.0)
    return round(score * max_pts, 3)


def score_venue_profile(h: dict, venue: str, max_pts: int) -> float:
    prof    = h.get('profile_venue', {})
    starts  = prof.get('starts', 0)
    win_pct = prof.get('win_pct', 0) or 0
    if starts == 0:
        return max_pts * 0.4
    return round(min(win_pct / 25, 1.0) * max_pts, 3)


def score_days_since_run(h: dict, max_pts: int) -> float:
    days = h.get('days_since_run') or 0
    if   7  <= days <= 21: score = 1.0
    elif 22 <= days <= 42: score = 0.8
    elif 43 <= days <= 60: score = 0.6
    elif days > 60:        score = 0.3
    elif days < 7:         score = 0.7
    else:                  score = 0.5
    return round(score * max_pts, 3)


def score_horse_weight(h: dict, horses: list, max_pts: int) -> float:
    weights = [x.get('carried_wt') for x in horses
               if x.get('carried_wt') and not x.get('scratched')]
    if not weights or not h.get('carried_wt'):
        return max_pts * 0.5
    lo, hi = min(weights), max(weights)
    spread = hi - lo if hi != lo else 1
    return round(((hi - h['carried_wt']) / spread) * max_pts, 3)


def score_gear_change(h: dict, max_pts: int) -> float:
    gear = h.get('gear', '') or ''
    if not gear:       score = 0.8   # no gear = natural
    elif '-' in gear:  score = 0.7   # gear removed
    else:              score = 0.5   # gear on
    return round(score * max_pts, 3)


def score_jockey_trainer_combo(h: dict, max_pts: int) -> float:
    prof    = h.get('profile_jockey', {})
    starts  = prof.get('starts', 0)
    win_pct = prof.get('win_pct', 0) or 0
    if starts == 0:
        return max_pts * 0.4
    return round(min(win_pct / 25, 1.0) * max_pts, 3)


# ─── SCORE ONE HORSE ─────────────────────────────────────────────

def score_horse(h: dict, horses: list, race_meta: dict, venue: str) -> dict:
    if h.get('scratched'):
        return {"win_score": 0, "win_pct": 0, "tier": "SCR",
                "value_flag": False, "score_breakdown": {}}
    distance = race_meta.get('distance', 1200)
    bd = {
        "recent_form":          score_recent_form(h, W["recent_form"]),
        "class_rating":         score_class_rating(h, horses, W["class_rating"]),
        "draw_bias":            score_draw_bias(h, W["draw_bias"]),
        "jockey_form":          score_jockey_form(h, W["jockey_form"]),
        "trainer_form":         score_trainer_form(h, W["trainer_form"]),
        "distance_profile":     score_distance_profile(h, distance, W["distance_profile"]),
        "venue_profile":        score_venue_profile(h, venue, W["venue_profile"]),
        "days_since_run":       score_days_since_run(h, W["days_since_run"]),
        "horse_weight":         score_horse_weight(h, horses, W["horse_weight"]),
        "gear_change":          score_gear_change(h, W["gear_change"]),
        "jockey_trainer_combo": score_jockey_trainer_combo(h, W["jockey_trainer_combo"]),
    }
    return {"win_score": round(sum(bd.values()), 3), "win_pct": 0,
            "tier": "", "value_flag": False, "score_breakdown": bd}


# ─── NORMALISE PER RACE ──────────────────────────────────────────

def normalise_race(horses: list) -> list:
    active = [h for h in horses if not h.get('scratched')]
    total  = sum(h.get('win_score', 0) for h in active)
    for h in horses:
        if h.get('scratched'):
            continue
        wp = round(h.get('win_score', 0) / total * 100, 2) if total else 0
        h['win_pct'] = wp
        if   wp >= 25: h['tier'] = 'A'
        elif wp >= 15: h['tier'] = 'B'
        elif wp >= 8:  h['tier'] = 'C'
        else:          h['tier'] = 'D'
    return horses


# ─── PROCESS ONE RACE ────────────────────────────────────────────

def process_race(race: dict, venue: str) -> dict:
    meta      = race.get('race_meta', {})
    horses    = race.get('horses', [])
    draw_bias = race.get('draw_bias', {})

    # Inject race-level draw bias into each horse for scoring
    for h in horses:
        h['_race_draw_bias'] = draw_bias

    # Score + normalise
    for h in horses:
        h.update(score_horse(h, horses, meta, venue))
    horses = normalise_race(horses)

    # Sort active by win_pct, scratched to end
    active    = sorted([h for h in horses if not h.get('scratched')],
                       key=lambda x: x['win_pct'], reverse=True)
    scratched = [h for h in horses if h.get('scratched')]
    race['horses'] = active + scratched

    rn = meta.get('race_no', '?')
    runners = len(active)
    top = active[0] if active else {}
    print(f"  Race {rn}: {meta.get('race_name','?')[:28]:<28} "
          f"{runners} runners  → "
          f"#{top.get('no','-')} {top.get('name','?'):<18} {top.get('win_pct',0):.1f}% [{top.get('tier','-')}]")
    return race


# ─── MAIN ────────────────────────────────────────────────────────

def run_model(input_file: str = "race_card.json"):
    fpath = Path(input_file)
    if not fpath.exists():
        print(f"[ERROR] {fpath} not found. Run race_card_scraper.py first.")
        import sys; sys.exit(1)

    data  = json.loads(fpath.read_text())
    venue = data.get('venue', 'HV')
    date  = data.get('date', '')
    races = data.get('races', [])

    print(f"\n{'='*60}")
    print(f"  v85 Win Rate Model  |  {date}  {venue}  ({len(races)} races)")
    print(f"{'='*60}\n")

    data['races']        = [process_race(r, venue) for r in races]
    data['model_run_at'] = datetime.now().isoformat()
    data['model_version']= 'v85'
    data['weights']      = W

    fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    print(f"\n  ✅ Scores written to {fpath}")

    _print_leaderboard(data)
    return data


def _print_leaderboard(data: dict):
    print(f"\n{'='*60}")
    print(f"  LEADERBOARD  {data.get('date','')}  {data.get('venue','')}")
    print(f"{'='*60}")
    for race in data['races']:
        m = race['race_meta']
        hs = sorted([h for h in race['horses'] if not h.get('scratched')],
                    key=lambda x: x.get('win_pct', 0), reverse=True)
        print(f"\n  Race {m['race_no']} — {m.get('race_name','')} "
              f"({m.get('distance','')}m {m.get('surface','')} "
              f"\"{m.get('course_config','')}\") {m.get('going','')}")
        print(f"  {'#':>3}  {'Horse':<22} {'Win%':>6}  Tier  Draw   Rtg")
        print(f"  {'-'*52}")
        for h in hs:
            print(f"  #{h['no']:>2}  {h['name']:<22} {h['win_pct']:>5.1f}%   "
                  f"{h['tier']}   Drw{h.get('draw',0):>2}  Rtg{h.get('rating','?'):>3}")
    print()


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="HKJC Win Rate Model — Module 2 (v85)")
    ap.add_argument("--input", default="race_card.json", help="Path to race_card.json")
    args = ap.parse_args()
    run_model(args.input)

if __name__ == "__main__":
    main()

"""
v85_scoring.py — V85-style scoring system for HKJC predictions
Based on the user's weighting table with all factors.
"""

import re
from typing import Dict, List, Any

# Weights from user's table
V85_WEIGHTS = {
    "recent_form": 30,
    "class_rating": 15,
    "draw_bias": 15,
    "jockey_form": 10,
    "trainer_form": 8,
    "distance_profile": 8,
    "venue_profile": 5,
    "days_since_run": 5,
    "horse_weight": 2,
    "gear_change": 2,
}

def parse_last6_runs(last6: str) -> Dict[str, Any]:
    """Parse last 6 runs string into statistics."""
    if not last6 or last6.strip() == "":
        return {"positions": [], "avg_position": 0, "top3_count": 0, "top3_rate": 0}

    # Extract positions from string like "1-2-3-4-5-6"
    positions = []
    for char in last6:
        if char.isdigit():
            pos = int(char)
            if 1 <= pos <= 14:  # Valid positions
                positions.append(pos)

    positions = positions[:6]  # Take last 6

    if not positions:
        return {"positions": [], "avg_position": 0, "top3_count": 0, "top3_rate": 0}

    avg_pos = sum(positions) / len(positions)
    top3_count = sum(1 for p in positions if p <= 3)
    top3_rate = top3_count / len(positions)

    return {
        "positions": positions,
        "avg_position": avg_pos,
        "top3_count": top3_count,
        "top3_rate": top3_rate
    }

def score_recent_form(h: Dict, max_pts: int) -> float:
    """Score based on recent form (last 6 runs)."""
    rf = parse_last6_runs(h.get("last6_runs", ""))

    if not rf["positions"]:
        return max_pts * 0.3

    # Score based on average position (lower is better)
    pos_score = max(0, (14 - rf["avg_position"]) / 13)

    # Score based on top 3 rate
    top3_score = rf["top3_rate"]

    return round((0.6 * pos_score + 0.4 * top3_score) * max_pts, 3)

def score_class_rating(h: Dict, horses: List[Dict], max_pts: int) -> float:
    """Score based on rating vs field."""
    ratings = [x.get("rating", 50) for x in horses if x.get("rating")]
    if not ratings or not h.get("rating"):
        return max_pts * 0.5

    min_r, max_r = min(ratings), max(ratings)
    if max_r == min_r:
        return max_pts * 0.5

    # Higher rating gets higher score
    return round(((h["rating"] - min_r) / (max_r - min_r)) * max_pts, 3)

def score_draw_bias(h: Dict, max_pts: int) -> float:
    """Score based on draw bias (simplified - using draw position)."""
    draw = h.get("draw", 1)
    # Simple heuristic: middle draws are better
    if draw <= 2:
        score = 0.6
    elif draw <= 6:
        score = 1.0
    elif draw <= 10:
        score = 0.8
    else:
        score = 0.4

    return round(score * max_pts, 3)

def score_jockey_form(h: Dict, jockey_stats: Dict, max_pts: int) -> float:
    """Score based on jockey statistics."""
    jockey = h.get("jockey", "")
    stats = jockey_stats.get(jockey, {})
    win_rate = stats.get("win_rate", 0)

    # Simple scoring based on win rate
    return round(min(win_rate * 100 / 20, 1.0) * max_pts, 3)

def score_trainer_form(h: Dict, trainer_stats: Dict, max_pts: int) -> float:
    """Score based on trainer statistics."""
    trainer = h.get("trainer", "")
    stats = trainer_stats.get(trainer, {})
    win_rate = stats.get("win_rate", 0)

    return round(min(win_rate * 100 / 15, 1.0) * max_pts, 3)

def score_distance_profile(h: Dict, race_distance: int, max_pts: int) -> float:
    """Score based on horse's performance at this distance (simplified)."""
    # Simple heuristic based on distance ranges
    if 1000 <= race_distance <= 1200:
        score = 0.8  # Most common distance
    elif 1201 <= race_distance <= 1400:
        score = 0.9
    elif 1401 <= race_distance <= 1600:
        score = 0.7
    elif 1601 <= race_distance <= 1800:
        score = 0.6
    else:
        score = 0.5

    return round(score * max_pts, 3)

def score_venue_profile(h: Dict, venue: str, max_pts: int) -> float:
    """Score based on horse's performance at this venue (simplified)."""
    # Simple heuristic - assume horses perform similarly at all venues
    return round(0.7 * max_pts, 3)

def score_days_since_run(h: Dict, max_pts: int) -> float:
    """Score based on freshness (simplified - assume 14 days)."""
    # Assume average of 14 days since last run
    days = 14
    if 7 <= days <= 21:
        score = 1.0
    elif 22 <= days <= 42:
        score = 0.8
    elif 43 <= days <= 60:
        score = 0.6
    else:
        score = 0.4

    return round(score * max_pts, 3)

def score_horse_weight(h: Dict, horses: List[Dict], max_pts: int) -> float:
    """Score based on weight carried vs field."""
    weights = [x.get("weight_lbs", 126) for x in horses]
    if not weights or not h.get("weight_lbs"):
        return max_pts * 0.5

    avg_weight = sum(weights) / len(weights)
    horse_weight = h["weight_lbs"]

    # Higher weight relative to field average is better
    if horse_weight >= avg_weight:
        score = 1.0
    else:
        score = 0.5 + 0.5 * (horse_weight / avg_weight)

    return round(score * max_pts, 3)

def score_gear_change(h: Dict, max_pts: int) -> float:
    """Score based on gear changes."""
    gear = h.get("gear", "")
    if not gear:
        score = 0.8  # No gear is good
    elif "-" in gear:
        score = 0.7  # Gear removed
    else:
        score = 0.5  # Gear added

    return round(score * max_pts, 3)

def score_horse_v85(h: Dict, horses: List[Dict], race_meta: Dict,
                   jockey_stats: Dict, trainer_stats: Dict) -> Dict:
    """Score a single horse using V85 system."""
    venue = race_meta.get("venue", "ST")
    distance = race_meta.get("distance", 1200)

    breakdown = {
        "recent_form": score_recent_form(h, V85_WEIGHTS["recent_form"]),
        "class_rating": score_class_rating(h, horses, V85_WEIGHTS["class_rating"]),
        "draw_bias": score_draw_bias(h, V85_WEIGHTS["draw_bias"]),
        "jockey_form": score_jockey_form(h, jockey_stats, V85_WEIGHTS["jockey_form"]),
        "trainer_form": score_trainer_form(h, trainer_stats, V85_WEIGHTS["trainer_form"]),
        "distance_profile": score_distance_profile(h, distance, V85_WEIGHTS["distance_profile"]),
        "venue_profile": score_venue_profile(h, venue, V85_WEIGHTS["venue_profile"]),
        "days_since_run": score_days_since_run(h, V85_WEIGHTS["days_since_run"]),
        "horse_weight": score_horse_weight(h, horses, V85_WEIGHTS["horse_weight"]),
        "gear_change": score_gear_change(h, V85_WEIGHTS["gear_change"]),
    }

    win_score = sum(breakdown.values())
    return {
        "win_score": round(win_score, 3),
        "score_breakdown": breakdown
    }

def score_race_v85(race: Dict, jockey_stats: Dict, trainer_stats: Dict) -> List[Dict]:
    """Score all horses in a race using V85 system."""
    horses = race.get("horses", [])
    race_meta = {
        "venue": race.get("venue", "ST"),
        "distance": race.get("distance", 1200)
    }

    scored_horses = []
    for h in horses:
        score_data = score_horse_v85(h, horses, race_meta, jockey_stats, trainer_stats)
        horse_result = h.copy()
        horse_result.update(score_data)
        scored_horses.append(horse_result)

    # Normalize to percentages
    total_score = sum(h.get("win_score", 0) for h in scored_horses)
    if total_score > 0:
        for h in scored_horses:
            h["win_pct"] = round(h.get("win_score", 0) / total_score * 100, 2)

    return scored_horses
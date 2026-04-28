#!/usr/bin/env python3
"""test_weights.py — Test specific weight configurations

Usage:
  python test_weights.py
"""

import json
from pathlib import Path
from eval_weights import evaluate_weights, load_meeting_files, normalize_weights

# Current weights from weights.json
with open("weights.json") as f:
    data = json.load(f)

CURRENT_WEIGHTS = data["WEIGHTS_GENERAL"]

# Provided weights from user table
# Mapping to current factors:
# recent_form (30%) -> form
# draw_bias (15%) -> draw
# class_rating (15%) -> rating
# jockey_form (10%) -> jockey
# trainer_form (8%) -> trainer
# horse_weight (2%) -> weight
# For market and h2h (not in table), keep current proportions but scale
PROPOSED_WEIGHTS_RAW = {
    "form": 30,
    "draw": 15,
    "rating": 15,
    "jockey": 10,
    "trainer": 8,
    "weight": 2,
    "market": 10,  # kept from current
    "h2h": 10,     # kept from current
}

PROPOSED_WEIGHTS = normalize_weights(PROPOSED_WEIGHTS_RAW)

def main():
    meetings = load_meeting_files(8)

    print("Testing weight configurations...")
    print(f"Meetings: {len(meetings)}")

    # Test current weights
    hits_current, picks_current = evaluate_weights(CURRENT_WEIGHTS, meetings, data["WEIGHTS_CLASSIC"])
    rate_current = hits_current / picks_current * 100 if picks_current > 0 else 0

    # Test proposed weights
    hits_proposed, picks_proposed = evaluate_weights(PROPOSED_WEIGHTS, meetings, data["WEIGHTS_CLASSIC"])
    rate_proposed = hits_proposed / picks_proposed * 100 if picks_proposed > 0 else 0

    print("\nCurrent weights:")
    print(f"  {CURRENT_WEIGHTS}")
    print(f"  Hit rate: {rate_current:.2f}% ({hits_current}/{picks_current})")

    print("\nProposed weights (mapped from table):")
    print(f"  {PROPOSED_WEIGHTS}")
    print(f"  Hit rate: {rate_proposed:.2f}% ({hits_proposed}/{picks_proposed})")

    diff = rate_proposed - rate_current
    print(f"\nDifference: {diff:+.2f}%")

if __name__ == "__main__":
    main()
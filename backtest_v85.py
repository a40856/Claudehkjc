#!/usr/bin/env python3
"""backtest_v85.py — Backtest V85 scoring system

Usage:
  python backtest_v85.py
  python backtest_v85.py --window 12
"""

import argparse
from eval_weights import load_meeting_files, build_stats, build_races, load_actual_top4
from v85_scoring import score_race_v85

def evaluate_v85(meetings: list[tuple]) -> tuple[int, int]:
    total_hits = 0
    total_picks = 0

    for raw_path, results_path in meetings:
        jockey_stats, trainer_stats = build_stats(raw_path)
        races = build_races(raw_path)
        actual_map = load_actual_top4(results_path)

        for race in races:
            race_no = int(race["race_no"])
            actual = actual_map.get(race_no, [])
            if not actual:
                continue

            # Score with V85 system
            scored_horses = score_race_v85(race, jockey_stats, trainer_stats)

            # Get top 4 predictions
            sorted_horses = sorted(scored_horses, key=lambda x: x.get("win_score", 0), reverse=True)
            predicted = [str(h.get("horse_no", "")) for h in sorted_horses[:4]]
            predicted = [p for p in predicted if p]

            hits = sum(1 for p in predicted if p in actual)
            total_hits += hits
            total_picks += len(predicted)

    return total_hits, total_picks

def main():
    parser = argparse.ArgumentParser(description="Backtest V85 scoring system")
    parser.add_argument("--window", type=int, default=8,
                        help="Number of most recent reviewed meetings to use")
    args = parser.parse_args()

    meetings = load_meeting_files(args.window)

    print(f"Backtesting V85 system with {len(meetings)} meetings...")

    hits, picks = evaluate_v85(meetings)
    rate = hits / picks * 100 if picks > 0 else 0

    print(".2f")

if __name__ == "__main__":
    main()
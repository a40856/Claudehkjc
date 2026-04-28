#!/usr/bin/env python3
"""eval_weights.py — Weekly weight evaluation and tuning

This script evaluates the current prediction weights against historical
race results and searches for a better weight vector that maximizes
Top-4 hit rate.

It only adjusts the active scoring weights used by score_field():
form, rating, market, draw, jockey, trainer, h2h, weight.

Usage:
  python eval_weights.py
  python eval_weights.py --window 8 --dry-run
  python eval_weights.py --window 8 --save
"""

import argparse
import json
import math
import random
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import TARGET_HIT_RATE, WEIGHTS_CLASSIC, WEIGHTS_FILE, WEIGHTS_GENERAL
from predict import CLASSIC_RACE_NAMES, score_field

DATA_RAW = Path("data/raw")
DATA_RESULTS = Path("data/results")
CACHE_DIR = Path("data/cache")
EVAL_HISTORY_PATH = Path("data/weight_eval.json")

ACTIVE_WEIGHT_KEYS = [
    "form", "rating", "market", "draw",
    "jockey", "trainer", "h2h", "weight"
]


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate and tune prediction weights")
    parser.add_argument("--window", type=int, default=8,
                        help="Number of most recent reviewed meetings to use")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate without saving weight changes")
    parser.add_argument("--save", action="store_true",
                        help="Save improved weights to weights.json")
    parser.add_argument("--iterations", type=int, default=500,
                        help="Number of random candidate evaluations")
    parser.add_argument("--seed", type=int, default=2026,
                        help="Random seed for weight search")
    return parser.parse_args()


def normalize_weights(weights: dict) -> dict:
    cleaned = {k: max(0.0, float(weights.get(k, 0.0))) for k in ACTIVE_WEIGHT_KEYS}
    total = sum(cleaned.values())
    if total <= 0:
        return {k: 1.0 / len(ACTIVE_WEIGHT_KEYS) for k in ACTIVE_WEIGHT_KEYS}
    return {k: v / total for k, v in cleaned.items()}


def perturb_weights(base: dict, scale: float = 0.15) -> dict:
    weights = deepcopy(base)
    keys = list(ACTIVE_WEIGHT_KEYS)
    n_changes = random.randint(1, max(1, len(keys) // 2))
    for _ in range(n_changes):
        key = random.choice(keys)
        delta = random.gauss(0, scale) * base.get(key, 0.1)
        weights[key] = max(0.0, weights.get(key, 0.0) + delta)
    return normalize_weights(weights)


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def load_meeting_files(window: int):
    raw_files = sorted([p for p in DATA_RAW.glob("*.xlsx")])
    meetings = []
    for raw_path in reversed(raw_files):
        results_path = DATA_RESULTS / raw_path.name
        if results_path.exists():
            meetings.append((raw_path, results_path))
        if len(meetings) >= window:
            break
    return list(reversed(meetings))


def build_stats(raw_path: Path) -> tuple[dict, dict]:
    with pd.ExcelFile(raw_path) as xlsx:
        jockey_df = pd.read_excel(xlsx, sheet_name="JockeyRanking")
        trainer_df = pd.read_excel(xlsx, sheet_name="TrainerRanking")

    jockey_stats = {
        str(row["jockey"]).strip(): {"win_rate": float(row.get("win_rate", 0) or 0)}
        for _, row in jockey_df.iterrows()
        if row.get("jockey")
    }
    trainer_stats = {
        str(row["trainer"]).strip(): {"win_rate": float(row.get("win_rate", 0) or 0)}
        for _, row in trainer_df.iterrows()
        if row.get("trainer")
    }
    return jockey_stats, trainer_stats


def build_history_cache(horse_ids: set) -> dict:
    cache = {}
    for horse_id in horse_ids:
        path = CACHE_DIR / f"horse_{horse_id}.json"
        if path.exists():
            try:
                cache[horse_id] = json.loads(path.read_text())
            except Exception:
                cache[horse_id] = []
        else:
            cache[horse_id] = []
    return cache


def build_races(raw_path: Path):
    with pd.ExcelFile(raw_path) as xlsx:
        racecard = pd.read_excel(xlsx, sheet_name="RaceCard")

    races = []
    for race_no, group in racecard.groupby("race_no"):
        if group.empty:
            continue
        first = group.iloc[0]
        horses = []
        for _, row in group.iterrows():
            horses.append({
                "horse_no": str(int(row["horse_no"])) if not pd.isna(row["horse_no"]) else "",
                "horse_id": str(row.get("horse_id", "")) if not pd.isna(row.get("horse_id", "")) else "",
                "horse_name": str(row.get("horse_name", "")) if not pd.isna(row.get("horse_name", "")) else "",
                "draw": int(row["draw"]) if not pd.isna(row["draw"]) else 0,
                "weight_lbs": float(row["weight_lbs"]) if not pd.isna(row["weight_lbs"]) else 126.0,
                "jockey": str(row.get("jockey", "")) if not pd.isna(row.get("jockey", "")) else "",
                "trainer": str(row.get("trainer", "")) if not pd.isna(row.get("trainer", "")) else "",
                "rating": float(row["rating"]) if not pd.isna(row["rating"]) else 50.0,
                "last6_runs": str(row.get("last6_runs", "")) if not pd.isna(row.get("last6_runs", "")) else "",
                "gear": str(row.get("gear", "")) if not pd.isna(row.get("gear", "")) else "",
                "win_odds": float(row["win_odds"]) if not pd.isna(row["win_odds"]) else 20.0,
            })
        races.append({
            "race_no": int(race_no),
            "race_name": str(first.get("race_name", "") or "").strip(),
            "distance": int(first.get("distance", 1200) or 1200),
            "surface": str(first.get("surface", "Turf") or "Turf"),
            "class_": str(first.get("class", "") or ""),
            "horses": horses,
        })
    return races


def load_actual_top4(results_path: Path):
    df = pd.read_excel(results_path, sheet_name="Results")
    actual = {}
    for race_no, group in df.groupby("race_no"):
        tops = group.sort_values("pos").head(4)["horse_no"].astype(str).tolist()
        actual[int(race_no)] = [str(x) for x in tops if str(x).strip()]
    return actual


def is_classic_race(race_name: str) -> bool:
    name = race_name.upper().strip()
    return any(key in name for key in CLASSIC_RACE_NAMES)


def evaluate_weights(weights: dict, meetings: list[tuple[Path, Path]], current_classic: dict) -> tuple[int, int]:
    total_hits = 0
    total_picks = 0

    for raw_path, results_path in meetings:
        jockey_stats, trainer_stats = build_stats(raw_path)
        races = build_races(raw_path)
        actual_map = load_actual_top4(results_path)

        horse_ids = {h["horse_id"] for race in races for h in race["horses"] if h.get("horse_id")}
        history_cache = build_history_cache(horse_ids)

        for race in races:
            race_no = int(race["race_no"])
            actual = actual_map.get(race_no, [])
            if not actual:
                continue
            weights_for_race = current_classic if is_classic_race(race["race_name"]) else weights
            scored = score_field(race, raw_path.stem.split("_")[-1], jockey_stats, trainer_stats, history_cache, weights_for_race)
            if scored.empty:
                continue
            predicted = scored.head(4)["horse_no"].astype(str).tolist()
            hits = sum(1 for p in predicted if p in actual)
            total_hits += hits
            total_picks += 4
    return total_hits, total_picks


def score_candidate(weights: dict, meetings: list[tuple[Path, Path]], current_classic: dict) -> float:
    hits, picks = evaluate_weights(weights, meetings, current_classic)
    return float(hits) / picks * 100 if picks else 0.0


def search_best_weights(base_weights: dict, meetings: list[tuple[Path, Path]], current_classic: dict, iterations: int):
    best_weights = normalize_weights(base_weights)
    best_score = score_candidate(best_weights, meetings, current_classic)
    print(f"Starting hit rate: {best_score:.2f}% with base weights")

    for i in range(iterations):
        candidate = perturb_weights(best_weights, scale=0.15)
        score = score_candidate(candidate, meetings, current_classic)
        if score > best_score:
            best_score = score
            best_weights = candidate
            print(f"  New best {score:.2f}% at iteration {i + 1}")
    return best_weights, best_score


def persist_weights(weights: dict):
    existing = load_json(WEIGHTS_FILE) or {}
    existing["WEIGHTS_GENERAL"] = weights
    existing["TARGET_HIT_RATE"] = TARGET_HIT_RATE
    WEIGHTS_FILE.write_text(json.dumps(existing, indent=2))
    print(f"Saved optimized weights to {WEIGHTS_FILE}")


def main():
    args = parse_args()
    random.seed(args.seed)

    meetings = load_meeting_files(args.window)
    if not meetings:
        raise SystemExit("No reviewed meetings found in data/raw and data/results.")

    print(f"Evaluating {len(meetings)} meetings (window={args.window})")
    current_classic = normalize_weights(WEIGHTS_CLASSIC)
    base_general = normalize_weights(WEIGHTS_GENERAL)
    base_score = score_candidate(base_general, meetings, current_classic)
    best_general, best_score = search_best_weights(base_general, meetings, current_classic, args.iterations)

    print("\n=== Evaluation Summary ===")
    print(f"Current general weights: {base_general}")
    print(f"Base hit rate:          {base_score:.2f}%")
    print(f"Best general weights:    {best_general}")
    print(f"Best hit rate:          {best_score:.2f}%")
    print(f"Target hit rate:        {TARGET_HIT_RATE:.1f}%")

    if args.save and best_score > base_score:
        persist_weights(best_general)
        EVAL_HISTORY_PATH.write_text(json.dumps({
            "updated_at": datetime.now().isoformat(),
            "window": args.window,
            "iterations": args.iterations,
            "base_hit_rate": base_score,
            "best_hit_rate": best_score,
            "weights": best_general,
        }, indent=2))
        print(f"Evaluation log written to {EVAL_HISTORY_PATH}")
    elif args.save:
        print("No improvement found; weights not updated.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
reports/build_report.py — Per-race explainable prediction report.

For each meeting in data/predictions/, produces a self-contained dark-themed
HTML report at data/predictions/<tag>_report.html and a structured JSON at
data/predictions/<tag>_report.json.

Per horse we show:
  - Raw race-card data: draw, weight, rating, gear, last6, jockey, trainer
  - HKJC supplementary stats: jockey season win-rate, trainer season win-rate,
    "when-on-favourite" strike rates, recent meeting challenge points
  - Lifetime stats from horses.csv (starts / wins / places / top4 / recent form)
  - The 8 model score components with raw inputs + contribution to composite
  - Natural-language one-line "why this rank" summary

Usage:
    python reports/build_report.py                          # all stored-pred meetings
    python reports/build_report.py --tag 2026-04-29_HV      # one meeting
    python reports/build_report.py --format html            # html only (default: both)
    python reports/build_report.py --format json
    python reports/build_report.py --out data/my_reports     # custom output dir
"""

import argparse
import json
import re
import sys
from html import escape
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PRED_DIR = ROOT / "data" / "predictions"
RESULTS_DIR = ROOT / "data" / "results"
HORSES_CSV = ROOT / "data" / "horses.csv"
DEFAULT_OUT = PRED_DIR

sys.path.insert(0, str(ROOT))


# ── Clean HKJC sheet loaders ────────────────────────────────────────────────

def _clean_rank_sheet(df: pd.DataFrame, name_col: str) -> pd.DataFrame:
    """JockeyRanking / TrainerRanking have a junk header row (rank=0, 'Win', 2,3,4,5,0)
       where win_rate is 0 and the rest are weird. Drop it then normalise."""
    if df.empty:
        return df
    df = df.copy()
    # The first row is the column-label-as-data row from HKJC. Detect by
    # looking for the row where the name column equals "Win" (jockey) or "Trainer".
    mask = df[name_col].astype(str).str.strip().isin({"Win", "Trainer", "Jockey", ""})
    df = df[~mask].reset_index(drop=True)
    # Coerce numeric
    for c in df.columns:
        if c == name_col: continue
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_all_sheets(tag: str) -> dict:
    """Return every HKJC sheet for one meeting as cleaned DataFrames."""
    raw_p = RAW_DIR / f"{tag}.xlsx"
    out = {"raw_path": str(raw_p)}
    if not raw_p.exists():
        return out
    with pd.ExcelFile(raw_p) as x:
        for sheet in x.sheet_names:
            df = pd.read_excel(x, sheet_name=sheet)
            if sheet in ("JockeyRanking", "TrainerRanking"):
                name_col = "jockey" if sheet == "JockeyRanking" else "trainer"
                df = _clean_rank_sheet(df, name_col)
            out[sheet] = df
    return out


def lookup_person_stat(df: pd.DataFrame, name: str, name_col: str) -> dict | None:
    """Find a person in a JockeyRanking/TrainerRanking sheet, return their
       season stats as a dict, or None if not found."""
    if df is None or df.empty: return None
    sub = df[df[name_col].astype(str).str.strip() == str(name).strip()]
    if sub.empty: return None
    row = sub.iloc[0]
    return {c: (None if pd.isna(v) else v) for c, v in row.to_dict().items()}


def lookup_challenge(df: pd.DataFrame, name: str, name_col: str) -> dict | None:
    """JockeyChallenge / TrainerChallenge — 'name' column is actually an integer rank,
       so we don't try to match by name. We expose the full top-N table for context."""
    if df is None or df.empty: return None
    return df.head(10).to_dict(orient="records")


def lookup_favourite(df: pd.DataFrame, name: str, name_col: str) -> dict | None:
    """JockeyFavourite / TrainerFavourite — strike rate when riding the favourite."""
    if df is None or df.empty: return None
    sub = df[df[name_col].astype(str).str.strip() == str(name).strip()]
    if sub.empty: return None
    row = sub.iloc[0]
    return {c: (None if pd.isna(v) else v) for c, v in row.to_dict().items()}


def load_horses_lookup() -> pd.DataFrame:
    hc = pd.read_csv(HORSES_CSV)
    return hc


def lookup_horse_history(hc: pd.DataFrame, horse_id: str,
                         venue: str, distance: int, surface: str) -> dict | None:
    """Look up the horse's stats for this (venue, distance, surface) slice.
       Falls back to closest match by distance if exact slice missing."""
    if hc is None or hc.empty: return None
    sub = hc[(hc["horse_id"] == horse_id)
             & (hc["venue"] == venue)
             & (hc["distance_m"] == distance)
             & (hc["surface"] == surface)]
    if sub.empty:
        # fallback: same horse, any distance/surface
        sub = hc[hc["horse_id"] == horse_id]
    if sub.empty: return None
    # Pick the slice with the most starts (most signal)
    sub = sub.sort_values("starts", ascending=False)
    row = sub.iloc[0]
    return {c: (None if pd.isna(v) else v) for c, v in row.to_dict().items()}


def load_actual_results(tag: str) -> dict:
    """{race_no: [(horse_no, pos, win_odds, time, ...), ...]} sorted by pos."""
    res_p = RESULTS_DIR / f"{tag}.xlsx"
    if not res_p.exists(): return {}
    df = pd.read_excel(res_p, sheet_name="Results")
    df["horse_no"] = df["horse_no"].fillna(0).astype(int).astype(str)
    df["race_no"] = df["race_no"].astype(int)
    df["pos"] = pd.to_numeric(df["pos"], errors="coerce")
    df.loc[df["pos"] <= 0, "pos"] = None
    out = {}
    for race_no, g in df.groupby("race_no"):
        rows = g.sort_values("pos").to_dict(orient="records")
        out[int(race_no)] = rows
    return out


# ── Per-race explainability ──────────────────────────────────────────────────

SCORE_DESCRIPTIONS = {
    "s_form":    ("Form",      "Recency-weighted avg of last-6 finishing positions"),
    "s_rating":  ("Rating",    "HKJC official rating, scaled 1-10 within the field"),
    "s_market":  ("Market",    "Win odds signal — currently a placeholder (no live odds)"),
    "s_draw":    ("Draw",      "Bias-adjusted draw advantage at this venue/distance"),
    "s_jockey":  ("Jockey",    "Jockey's season win-rate (HKJC ranking)"),
    "s_trainer": ("Trainer",   "Trainer's season win-rate (HKJC ranking)"),
    "s_h2h":     ("H2H",       "Head-to-head record vs other horses in today's field"),
    "s_weight":  ("Weight",    "Weight carried relative to the field (lighter = better)"),
}

SCORE_WEIGHTS = {
    "form": 0.30, "rating": 0.15, "market": 0.10, "draw": 0.15,
    "jockey": 0.10, "trainer": 0.08, "h2h": 0.10, "weight": 0.02,
}


def explain_score(score_name: str, score_value: float, row: dict, race_meta: dict,
                  jockey_stat: dict | None, trainer_stat: dict | None,
                  horse_hist: dict | None) -> dict:
    """Build a {label, value, formula, raw_inputs, narrative} dict for one score."""
    raw = {}
    formula = ""
    narrative = ""
    if score_name == "s_form":
        l6 = row.get("last6_runs", "")
        raw = {"last6_runs": l6}
        if l6 and "/" in l6:
            positions = [int(re.sub(r"\D", "", p) or "99") for p in l6.split("/")]
            positions = [min(p, 20) for p in positions[-6:]]
            wts = list(range(1, len(positions) + 1))
            score_map = {1:10,2:9,3:8,4:6,5:5,6:4,7:3,8:2,9:2,10:1,11:1,12:1}
            scored = [score_map.get(v, 1) * w for v, w in zip(positions, wts)]
            avg = sum(scored) / sum(wts) if wts else 0
            formula = f"avg over last-6 of pos→score map, weighted 1..{len(positions)}"
            narrative = (f"Last 6: {'/'.join(str(p) for p in positions)} "
                         f"→ weighted avg {avg:.2f}/10")
        else:
            formula = "default 5.0 (no last-6 data)"
            narrative = "No recent form data; defaulted to 5.0"
    elif score_name == "s_rating":
        raw = {"rating": row.get("rating")}
        formula = "1 + 9 × (rating - field_min) / (field_max - field_min)"
        r = row.get("rating", "?")
        narrative = f"Rating {r} in field range [{race_meta.get('rating_min','?')}, {race_meta.get('rating_max','?')}]"
    elif score_name == "s_market":
        raw = {"odds": row.get("odds")}
        formula = "10 - 9 × (odds - field_min) / (field_max - field_min)"
        narrative = ("Odds signal not yet wired up — all horses default to 20.0. "
                     "Wire live_odds.py into predict.py to enable this signal.")
    elif score_name == "s_draw":
        raw = {"draw": row.get("draw"), "venue": race_meta.get("venue"),
               "distance": race_meta.get("distance"), "surface": race_meta.get("surface")}
        formula = "clamp(draw_bias × 7, 1, 10)  (bias from DRAW_BIAS table)"
        narrative = (f"Draw {row.get('draw')} at {race_meta.get('venue')} "
                     f"{race_meta.get('distance')}m {race_meta.get('surface')} — "
                     f"bias multiplier {row.get('_draw_bias','?')}")
    elif score_name == "s_jockey":
        raw = {"jockey": row.get("jockey")}
        if jockey_stat:
            wr = jockey_stat.get("win_rate")
            raw["season_win_rate_%"] = wr
            raw["season_rides"] = jockey_stat.get("rides")
            raw["season_wins"]   = jockey_stat.get("wins")
            formula = "1 + 9 × min(win_rate / 0.25, 1)"
            narrative = f"Season: {wr}% win / {jockey_stat.get('rides')} rides"
        else:
            formula = "fallback to JOCKEY_SCORES lookup (default 6.5)"
            narrative = "Jockey not in this meeting's ranking sheet (uses historical fallback)"
    elif score_name == "s_trainer":
        raw = {"trainer": row.get("trainer")}
        if trainer_stat:
            wr = trainer_stat.get("win_rate")
            raw["season_win_rate_%"] = wr
            raw["season_rides"] = trainer_stat.get("rides")
            raw["season_wins"]   = trainer_stat.get("wins")
            formula = "1 + 9 × min(win_rate / 0.22, 1)"
            narrative = f"Season: {wr}% win / {trainer_stat.get('rides')} rides"
        else:
            formula = "fallback to TRAINER_SCORES lookup (default 6.0)"
            narrative = "Trainer not in this meeting's ranking sheet (uses historical fallback)"
    elif score_name == "s_h2h":
        raw = {"horse_id": row.get("horse_id")}
        formula = "(wins_vs_field / total_meetings) × 10"
        narrative = ("Head-to-head vs other horses in this field — uses data/cache/horse_*.json. "
                     "Empty cache returns 5.0 (neutral).")
    elif score_name == "s_weight":
        raw = {"weight_lbs": row.get("weight_lbs")}
        formula = "1 + 9 × (field_max_weight - weight) / (field_max_weight - field_min_weight)"
        narrative = (f"Weight {row.get('weight_lbs')} lbs in field "
                     f"[{race_meta.get('weight_min','?')}, {race_meta.get('weight_max','?')}]")
    return {
        "score_name":  score_name,
        "label":       SCORE_DESCRIPTIONS.get(score_name, (score_name, ""))[0],
        "description": SCORE_DESCRIPTIONS.get(score_name, (score_name, ""))[1],
        "value":       float(score_value),
        "weight":      SCORE_WEIGHTS.get(score_name.replace("s_", ""), 0),
        "contribution": float(score_value) * SCORE_WEIGHTS.get(score_name.replace("s_", ""), 0),
        "raw_inputs":  raw,
        "formula":     formula,
        "narrative":   narrative,
    }


def build_race_report(race: dict, race_no: int, sheets: dict,
                      horses_lookup: pd.DataFrame,
                      actual_results: dict,
                      venue: str) -> dict:
    """Build the structured report dict for one race."""
    # venue is passed in (don't try to extract from raw_path string)
    # Distance + surface come from the raw RaceCard row context
    rc = sheets.get("RaceCard")
    race_rc = None
    if rc is not None and not rc.empty and (rc["race_no"] == race_no).any():
        race_rc = rc[rc["race_no"] == race_no].iloc[0]
    try:
        distance = int(race_rc["distance"]) if race_rc is not None and pd.notna(race_rc.get("distance")) else 0
    except (ValueError, TypeError):
        distance = 0
    surface  = str(race_rc.get("surface", "Turf")) if race_rc is not None else "Turf"
    if surface not in ("Turf", "AWT"):
        surface = "Turf"

    horses = race["horses"]
    field_size = len(horses)
    rating_min = min((h.get("rating", 50) or 50) for h in horses)
    rating_max = max((h.get("rating", 50) or 50) for h in horses)
    weight_min = min((h.get("weight_lbs", 126) or 126) for h in horses)
    weight_max = max((h.get("weight_lbs", 126) or 126) for h in horses)
    odds_min   = min((h.get("win_odds", 20) or 20) for h in horses)
    odds_max   = max((h.get("win_odds", 20) or 20) for h in horses)

    race_meta = {
        "venue": venue, "distance": distance, "surface": surface,
        "field_size": field_size,
        "rating_min": rating_min, "rating_max": rating_max,
        "weight_min": weight_min, "weight_max": weight_max,
        "odds_min": odds_min,   "odds_max": odds_max,
    }

    # Jockey / trainer stat lookups
    jky_df = sheets.get("JockeyRanking")
    trn_df = sheets.get("TrainerRanking")
    jf_df  = sheets.get("JockeyFavourite")
    tf_df  = sheets.get("TrainerFavourite")
    jc_df  = sheets.get("JockeyChallenge")
    tc_df  = sheets.get("TrainerChallenge")

    # Build per-horse breakdown
    horses_out = []
    # Need to sort by composite score to assign rank — read from stored predictions
    # OR compute from race dict if 'composite' key exists
    horses_sorted = sorted(horses, key=lambda h: -(h.get("composite", 0) or 0))
    for rank, h in enumerate(horses_sorted, 1):
        jockey_stat  = lookup_person_stat(jky_df, h.get("jockey", ""), "jockey")
        trainer_stat = lookup_person_stat(trn_df, h.get("trainer", ""), "trainer")
        jf_stat      = lookup_favourite(jf_df, h.get("jockey", ""), "jockey")
        tf_stat      = lookup_favourite(tf_df, h.get("trainer", ""), "trainer")
        horse_hist   = lookup_horse_history(horses_lookup, h.get("horse_id", ""),
                                            venue, distance, surface)
        h2 = dict(h)
        # Compute per-score explanations
        score_expl = {}
        for sn in ("s_form","s_rating","s_market","s_draw",
                   "s_jockey","s_trainer","s_h2h","s_weight"):
            score_expl[sn] = explain_score(
                sn, h.get(sn, 0) or 0, h, race_meta,
                jockey_stat, trainer_stat, horse_hist,
            )

        # Look up actual result if present
        actual_pos = None
        actual_odds = None
        if race_no in actual_results:
            for ar in actual_results[race_no]:
                if str(ar.get("horse_no")) == str(h.get("horse_no")):
                    actual_pos  = ar.get("pos")
                    actual_odds = ar.get("win_odds")
                    break

        # Build a one-line narrative summary
        top_drivers = sorted(score_expl.items(), key=lambda kv: -kv[1]["contribution"])[:3]
        drivers_str = ", ".join(f"{v['label']}={v['value']:.1f}" for _, v in top_drivers)
        narrative = f"#{rank} {h.get('horse_name','')} — top drivers: {drivers_str}"

        horses_out.append({
            "rank": rank,
            "horse_no":   str(h.get("horse_no", "")),
            "horse_id":   str(h.get("horse_id", "")),
            "horse_name": str(h.get("horse_name", "")),
            "jockey":     str(h.get("jockey", "")),
            "trainer":    str(h.get("trainer", "")),
            "draw":       int(h.get("draw", 0) or 0),
            "weight_lbs": int(h.get("weight_lbs", 126) or 126),
            "rating":     int(h.get("rating", 0) or 0),
            "gear":       str(h.get("gear", "") or ""),
            "last6_runs": str(h.get("last6_runs", "") or ""),
            "composite":  round(float(h.get("composite", 0) or 0), 3),
            "win_prob_%": round(float(h.get("win_prob", 0) or 0), 1),
            "calc_odds":  round(float(h.get("calc_odds", 0) or 0), 1),
            "scores":     score_expl,
            "jockey_stat":    jockey_stat,
            "trainer_stat":   trainer_stat,
            "jockey_fav_stat":jf_stat,
            "trainer_fav_stat":tf_stat,
            "horse_history":  horse_hist,
            "actual_pos":     actual_pos,
            "actual_win_odds":actual_odds,
            "narrative":      narrative,
        })

    return {
        "race_no":   race_no,
        "venue":     venue,
        "distance":  distance,
        "surface":   surface,
        "field_size":field_size,
        "race_meta": race_meta,
        "jockey_challenge_top10": lookup_challenge(jc_df, "", "jockey"),
        "trainer_challenge_top10":lookup_challenge(tc_df, "", "trainer"),
        "horses":    horses_out,
    }


# ── HTML rendering ──────────────────────────────────────────────────────────

def _bar(pct: float, color: str = "#3b82f6") -> str:
    return (f'<div style="background:#1e293b;border-radius:4px;height:6px;width:100%;">'
            f'<div style="background:{color};height:6px;width:{max(0,min(100,pct))}%;border-radius:4px;"></div>'
            f'</div>')


def render_race_html(race: dict) -> str:
    meta = race["race_meta"]
    actual_label = ""
    if any(h.get("actual_pos") for h in race["horses"]):
        winner = next((h for h in race["horses"] if h.get("actual_pos") == 1), None)
        if winner:
            actual_label = f'<span style="color:#22c55e;font-weight:600;">actual winner: #{winner["horse_no"]} {escape(winner["horse_name"])}</span>'

    parts = [f"""
<section class="race">
  <header class="race-header">
    <h2>R{race['race_no']} — {meta['distance']}m {meta['surface']} — {meta['venue']}</h2>
    <div class="meta-row">
      <span>field size: {meta['field_size']}</span>
      <span>rating range: {meta['rating_min']}–{meta['rating_max']}</span>
      <span>weight range: {meta['weight_min']}–{meta['weight_max']} lbs</span>
      <span>odds range: {meta['odds_min']}–{meta['odds_max']}</span>
      {actual_label}
    </div>
  </header>
  <div class="horse-grid">
"""]

    for h in race["horses"]:
        rank_class = "p1" if h["rank"] == 1 else ("p2" if h["rank"] == 2 else
                                                    ("p3" if h["rank"] == 3 else ""))
        # actual-position badge
        actual_badge = ""
        ap_raw = h.get("actual_pos")
        if ap_raw is not None and pd.notna(ap_raw):
            ap = int(ap_raw)
            if ap <= 14:
                suffix = "st" if ap == 1 else ("nd" if ap == 2 else ("rd" if ap == 3 else "th"))
                actual_badge = f'<span class="actual actual-{min(ap,4)}">finished {ap}{suffix}</span>'
            else:
                actual_badge = f'<span class="actual actual-bad">finished {ap}</span>'

        # Build the 8 score rows
        score_rows = []
        for sn, sd in h["scores"].items():
            v = sd["value"]
            color = "#22c55e" if v >= 7 else ("#eab308" if v >= 5 else "#ef4444")
            score_rows.append(f"""
            <div class="score-row" title="{escape(sd['narrative'])}">
              <div class="score-label">{sd['label']}</div>
              <div class="score-val" style="color:{color};">{v:.2f}</div>
              <div class="score-bar">{_bar(v*10, color)}</div>
              <div class="score-contrib">×{sd['weight']:.2f} = <b>{sd['contribution']:.3f}</b></div>
              <div class="score-narr">{escape(sd['narrative'])}</div>
            </div>""")

        # Lifetime stats summary
        lh = h.get("horse_history") or {}
        lh_html = '<span class="muted">no lifetime slice data</span>'
        if lh and lh.get("starts"):
            lh_html = (f"<b>{int(lh['starts'])}</b> starts / "
                       f"<b>{int(lh['wins'])}</b> wins / "
                       f"<b>{int(lh['places'])}</b> places "
                       f"({float(lh.get('top4_rate',0))*100:.0f}% top-4)")
            if pd.notna(lh.get("recent_form_l5")):
                lh_html += f"  •  recent L5 avg: <b>{float(lh['recent_form_l5']):.2f}</b>"
            if pd.notna(lh.get("days_since_last_run")):
                lh_html += f"  •  last run: <b>{int(lh['days_since_last_run'])}</b> days ago"

        # Jockey / trainer season stats
        js = h.get("jockey_stat") or {}
        ts = h.get("trainer_stat") or {}
        js_html = (f"{js.get('win_rate', '?')}% / {js.get('rides', '?')} rides / "
                   f"{js.get('wins', '?')} wins") if js else "—"
        ts_html = (f"{ts.get('win_rate', '?')}% / {ts.get('rides', '?')} rides / "
                   f"{ts.get('wins', '?')} wins") if ts else "—"

        # When-on-favourite
        jf = h.get("jockey_fav_stat") or {}
        tf = h.get("trainer_fav_stat") or {}
        jf_html = (f"win% {jf.get('win_pct','?')}, place% {jf.get('place_pct','?')}"
                   f" ({jf.get('rides','?')} fav rides)") if jf else "—"
        tf_html = (f"win% {tf.get('win_pct','?')}, place% {tf.get('place_pct','?')}"
                   f" ({tf.get('rides','?')} fav runs)") if tf else "—"

        parts.append(f"""
    <article class="horse {rank_class}">
      <div class="horse-head">
        <div class="rank-badge">{h['rank']}</div>
        <div class="horse-name">
          <span class="num">#{escape(h['horse_no'])}</span>
          <span class="name">{escape(h['horse_name'])}</span>
          <span class="horse-id">{escape(h['horse_id'])}</span>
        </div>
        <div class="horse-summary">
          <div class="composite">{h['composite']:.2f}</div>
          <div class="prob">{h['win_prob_%']:.1f}%</div>
          <div class="odds">{h['calc_odds']:.1f}</div>
        </div>
        {actual_badge}
      </div>
      <div class="horse-card">
        <div><span class="k">draw</span><span class="v">{h['draw']}</span></div>
        <div><span class="k">weight</span><span class="v">{h['weight_lbs']} lbs</span></div>
        <div><span class="k">rating</span><span class="v">{h['rating']}</span></div>
        <div><span class="k">gear</span><span class="v">{escape(h['gear']) or '—'}</span></div>
        <div class="wide"><span class="k">last6</span><span class="v">{escape(h['last6_runs']) or '—'}</span></div>
        <div class="wide"><span class="k">jockey</span><span class="v">{escape(h['jockey'])} — {js_html}</span></div>
        <div class="wide"><span class="k">jockey on fav</span><span class="v">{jf_html}</span></div>
        <div class="wide"><span class="k">trainer</span><span class="v">{escape(h['trainer'])} — {ts_html}</span></div>
        <div class="wide"><span class="k">trainer on fav</span><span class="v">{tf_html}</span></div>
        <div class="wide lifetime"><span class="k">lifetime @ this slice</span><span class="v">{lh_html}</span></div>
      </div>
      <details class="score-details">
        <summary>score breakdown — 8 components</summary>
        <div class="scores">
          {''.join(score_rows)}
        </div>
      </details>
      <div class="narrative">{escape(h['narrative'])}</div>
    </article>
""")
    parts.append("  </div></section>")
    return "".join(parts)


def render_meeting_html(meeting: dict) -> str:
    title = f"{meeting['tag']} — HKJC prediction report"
    race_blocks = "\n".join(render_race_html(r) for r in meeting["races"])

    # Headline summary at top
    total_horses = sum(len(r["horses"]) for r in meeting["races"])
    actual_winners = [h for r in meeting["races"] for h in r["horses"] if h.get("actual_pos") == 1]
    p1_hits = 0
    top4_hits = 0
    for r in meeting["races"]:
        for h in r["horses"]:
            ap_raw = h.get("actual_pos")
            if ap_raw is None or pd.isna(ap_raw):
                continue
            ap = int(ap_raw)
            if h["rank"] == 1 and ap <= 4:
                p1_hits += 1
            if ap <= 4:
                top4_hits += 1
    summary_html = ""
    if actual_winners:
        summary_html = f"""
    <div class="summary">
      <div class="summary-item"><b>{len(meeting['races'])}</b> races</div>
      <div class="summary-item"><b>{total_horses}</b> horses scored</div>
      <div class="summary-item"><b>{p1_hits}/{len(meeting['races'])}</b> P1 winners hit (top-4)</div>
      <div class="summary-item"><b>{top4_hits}/{total_horses}</b> top-4 picks landed</div>
    </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{escape(title)}</title>
<style>
  :root {{
    --bg:#0f172a; --panel:#1e293b; --panel-2:#273449; --border:#334155;
    --text:#e2e8f0; --muted:#94a3b8; --accent:#3b82f6;
    --green:#22c55e; --yellow:#eab308; --red:#ef4444; --purple:#a855f7;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
                 "Microsoft JhengHei", Arial, sans-serif;
    font-size: 14px; line-height: 1.45;
  }}
  h1 {{ margin: 0 0 4px; font-size: 22px; color: #f8fafc; }}
  h2 {{ margin: 0 0 12px; font-size: 18px; color: #f1f5f9; }}
  .subtitle {{ color: var(--muted); margin-bottom: 24px; }}
  .summary {{
    display: flex; gap: 24px; flex-wrap: wrap;
    background: var(--panel); padding: 16px 20px; border-radius: 8px;
    border: 1px solid var(--border); margin-bottom: 24px;
  }}
  .summary-item {{ font-size: 13px; color: var(--muted); }}
  .summary-item b {{ color: var(--text); font-size: 16px; margin-right: 4px; }}
  section.race {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; margin-bottom: 24px;
  }}
  .race-header {{ margin-bottom: 16px; padding-bottom: 12px;
                  border-bottom: 1px solid var(--border); }}
  .meta-row {{ display: flex; flex-wrap: wrap; gap: 18px; color: var(--muted); font-size: 13px; }}
  .horse-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
                  gap: 12px; }}
  article.horse {{
    background: var(--panel-2); border: 1px solid var(--border);
    border-radius: 6px; padding: 12px 14px;
  }}
  article.horse.p1 {{ border-left: 4px solid var(--green); }}
  article.horse.p2 {{ border-left: 4px solid var(--yellow); }}
  article.horse.p3 {{ border-left: 4px solid #f97316; }}
  .horse-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
  .rank-badge {{
    background: var(--border); color: var(--text); font-weight: 700;
    width: 26px; height: 26px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; font-size: 13px;
  }}
  article.p1 .rank-badge {{ background: var(--green); color: #052e16; }}
  article.p2 .rank-badge {{ background: var(--yellow); color: #422006; }}
  article.p3 .rank-badge {{ background: #f97316; color: #431407; }}
  .horse-name {{ flex: 1; display: flex; align-items: baseline; gap: 8px; min-width: 0; }}
  .horse-name .num {{ color: var(--muted); font-weight: 600; }}
  .horse-name .name {{ font-weight: 600; font-size: 15px; color: #f8fafc;
                       overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .horse-name .horse-id {{ color: var(--muted); font-size: 12px; font-family: ui-monospace,
                            "SF Mono", Consolas, monospace; }}
  .horse-summary {{ display: flex; gap: 12px; align-items: baseline; }}
  .horse-summary .composite {{ font-size: 16px; font-weight: 700; color: var(--accent); }}
  .horse-summary .prob {{ color: var(--muted); font-size: 12px; }}
  .horse-summary .odds {{ color: var(--muted); font-size: 12px; }}
  .actual {{ padding: 2px 8px; border-radius: 4px; font-size: 11px;
             background: var(--green); color: #052e16; font-weight: 600; }}
  .actual-bad {{ background: var(--border); color: var(--muted); }}
  .horse-card {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px 10px;
    margin: 8px 0; font-size: 12px;
  }}
  .horse-card > div {{ display: flex; flex-direction: column; }}
  .horse-card .wide {{ grid-column: span 4; }}
  .horse-card .k {{ color: var(--muted); font-size: 11px; text-transform: uppercase;
                     letter-spacing: 0.5px; }}
  .horse-card .v {{ color: var(--text); }}
  .lifetime .v {{ color: #cbd5e1; font-size: 13px; }}
  .muted {{ color: var(--muted); font-style: italic; }}
  details.score-details {{ margin-top: 8px; }}
  details.score-details summary {{
    cursor: pointer; color: var(--accent); font-size: 12px;
    padding: 4px 0;
  }}
  .scores {{ padding: 8px 0 0; }}
  .score-row {{
    display: grid; grid-template-columns: 60px 50px 1fr 130px;
    gap: 8px; align-items: center; padding: 4px 0; font-size: 12px;
  }}
  .score-label {{ color: var(--muted); font-weight: 500; }}
  .score-val {{ font-weight: 700; text-align: right; }}
  .score-contrib {{ color: var(--muted); text-align: right; font-family: ui-monospace,
                     "SF Mono", Consolas, monospace; }}
  .score-narr {{ grid-column: 1 / -1; color: var(--muted); font-size: 11px;
                 margin-top: -4px; padding-left: 60px; }}
  .narrative {{ margin-top: 8px; padding-top: 8px;
                border-top: 1px dashed var(--border); font-size: 12px;
                color: var(--muted); font-style: italic; }}
</style>
</head>
<body>
<h1>{escape(title)}</h1>
<div class="subtitle">explainable per-race prediction · {len(meeting['races'])} races ·
{sum(len(r['horses']) for r in meeting['races'])} horses</div>
{summary_html}
{race_blocks}
</body>
</html>"""


# ── Per-meeting report builder ──────────────────────────────────────────────

def build_meeting_report(tag: str, horses_lookup: pd.DataFrame) -> dict | None:
    """Score one meeting from raw XLSX and return a structured report dict."""
    raw_p = RAW_DIR / f"{tag}.xlsx"
    if not raw_p.exists(): return None

    # Reproduce the same setup predict.py uses, then run score_field
    from predict import score_field
    from config import WEIGHTS_GENERAL, WEIGHTS_CLASSIC, CLASSIC_RACE_NAMES
    from eval_weights import build_stats, build_races, build_history_cache

    sheets = load_all_sheets(tag)
    jky, trn = build_stats(raw_p)
    races = build_races(raw_p)
    hids = {h["horse_id"] for r in races for h in r["horses"] if h.get("horse_id")}
    hist = build_history_cache(hids)
    venue = tag.split("_")[-1]

    race_reports = []
    for race in races:
        is_classic = any(name in (race.get("race_name") or "").upper()
                         for name in CLASSIC_RACE_NAMES)
        weights = WEIGHTS_CLASSIC if is_classic else WEIGHTS_GENERAL
        scored = score_field(race, venue, jky, trn, hist, weights)
        if scored.empty:
            continue
        # merge scored back into race['horses'] so composite / s_* fields are present
        # score_field output preserves horse order, so zip is safe
        for h_orig, (_, row) in zip(race["horses"], scored.iterrows()):
            h_orig["composite"] = float(row["composite"])
            h_orig["win_prob"]  = float(row["win_prob"])
            h_orig["calc_odds"] = float(row["calc_odds"])
            for sn in ("s_form","s_rating","s_market","s_draw",
                       "s_jockey","s_trainer","s_h2h","s_weight"):
                h_orig[sn] = float(row[sn])
        actual = load_actual_results(tag)
        race_reports.append(build_race_report(race, int(race["race_no"]),
                                              sheets, horses_lookup, actual, venue))

    return {
        "tag":        tag,
        "venue":      venue,
        "races":      race_reports,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Per-race explainable prediction reports.")
    ap.add_argument("--tag",    help="Specific meeting tag (default: all with stored predictions)")
    ap.add_argument("--format", choices=["html", "json", "both"], default="both")
    ap.add_argument("--out",    default=str(DEFAULT_OUT),
                    help="Output directory (default: data/predictions)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    horses_lookup = load_horses_lookup()

    if args.tag:
        tags = [args.tag]
    else:
        tags = sorted(p.stem for p in PRED_DIR.glob("*.xlsx"))
    print(f"Building reports for {len(tags)} meetings → {out_dir}", flush=True)

    ok = 0
    import traceback
    for tag in tags:
        try:
            report = build_meeting_report(tag, horses_lookup)
            if report is None:
                print(f"  ✗ {tag}  (raw XLSX missing)")
                continue
            if args.format in ("html", "both"):
                html_path = out_dir / f"{tag}_report.html"
                html_path.write_text(render_meeting_html(report), encoding="utf-8")
            if args.format in ("json", "both"):
                json_path = out_dir / f"{tag}_report.json"
                # Convert any Timestamp / non-serialisable to str
                def clean(o):
                    try:
                        json.dumps(o); return o
                    except TypeError:
                        return str(o)
                json_path.write_text(json.dumps(report, indent=2, default=clean))
            print(f"  ✓ {tag}")
            ok += 1
        except Exception as exc:
            print(f"  ✗ {tag}  ERROR: {exc}")
            traceback.print_exc()
    print(f"\nDone: {ok}/{len(tags)} reports written.")


if __name__ == "__main__":
    main()

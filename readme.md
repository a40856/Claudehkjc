# HKJC Horse Prediction System

A hand-tuned rule-based model + ML experiments for predicting Hong Kong Jockey
Club horse races. Built around a small set of weighted features (form, rating,
draw, jockey, trainer, etc.) with cross-validated reporting and explainable
per-race HTML reports.

## Project status (as of 2026-06-19)

- **Production model**: rule-based scorer with 8 weighted features → softmax
  win probability. 24-meeting back-test shows **47.19% top-4 hit rate (per pick)**,
  **94.9% per-race top-4 hit rate**, **+6.22% place-bet ROI** vs −34.42% on win bets.
- **ML experiments**: LightGBM LambdaRank ranker tested with honest
  leave-one-meeting-out CV — performs ≈ the rule-based model (no significant
  gain). Model and CV results kept for future reference.
- **Live odds**: NOT wired up. `raw.win_odds` is a placeholder (=20). This is
  the single biggest unrealised improvement (naive favourites baseline hits
  56.88% top-4 — beating both models).

## Folder layout

```
Claudehkjc/
├── predict.py              ← production: generate predictions for a race day
├── review.py               ← production: fetch actual results, fill hit columns
├── scheduler.py            ← production: cron driver (check-and-run / check-and-review)
├── live_odds.py            ← production: fetch live HKJC odds (not yet wired into predict.py)
├── eval_weights.py         ← production: weekly weight tuning (random search)
├── v85_scoring.py          ← production: alternative 100-pt V85 scoring system
├── backfill_results.py     ← production: bulk-fetch any missing historical results
├── backtest_v85.py         ← production: V85 backtest
├── test_weights.py         ← tests for weights.json schema
├── config.py               ← shared constants (URLs, weights, draw bias, etc.)
├── weights.json            ← current model weights (WEIGHTS_GENERAL / CLASSIC)
├── index.html              ← live web dashboard (served from docs/)
├── requirements.txt
│
├── scripts/                ← one-off and utility scripts (no production traffic)
│   ├── aggregate_horse_history.py   ← walks all raw + results → data/horses.csv
│   ├── regen_predictions.py         ← re-score cached raw XLSX with current model
│   ├── model_accuracy_report.py     ← audit stored predictions vs actual results
│   ├── reports_build.py             ← per-race explainable HTML + JSON reports
│   ├── tune_weights.py              ← internal: random-search weight tuner
│   ├── lomo_cv.py                   ← internal: leave-one-meeting-out CV
│   └── ml_train.py                  ← internal: LightGBM ranker + LOMO CV
│
├── ml/                     ← (reserved) future ML experiment home
│
├── data/                   ← runtime data (live)
│   ├── raw/                  ← scraped race cards per meeting (input)
│   ├── results/              ← HKJC results per meeting (ground truth)
│   ├── predictions/          ← live predictions XLSX per meeting
│   ├── regen/                ← regenerated predictions (output of regen_predictions.py)
│   ├── cache/                ← raw HKJC HTML cache (internal)
│   ├── horses.csv            ← per-horse lifetime stats (built once, refreshed by aggregate)
│   └── backtest_log.csv      ← backtest history log
│
├── reports/                ← generated audit artifacts (committed for history)
│   ├── accuracy/             ← model_accuracy_report*.json (per run)
│   ├── weight_tuning/        ← weight_tuning_result.json + lomo_cv_result.json
│   ├── ml/                   ← LightGBM model + features + importance
│   └── prediction_reports/   ← per-meeting HTML + JSON explainable reports
│
├── docs/                   ← GitHub Pages web mirror (live dashboard data)
│   ├── index.html
│   └── data/...              ← web-friendly copies of predictions/results
│
├── .github/workflows/      ← scheduled CI
│   ├── predict-schedule.yml      ← runs predict.py nightly
│   ├── review-schedule.yml       ← runs review.py after each race day
│   ├── backfill-schedule.yml     ← weekly backfill of missing results
│   ├── weight-eval-schedule.yml  ← weekly weight tuning
│   └── hkjc.yml                  ← PR/build validation
│
├── _archive/               ← one-off debug scripts kept for reference (do not run)
│   ├── _bench_eval.py
│   ├── _run_eval.py
│   ├── _sanity.py
│   ├── inspect_html.py
│   ├── tmp_comm.html
│   ├── tmp_debug.py
│   └── tmp_racing_aspx.html
│
└── claude1/                ← unrelated legacy folder, left untouched
```

## Common workflows

### Daily / race day

```
predict.py --date 2026/06/21 --venue ST   # generate predictions for upcoming race
review.py  --date 2026/06/21 --venue ST   # next day: fetch actual results + fill hit Y/N
```

### Weekly audit + weight tuning

```bash
# 1. Rebuild per-horse history from latest results
python scripts/aggregate_horse_history.py

# 2. Re-score all meetings with current model (apples-to-apples)
python scripts/regen_predictions.py --all

# 3. Audit predictions vs actuals
python scripts/model_accuracy_report.py \
    --pred-dir data/regen \
    --out reports/accuracy/model_accuracy_report_$(date +%F).json

# 4. Try to beat the current weights via random search
python eval_weights.py --window 11 --iterations 500 --save
```

### ML experiments

```bash
python scripts/ml_train.py        # ~3 min: builds feature matrix, runs LOMO CV, saves LightGBM model
```

The trained model is **NOT** wired into predict.py yet (LOMO CV showed ≈ rule-based
performance, so this is for research only). See `reports/ml/lomo_cv_result.json`.

### Generate explainable per-race reports

```bash
python scripts/reports_build.py    # writes HTML + JSON per meeting → reports/prediction_reports/
python scripts/reports_build.py --tag 2026-04-29_HV   # one meeting only
```

Open any `reports/prediction_reports/*.html` in a browser to see:
- Per-horse breakdown of all 8 score components
- Each score's formula, raw inputs, and narrative
- Lifetime stats, jockey/trainer season stats, "when on favourite" strike rates
- Actual finishing position (when results are available)

## Scoring model

`score_field()` in `predict.py` computes 8 normalised scores (1-10 each):

| Score       | Weight | What it captures                                    |
|-------------|-------:|-----------------------------------------------------|
| `s_form`    | 0.30   | Recency-weighted avg of last-6 finishing positions  |
| `s_rating`  | 0.15   | HKJC official rating, scaled within the field       |
| `s_draw`    | 0.15   | Draw bias at this venue/distance (from DRAW_BIAS)   |
| `s_jockey`  | 0.10   | Jockey's season win-rate                            |
| `s_trainer` | 0.08   | Trainer's season win-rate                           |
| `s_h2h`     | 0.10   | Head-to-head record vs other horses in this field   |
| `s_market`  | 0.10   | **PLACEHOLDER** — win odds not wired up (always 20) |
| `s_weight`  | 0.02   | Weight carried relative to field                    |

`composite = Σ (weight_i × score_i)`, then softmax → `win_prob%` and `calc_odds`.

Weights live in `weights.json` and can be tuned with `eval_weights.py`.

## Known limitations

1. **No live odds** — biggest unrealised gain. Naive favourite baseline (56.88%
   top-4) beats both models because they all use a constant `win_odds=20`.
2. **`s_h2h` depends on cached history** (`data/cache/horse_*.json`) — if cache
   is empty, score defaults to neutral 5.0.
3. **Label data only covers 11 stored-prediction meetings** for the most
   recent CV. Earlier 13 meetings (May–Jun) have raw + results but no
   predictions on disk (regen_predictions.py fills this gap).
4. **Draw bias tables are hand-coded** in `config.py` based on historical
   observation, not learned.

## CI / scheduled work

GitHub Actions workflows under `.github/workflows/`:

- `predict-schedule.yml` — daily 09:00 HKT, runs `predict.py` if a race is
  scheduled today or tomorrow. Commits new predictions + web mirror to repo.
- `review-schedule.yml` — daily 23:00 + 00:15 HKT, runs `review.py` after the
  race meeting ends. Fills `actual_pos` + `hit` columns.
- `backfill-schedule.yml` — Mondays 02:00 HKT, bulk-fetches any missing
  historical results.
- `weight-eval-schedule.yml` — Sundays 02:00 HKT, random-search weight tuning
  over the last 8 meetings. Saves improved weights to `weights.json`.
- `hkjc.yml` — PR/push validation (syntax + config sanity).

## Quick reference: key files

| Path | When to look |
|---|---|
| `predict.py:score_field` | How the model scores a race |
| `predict.py:compute_form_score` | last6_runs string → 1-10 form score |
| `predict.py:compute_draw_score` | (stall, venue, distance) → 1-10 draw score |
| `config.py:DRAW_BIAS` | Hand-coded draw advantage tables |
| `config.py:WEIGHTS_GENERAL` | Default weights (overridden by weights.json) |
| `scripts/reports_build.py` | Build explainable HTML reports |
| `reports/accuracy/model_accuracy_report_*.json` | Latest audit results |
| `scripts/ml_train.py` | ML experiment + LOMO CV |

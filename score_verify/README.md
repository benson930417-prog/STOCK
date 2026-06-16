# score_verify

Independent cross-check for the ETF 綜合評分 history.

`verify_scores.py` re-derives the three score pillars (效率 / 不對稱 / 一致性)
from raw prices using **its own** SQL reader and metric maths — it imports nothing
from `src/` or `scripts/` — then compares against the app-produced
`data/etf_bench/score_history.csv`. If the app pipeline is correct, the numbers align.

## Run (on the server, where the sqlite lives)

```bash
cd /home/ubuntu/STOCK
python score_verify/verify_scores.py                         # pocket funds, latest date
python score_verify/verify_scores.py --date 2026-06-16
python score_verify/verify_scores.py --tickers 00981A,00988A
python score_verify/verify_scores.py --all                   # every stored fund on the date
```

To run locally, copy `data/etf_bench/etf_bench.sqlite` + `score_history.csv` down and
point at them with `--db` / `--csv`.

## What "aligned" proves

For the chosen date, each fund's recomputed `eff/asy/con` matches the stored value
(within ±0.05, i.e. CSV rounding). This independently confirms:

- the score is a **trailing-1-year** standing, not a single-day move (so a ±1% day is
  diluted across ~hundreds of days — the finding you observed);
- the percentile is computed **within asset class**;
- the 30-day listing gate and the NaN 不對稱 (no benchmark / R²<0.2) behave as expected.

Exit code 0 = aligned, 2 = mismatches found.

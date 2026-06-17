# ETF 綜合評分 — Scoring & Ranking Design

Authoritative spec for the ETF comparison score (the **綜合評分** in the ETF 比較 tab).
This describes the **final, agreed design** — implement and reason about the score from
this document.

---

## 1. What the score is

A **fair, regime-neutral quality score** (0–100) for each ETF, computed daily and stored,
that powers two views in the ETF 比較 tab:

- **綜合評分排名** — a ranked table of the selected ETFs.
- **綜合評分歷史** — an interactive line chart of each selected ETF's score over time.

Both views read **one shared standard** (the same store, the same formula), so the table
number and the line value for a fund are identical.

---

## 2. Design principles (the "why")

These principles justify every downstream choice.

1. **Direction-neutral only.** The score must not reward "being in a bull market" or
   "being defensive in a bear market." It only rewards metrics whose *meaning is
   independent of market direction*: risk-adjusted efficiency, up/down **asymmetry**
   (a ratio — direction cancels), and **consistency** vs a benchmark. Raw return, raw
   up-capture, and raw down-capture are **banned** as standalone signals because they are
   regime-contaminated.

2. **Relative, not absolute.** Scores are **percentile ranks within a peer group**, not
   absolute metric values. Reason: absolute return-level metrics (e.g. raw Sharpe) rise
   for *everyone* in a bull and fall for everyone in a bear, which silently rewards the
   luck of the sample period. Ranking within the same window cancels the common market
   level, leaving relative skill.

3. **Compare like with like — four asset-class baskets.** Funds are ranked **within their
   asset class**, never across. You cannot fairly compare a bond, a gold fund, and an
   equity fund on the same return metrics. The baskets are:

   | Basket | fund_type members |
   |---|---|
   | **股票 (equity)** | `passive_equity` + `active_equity` + `leveraged` (merged into one pool) |
   | **債券 (bond)** | `bond` |
   | **商品 (commodity)** | `commodity` |
   | **其他 (other)** | `other` |

   **Equity is deliberately one pool** (主動 + 被動 + 槓桿 together): they are all stock
   ETFs sharing one opportunity set, so e.g. `0050` (passive) and `00981A` (active) compete
   directly. Differences of *objective* (growth vs high-dividend) are expressed through
   **weights** (principle 4), not by splitting the pool — a high-dividend fund correctly
   scores low under a growth-tilted weighting, which is the right signal ("not a growth
   fund"), not unfairness.

4. **Objective lives in the weights.** The composite is a weighted average of the three
   pillars. A growth investor raises the 效率 / 不對稱 weights; an income/stability
   investor raises 一致性. Same data, different lens — the score is "fit for the chosen
   objective," not an objective-free verdict.

---

## 3. The score: three pillars

All three are direction-neutral. Each underlying metric is turned into a **0–100 percentile
within the asset-class basket**; a pillar is the **mean of its available metric
percentiles**; the composite is the **weighted mean of the available pillars**.

| Pillar | Metrics | Higher means |
|---|---|---|
| **效率 Efficiency** | Sortino, Calmar | more return per unit of *downside* risk (regime-neutral efficiency) |
| **不對稱 Asymmetry** *(UI label: 漲多跌少)* | up-capture − down-capture vs benchmark | keeps more of the upside while falling less — a ratio, so direction cancels |
| **一致性 Consistency** | batting average (↑), tracking error (↓), volatility (↓) | steadier, more reliable behaviour |

Rules:
- **Up/down is classified per trading day** by the benchmark's daily return sign
  (`b > 0` = up day, `b < 0` = down day). Up-capture and down-capture are each computed on
  their own side, so combining them is naturally 50/50 — the sample's bull/bear mix does
  not tilt it. (This is *not* the ZigZag bull/bear regime; that is only used by the
  separate, informational 市場區間績效 report.)
- **Asymmetry requires a benchmark and R² ≥ 0.20.** If a fund has no mapped benchmark or
  is too weakly correlated (typical for bonds / commodities / low-correlation ETFs), the
  asymmetry pillar is dropped and the composite **reweights to the remaining pillars**.
- A NaN pillar never zeroes the score — it is simply excluded from the weighted mean.

---

## 4. Methodology (exact)

- **Window:** trailing **1 year** ending at the as-of date. A fund younger than 1 year uses
  its full available history.
- **Price basis:** `adj_close` (total return; falls back to `close`). Independent of the
  chart's display toggle, so high-dividend funds are compared fairly and split artefacts are
  avoided.
- **Daily returns** are winsorised to ±50% to guard against split / bad-print spikes.
- **Per-fund listing gate:** a fund is scored only once it has **≥ 30 of its own trading
  days**. So a fund that listed later starts 30 trading days after *its own* listing, not
  after some global date.
- **Ranking pool:** all funds in the asset class with prices (the score itself is not
  liquidity-filtered; the tab's liquidity slider only filters which funds are *selectable*).
- **Benchmark per fund:** TW equity → `^TWII`; name/index containing NASDAQ/標普(S&P)/道瓊
  → `^IXIC`/`^GSPC`/`^DJI`; bond / commodity / other → none (asymmetry dropped).
- **Risk-free rate:** 0 (Sortino target = 0). Raise if a TW risk-free series is later added.
- **Confidence:** stored scores are the **raw** percentile (no shrinkage). Confidence is
  conveyed by `n_days` and a 信賴 label; the history chart offers an optional
  "依信賴度壓縮" toggle that pulls short-history scores toward the 50 midline. A single
  day is therefore **heavily diluted** across the ~1-year window — the score is a *standing*,
  not a daily P&L (see §6 verification).

---

## 5. Data store & pipeline

- **`step7_score.py`** computes the three pillar percentiles for **every eligible ETF**,
  ranked within its asset class, and writes one row per ETF per trading day to
  **`data/etf_bench/score_history.csv`** (long format):

  | column | meaning |
  |---|---|
  | `date`, `ticker`, `asset_class` | identity + basket |
  | `n_days` | trading days in the fund's window (drives 信賴) |
  | `eff`, `asy`, `con` | 效率 / 不對稱 / 一致性 percentile sub-scores (0–100; `asy` may be empty) |

  Only the **pillars** are stored, not the weighted composite — so the UI recombines them
  live with the weight sliders without any re-backfill.
- `--backfill` rebuilds history (default 1 year; `--years N` or `--start`); no args appends
  today. Idempotent (re-running a date replaces, never duplicates). A pre-fetch cache makes
  the full-universe backfill fast.
- Wired into the daily job (`update_and_notify.sh`, after step6), which also `git add`s the
  CSV so history accrues one point/day and syncs to checkouts.

---

## 6. How the UI uses it (`src/ui/etf_compare_tab.py`)

- **綜合評分排名 table:** reads the latest snapshot from the store for the selected funds,
  derives the composite via the weight sliders, and shows each fund's **percentile within
  its whole asset-class basket** (e.g. 同類排名 `9/209`). Same number as the history line.
- **綜合評分歷史 line:** the store's time series for the selected funds, clipped to the
  top date window, optional confidence fade/compression.
- Both are driven entirely by the **top selection** and the **shared weight sliders**.

---

## 7. Verification (method + conclusion)

The design was validated by **two independent methods** (the throwaway scripts have been
removed; the methodology and results are recorded here).

**Method A — pipeline reproduction.** An independent reimplementation of the pillar recipe
(own raw-SQL reader, own metric maths, no shared code) was run against the production
scorer on controlled data. **Result:** every pillar matched exactly (diff 0.00).
**Conclusion:** the stored scores faithfully implement the methodology in this document.

**Method B — independent triangulation.** Using *fresh Yahoo prices* and *textbook metrics*
(annualised Sharpe / total return / max drawdown — unrelated to the pillar system):
- **Dilution test:** recomputing each fund's trailing Sharpe with vs. without the last day
  moved it by ≈ 1/N (a few %). **Conclusion:** the score is a trailing *standing*; a single
  ±1% day barely moves it — which is why a strong fund stays high on a red day.
- **Ordering test:** Spearman ρ between the composite and independent Sharpe was positive
  and **agreed on the top fund** (00981A #1 by both). Mid-pack reordering is expected and
  correct, because the composite is three-dimensional (efficiency + asymmetry +
  consistency) whereas Sharpe is one-dimensional. **Conclusion:** the ranking reflects
  genuine, independently measurable standing, with intended multi-factor reordering.

---

## 8. Summary of final decisions

1. **Relative percentile**, not absolute (regime fairness).
2. **Four asset-class baskets**; **equity is one merged pool** (主動+被動+槓桿). Objective is
   expressed via **weights**, not by splitting the equity pool.
3. **Three direction-neutral pillars**: 效率 (Sortino+Calmar), 不對稱 (up−down capture,
   R²≥0.2 gate), 一致性 (batting + low tracking error + low volatility).
4. **Up/down classified per trading day** from the benchmark (not ZigZag regimes).
5. **Trailing 1-year** window, **adj_close**, **30-day** per-fund listing gate, raw stored
   percentile with confidence shown via `n_days`.
6. **One store, one standard** (`score_history.csv`) feeding both the ranking table and the
   history line, recorded daily by `step7_score.py`.

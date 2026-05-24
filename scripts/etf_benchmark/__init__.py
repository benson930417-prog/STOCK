"""ETF benchmark sub-system.

Local-first build. Goal: an absolutely-fair total-return database for every
TW-listed ETF since 2025-01-01, suitable for bull/bear regime analysis.

Pipeline (run scripts in order):

    step1_universe.py   ETF master list (TWSE + TPEx) -> data/etf_bench/universe.csv
    step2_schema.py     Initialise SQLite (prices, dividends, splits, tr_index, ...)
    step3_backfill.py   Yahoo Finance daily prices + corporate actions (TODO)
    step4_compute_tr.py Reconstruct adj_close ourselves from raw + events  (TODO)
    step5_verify.py     Cross-check against Yahoo adj_close + TWSE snapshots (TODO)
    step6_benchmark.py  TAIEX price + TAIEX total-return index (TODO)
"""

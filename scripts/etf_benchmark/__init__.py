"""ETF benchmark sub-system.

Local-first build. Goal: a practical benchmark database for every TW-listed ETF
since 2025-01-01, suitable for ETF comparison and regime analysis.

Pipeline (run scripts in order):

    step1_universe.py   ETF master list (TWSE + TPEx) -> data/etf_bench/universe.csv
    step2_schema.py     Initialise SQLite (prices, dividends, splits, regimes, ingest_log)
    step3_backfill.py   Yahoo Finance daily prices + corporate actions
    step4_regimes.py    Market regime tags
    step5_score.py      Fair-score history CSV
"""

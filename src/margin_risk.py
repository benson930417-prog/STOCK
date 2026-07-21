"""Shared read-only helpers for the Taiwan financing-risk tab and LINE card."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_PATH = ROOT_DIR / "data" / "margin_maintenance.csv"
LEGAL_CALL_REFERENCE = 130.0
LEGAL_CURE_REFERENCE = 166.0


@dataclass(frozen=True)
class MarginSnapshot:
    date: str
    estimate_pct: float
    change_1d: float | None
    change_5d: float | None
    change_20d: float | None
    percentile_1y: float | None
    financing_billion: float
    collateral_billion: float
    taiex_close: float | None
    distance_to_call: float
    status: str
    direction: str
    headline: str
    tone: str


def load_margin_cache(path: Path = DEFAULT_CACHE_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "date" not in df or "estimate_pct" not in df:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    numeric = [column for column in df.columns if column != "date"]
    for column in numeric:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return (
        df.dropna(subset=["date", "estimate_pct"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _change(series: pd.Series, sessions: int) -> float | None:
    if len(series) <= sessions:
        return None
    return float(series.iloc[-1] - series.iloc[-sessions - 1])


def _status(value: float) -> str:
    if value < 135:
        return "極度吃緊"
    if value < 145:
        return "壓力偏高"
    if value < 155:
        return "緩衝偏薄"
    return "仍有緩衝"


def _direction(change_5d: float | None) -> str:
    if change_5d is None:
        return "資料累積中"
    if change_5d <= -5:
        return "快速惡化"
    if change_5d <= -2:
        return "正在惡化"
    if change_5d >= 5:
        return "快速改善"
    if change_5d >= 2:
        return "正在改善"
    return "大致持平"


def make_snapshot(df: pd.DataFrame) -> MarginSnapshot:
    if df.empty:
        raise ValueError("Margin-maintenance cache is empty")
    row = df.iloc[-1]
    values = df["estimate_pct"].astype(float)
    value = float(row["estimate_pct"])
    d1 = _change(values, 1)
    d5 = _change(values, 5)
    d20 = _change(values, 20)

    one_year = values.iloc[-252:]
    percentile = None
    # Do not call a short seed cache a "one-year percentile". Roughly six
    # months of sessions is the minimum before this context becomes useful.
    if len(one_year) >= 120:
        percentile = float((one_year <= value).mean() * 100.0)

    status = _status(value)
    direction = _direction(d5)
    if value < 145 or (d5 is not None and d5 <= -5):
        headline, tone = "融資緩衝明顯轉弱", "negative"
    elif value < 155 or (d5 is not None and d5 <= -2):
        headline, tone = "融資風險正在升溫", "negative"
    elif d5 is not None and d5 >= 2:
        headline, tone = "融資緩衝正在改善", "positive"
    else:
        headline, tone = "融資緩衝仍然穩定", "neutral"

    taiex = row.get("taiex_close")
    return MarginSnapshot(
        date=pd.Timestamp(row["date"]).date().isoformat(),
        estimate_pct=value,
        change_1d=d1,
        change_5d=d5,
        change_20d=d20,
        percentile_1y=percentile,
        financing_billion=float(row["financing_balance_billion"]),
        collateral_billion=float(row["collateral_value_billion"]),
        taiex_close=float(taiex) if pd.notna(taiex) else None,
        distance_to_call=value - LEGAL_CALL_REFERENCE,
        status=status,
        direction=direction,
        headline=headline,
        tone=tone,
    )


def tone_color(tone: str) -> str:
    """Taiwan convention requested by the user: red good, green bad."""
    return {
        "positive": "#ef4444",
        "negative": "#22c55e",
        "neutral": "#f59e0b",
    }.get(tone, "#94a3b8")

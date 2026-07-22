"""Build the single safe LINE batch: one insight text plus active ETF images."""
from __future__ import annotations

import json
from pathlib import Path
import time


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ACTION_CACHE = DATA_DIR / "etf_action_insight.json"
WEBHOOK_HOST = "https://linechatbot.duckdns.org"
LINE_MAX_OBJECTS = 5
MAX_ACTIVE_IMAGES = LINE_MAX_OBJECTS - 1

ETF_SHORT = {
    "00403A": "403",
    "00981A": "981",
    "00988A": "988",
    "00991A": "991",
}

ACTIVE_NAMES = {
    "00403A": "主動統一升級50",
    "00981A": "主動統一台股增長",
    "00988A": "主動統一全球創新",
    "00991A": "主動復華未來50",
}
ACTIVE_TICKERS = list(ACTIVE_NAMES)
ACTIVE_SHORT_NAMES = {
    "00403A": "升級50",
    "00981A": "台股增長",
    "00988A": "全球創新",
    "00991A": "未來50",
}


def _latest_history_date(ticker: str, data_dir: Path) -> str:
    path = data_dir / f"etf_{ticker}_history.json"
    with path.open(encoding="utf-8") as fh:
        return max(json.load(fh).keys())


def _mobile_date(value: str) -> str:
    try:
        _, month, day = value.split("-")
        return f"{int(month)}月{int(day)}日"
    except (ValueError, TypeError):
        return value


def _action_text(cache_path: Path) -> str:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    text = str(payload.get("line_text") or "").strip()
    if not text:
        raise RuntimeError(f"{cache_path} has no line_text")
    return text


def build_active_report_messages(
    tickers: list[str],
    *,
    data_dir: Path = DATA_DIR,
    action_cache: Path = ACTION_CACHE,
    webhook_host: str = WEBHOOK_HOST,
    cache_buster: int | None = None,
) -> list[dict]:
    if not tickers:
        raise ValueError("at least one active ETF is required")
    if len(tickers) > MAX_ACTIVE_IMAGES:
        raise ValueError(
            f"LINE batch allows at most {MAX_ACTIVE_IMAGES} active ETF images; "
            f"got {len(tickers)}"
        )
    stamp = int(time.time()) if cache_buster is None else cache_buster
    header_lines = ["📊 主動 ETF 操作日報"]
    header_lines.extend(
        f"{ETF_SHORT.get(ticker, ticker)} {ACTIVE_SHORT_NAMES.get(ticker, ACTIVE_NAMES.get(ticker, ticker))}"
        f"｜{_mobile_date(_latest_history_date(ticker, data_dir))}"
        for ticker in tickers
    )
    text = "\n".join(header_lines) + "\n\n" + _action_text(action_cache)
    messages = [{"type": "text", "text": text}]
    for ticker in tickers:
        img_url = (
            f"{webhook_host}/api/webhook/summaries/"
            f"etf_{ticker}_summary_latest.jpg?t={stamp}"
        )
        messages.append(
            {
                "type": "image",
                "originalContentUrl": img_url,
                "previewImageUrl": img_url,
            }
        )
    if len(messages) > LINE_MAX_OBJECTS:
        raise AssertionError(
            f"refusing LINE payload with {len(messages)} objects; max is {LINE_MAX_OBJECTS}"
        )
    return messages

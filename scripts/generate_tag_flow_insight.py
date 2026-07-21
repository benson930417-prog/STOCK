#!/usr/bin/env python3
"""Generate one decision-focused 類股 insight shared by email and LINE.

The input is the price-drift-free, category-only observation cache produced by
``build_tag_flow.py``.  This script deliberately does not use 概念股 labels.
It identifies sectors that are both net-bought and accelerating, then lists
only stocks bought by all three Taiwan active ETFs in the selected window.

The generated JSON is a cache: the daily email and LINE webhook both read the
same text so their interpretation cannot drift.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import statistics

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE = DATA / "tag_flow.json"
OUT = DATA / "tag_flow_insight.json"
SUMMARY_DIR = DATA / "summaries"

ETFS = ["00403A", "00981A", "00991A"]
LOOKBACK = 10
RECENT_DAYS = 3
MAX_SECTORS = 3
MAX_SELL_SECTORS = 3
MAX_STOCKS = 5
EPSILON = 1e-6
MIN_STRENGTH = 0.05
MIN_ACCELERATION = 0.005

CARD_WIDTH = 1200
SECTOR_CARD_HEIGHT = 350
SECTOR_CARD_GAP = 14
CARD_BG = (8, 13, 23)
CARD_PANEL = (18, 26, 39)
CARD_PANEL_ALT = (22, 31, 46)
CARD_LINE = (42, 55, 74)
CARD_TEXT = (244, 247, 252)
CARD_MUTED = (154, 166, 184)
CARD_RED = (239, 68, 68)
CARD_RED_SOFT = (72, 29, 35)
CARD_GREEN = (34, 197, 94)
CARD_GREEN_SOFT = (13, 55, 38)
CARD_SLATE = (100, 116, 139)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    windows = [
        r"C:\Windows\Fonts\msjhbd.ttc" if bold else r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\NotoSansTC-Bold.otf" if bold else r"C:\Windows\Fonts\NotoSansTC-Regular.otf",
    ]
    linux = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Bold.otf" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    candidates = windows if os.name == "nt" else linux
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


CARD_FONTS = {
    "brand": _font(22, True),
    "title": _font(54, True),
    "date": _font(22, True),
    "subtitle": _font(25),
    "rank": _font(35, True),
    "sector": _font(39, True),
    "section": _font(28, True),
    "section_note": _font(20),
    "badge": _font(21, True),
    "body": _font(25),
    "body_bold": _font(25, True),
    "chip": _font(24, True),
    "chart": _font(19, True),
    "chart_date": _font(16),
    "cooling": _font(28, True),
    "footer": _font(20),
}


def _rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap_chars(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
            if len(lines) == max_lines - 1:
                break
        else:
            current = candidate
    consumed = sum(len(line) for line in lines)
    remaining = text[consumed:]
    if len(lines) < max_lines:
        current = remaining
        while current and _text_width(draw, current, font) > max_width:
            current = current[:-1]
        if len(current) < len(remaining) and current:
            current = current[:-1] + "…"
        if current:
            lines.append(current)
    return lines or [""]


def _draw_section_title(
    draw: ImageDraw.ImageDraw,
    top: int,
    title: str,
    note: str,
    color: tuple[int, int, int],
) -> None:
    draw.rounded_rectangle((54, top + 8, 62, top + 40), radius=4, fill=color)
    draw.text((78, top), title, font=CARD_FONTS["section"], fill=CARD_TEXT)
    title_w = _text_width(draw, title, CARD_FONTS["section"])
    draw.text(
        (94 + title_w, top + 8),
        note,
        font=CARD_FONTS["section_note"],
        fill=CARD_MUTED,
    )


def _draw_mini_trend(
    draw: ImageDraw.ImageDraw,
    row: dict,
    dates: list[str],
    left: int,
    top: int,
    right: int,
    scale: float,
) -> None:
    values = [float(value) for value in row.get("daily", [])]
    if not values:
        return
    draw.text(
        (left, top),
        f"{LOOKBACK}日每日相對力道",
        font=CARD_FONTS["chart"],
        fill=CARD_MUTED,
    )
    total_text = f"{LOOKBACK}日合計 {row['strength']:+.2f}%"
    total_w = _text_width(draw, total_text, CARD_FONTS["chart"])
    draw.text(
        (right - total_w, top),
        total_text,
        font=CARD_FONTS["chart"],
        fill=CARD_RED if row["strength"] >= 0 else CARD_GREEN,
    )

    graph_top = top + 31
    zero_y = graph_top + 39
    half_height = 35
    graph_width = right - left
    cell_width = graph_width / len(values)
    draw.line((left, zero_y, right, zero_y), fill=CARD_LINE, width=2)
    points: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        center_x = left + cell_width * (index + 0.5)
        end_y = zero_y - max(-1.0, min(1.0, value / scale)) * half_height
        color = CARD_RED if value >= 0 else CARD_GREEN
        bar_half = min(21, int(cell_width * 0.16))
        y0, y1 = sorted((zero_y, end_y))
        if abs(y1 - y0) < 2:
            y0, y1 = zero_y - 1, zero_y + 1
        draw.rounded_rectangle(
            (center_x - bar_half, y0, center_x + bar_half, y1),
            radius=4,
            fill=color,
        )
        points.append((center_x, end_y))
        date_text = dates[index][5:].replace("-", "/") if index < len(dates) else ""
        date_w = _text_width(draw, date_text, CARD_FONTS["chart_date"])
        draw.text(
            (center_x - date_w / 2, graph_top + 82),
            date_text,
            font=CARD_FONTS["chart_date"],
            fill=CARD_SLATE,
        )
    if len(points) > 1:
        draw.line(points, fill=CARD_TEXT, width=3, joint="curve")
    for x, y in points:
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=CARD_TEXT)


def _draw_sector_card(
    draw: ImageDraw.ImageDraw,
    row: dict,
    rank: int,
    top: int,
    dates: list[str],
    chart_scale: float,
    direction: str,
) -> None:
    buying = direction == "buy"
    accent = CARD_RED if buying else CARD_GREEN
    soft = CARD_RED_SOFT if buying else CARD_GREEN_SOFT
    left, right = 54, CARD_WIDTH - 54
    bottom = top + SECTOR_CARD_HEIGHT
    _rounded(draw, (left, top, right, bottom), 24, CARD_PANEL, CARD_LINE, 2)
    draw.rounded_rectangle((left, top, left + 9, bottom), radius=5, fill=accent)

    rank_box = (left + 28, top + 25, left + 92, top + 89)
    _rounded(draw, rank_box, 18, soft, accent, 2)
    rank_text = str(rank)
    rank_w = _text_width(draw, rank_text, CARD_FONTS["rank"])
    draw.text(
        (rank_box[0] + (64 - rank_w) / 2, top + 31),
        rank_text,
        font=CARD_FONTS["rank"],
        fill=accent,
    )

    draw.text(
        (left + 116, top + 25),
        row["category"],
        font=CARD_FONTS["sector"],
        fill=CARD_TEXT,
    )
    if buying:
        badge_text = "強勢 × 加速"
    elif not row["latest_negative"]:
        badge_text = "賣壓緩和"
    elif row["acceleration"] < -MIN_ACCELERATION:
        badge_text = "賣壓加重"
    else:
        badge_text = "持續減碼"
    badge_w = _text_width(draw, badge_text, CARD_FONTS["badge"]) + 40
    badge_box = (right - badge_w - 28, top + 30, right - 28, top + 74)
    _rounded(draw, badge_box, 18, soft, None)
    draw.text(
        (badge_box[0] + 20, top + 39),
        badge_text,
        font=CARD_FONTS["badge"],
        fill=accent,
    )

    reason = _sector_reason(row, LOOKBACK) if buying else _sell_reason(row, LOOKBACK)
    reason_lines = _wrap_chars(draw, reason, CARD_FONTS["body"], right - left - 170)
    for index, line in enumerate(reason_lines[:2]):
        draw.text(
            (left + 116, top + 88 + index * 34),
            line,
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )

    label_y = top + 153
    pool_key = "stocks_all_three" if buying else "stocks_all_three_selling"
    pool_label = "三檔共買池" if buying else "三檔共賣池"
    draw.text(
        (left + 116, label_y),
        pool_label,
        font=CARD_FONTS["body_bold"],
        fill=CARD_TEXT,
    )
    chip_x = left + 300
    names = [stock["name"] for stock in row.get(pool_key, [])]
    if not names:
        draw.text(
            (chip_x, label_y),
            "沒有同一檔股票形成 3/3 共識",
            font=CARD_FONTS["body"],
            fill=CARD_SLATE,
        )
    else:
        for name in names:
            chip_w = _text_width(draw, name, CARD_FONTS["chip"]) + 42
            if chip_x + chip_w > right - 28:
                break
            _rounded(
                draw,
                (chip_x, label_y - 5, chip_x + chip_w, label_y + 39),
                18,
                soft,
                accent,
                1,
            )
            draw.text(
                (chip_x + 21, label_y + 1),
                name,
                font=CARD_FONTS["chip"],
                fill=CARD_TEXT,
            )
            chip_x += chip_w + 12

    draw.line((left + 116, top + 207, right - 28, top + 207), fill=CARD_LINE, width=1)
    _draw_mini_trend(
        draw,
        row,
        dates,
        left + 116,
        top + 218,
        right - 28,
        chart_scale,
    )


def _render_card(payload: dict) -> tuple[Path, Path]:
    sectors = payload.get("sectors", [])[:MAX_SECTORS]
    selling = payload.get("selling", [])[:MAX_SELL_SECTORS]
    cooling = payload.get("cooling")
    buy_block_height = (
        len(sectors) * SECTOR_CARD_HEIGHT + max(0, len(sectors) - 1) * SECTOR_CARD_GAP
        if sectors else 142
    )
    sell_block_height = (
        len(selling) * SECTOR_CARD_HEIGHT + max(0, len(selling) - 1) * SECTOR_CARD_GAP
        if selling else 142
    )
    card_height = (
        220 + 48 + buy_block_height + 30 + 48 + sell_block_height
        + (130 if cooling else 0) + 178 + 88
    )
    img = Image.new("RGB", (CARD_WIDTH, card_height), CARD_BG)
    draw = ImageDraw.Draw(img)

    # Subtle top glow keeps the dark report family resemblance without
    # introducing decorative noise into the decision surface.
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for radius, alpha in [(310, 10), (240, 14), (170, 18)]:
        glow_draw.ellipse(
            (CARD_WIDTH - radius * 2 + 80, -radius, CARD_WIDTH + 80, radius),
            fill=(*CARD_RED, alpha),
        )
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(img)

    draw.text((56, 42), "MASTER WU · ACTIVE ETF", font=CARD_FONTS["brand"], fill=CARD_RED)
    draw.text((54, 82), "主動 ETF 類股洞察", font=CARD_FONTS["title"], fill=CARD_TEXT)
    date_text = f"資料截至 {payload['as_of']}"
    date_w = _text_width(draw, date_text, CARD_FONTS["date"]) + 36
    _rounded(
        draw,
        (CARD_WIDTH - 54 - date_w, 92, CARD_WIDTH - 54, 140),
        18,
        CARD_PANEL_ALT,
        CARD_LINE,
        1,
    )
    draw.text(
        (CARD_WIDTH - 54 - date_w + 18, 103),
        date_text,
        font=CARD_FONTS["date"],
        fill=CARD_MUTED,
    )
    draw.text(
        (56, 162),
        "買與賣都看｜403 · 981 · 991｜紅色加碼 · 綠色減碼",
        font=CARD_FONTS["subtitle"],
        fill=CARD_MUTED,
    )

    dates = payload.get("window", {}).get("dates", [])
    chart_rows = sectors + selling
    chart_scale = max(
        [abs(float(value)) for row in chart_rows for value in row.get("daily", [])]
        + [0.05]
    )

    y = 220
    _draw_section_title(draw, y, "買盤主線", "淨加碼、近期加速，且至少兩檔 ETF 同向", CARD_RED)
    y += 48
    if sectors:
        for index, row in enumerate(sectors, 1):
            _draw_sector_card(draw, row, index, y, dates, chart_scale, "buy")
            y += SECTOR_CARD_HEIGHT + SECTOR_CARD_GAP
        y -= SECTOR_CARD_GAP
    else:
        _rounded(
            draw,
            (54, y, CARD_WIDTH - 54, y + 142),
            24,
            CARD_PANEL,
            CARD_LINE,
            2,
        )
        draw.text(
            (84, y + 25),
            "目前沒有明確主線",
            font=CARD_FONTS["sector"],
            fill=CARD_TEXT,
        )
        draw.text(
            (84, y + 83),
            "沒有類股同時符合淨加碼、買盤加速與多數 ETF 同向。",
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )
        y += 142

    y += 30
    _draw_section_title(draw, y, "賣壓警示", "實際淨減碼，不把單純降溫誤叫成賣出", CARD_GREEN)
    y += 48
    if selling:
        for index, row in enumerate(selling, 1):
            _draw_sector_card(draw, row, index, y, dates, chart_scale, "sell")
            y += SECTOR_CARD_HEIGHT + SECTOR_CARD_GAP
        y -= SECTOR_CARD_GAP
    else:
        _rounded(
            draw,
            (54, y, CARD_WIDTH - 54, y + 142),
            24,
            CARD_PANEL,
            CARD_LINE,
            2,
        )
        draw.text(
            (84, y + 25),
            "本期沒有明確重度減碼類股",
            font=CARD_FONTS["sector"],
            fill=CARD_TEXT,
        )
        draw.text(
            (84, y + 83),
            f"需同時符合 {LOOKBACK} 日明確淨賣，且至少兩檔 ETF 同向。",
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )
        y += 142

    if cooling:
        y += 18
        _rounded(
            draw,
            (54, y, CARD_WIDTH - 54, y + 112),
            22,
            CARD_GREEN_SOFT,
            CARD_GREEN,
            2,
        )
        draw.rounded_rectangle((54, y, 63, y + 112), radius=5, fill=CARD_GREEN)
        draw.text(
            (84, y + 24),
            "降溫提醒",
            font=CARD_FONTS["badge"],
            fill=CARD_GREEN,
        )
        draw.text(
            (225, y + 19),
            cooling["category"],
            font=CARD_FONTS["cooling"],
            fill=CARD_TEXT,
        )
        draw.text(
            (225, y + 61),
            "仍是淨買，但近期力道放慢；這不是淨賣出。",
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )
        y += 112

    conclusion_y = y + 24
    _rounded(
        draw,
        (54, conclusion_y, CARD_WIDTH - 54, conclusion_y + 150),
        24,
        CARD_PANEL_ALT,
        CARD_LINE,
        2,
    )
    draw.text(
        (84, conclusion_y + 22),
        "紅｜優先觀察",
        font=CARD_FONTS["badge"],
        fill=CARD_RED,
    )
    pool_names: list[str] = []
    for row in sectors:
        for stock in row.get("stocks_all_three", []):
            if stock["name"] not in pool_names:
                pool_names.append(stock["name"])
    buy_conclusion = "、".join(pool_names[:6]) if pool_names else "等待三檔 ETF 形成同股共識"
    draw.text(
        (225, conclusion_y + 17),
        buy_conclusion,
        font=CARD_FONTS["cooling"],
        fill=CARD_TEXT,
    )
    draw.line((84, conclusion_y + 73, CARD_WIDTH - 84, conclusion_y + 73), fill=CARD_LINE, width=1)
    draw.text(
        (84, conclusion_y + 94),
        "綠｜賣壓留意",
        font=CARD_FONTS["badge"],
        fill=CARD_GREEN,
    )
    sell_categories = "、".join(row["category"] for row in selling)
    draw.text(
        (225, conclusion_y + 89),
        sell_categories or "本期沒有明確重度減碼類股",
        font=CARD_FONTS["cooling"],
        fill=CARD_TEXT,
    )

    footer_y = conclusion_y + 177
    footer = f"類股 only · 近 {LOOKBACK} 個共同交易日 · 同一尺度趨勢圖 · 共買／共賣池皆需 3/3 ETF"
    footer_w = _text_width(draw, footer, CARD_FONTS["footer"])
    draw.text(
        ((CARD_WIDTH - footer_w) / 2, footer_y),
        footer,
        font=CARD_FONTS["footer"],
        fill=CARD_SLATE,
    )
    note = "此卡是資金行為摘要，不是投資建議"
    note_w = _text_width(draw, note, CARD_FONTS["footer"])
    draw.text(
        ((CARD_WIDTH - note_w) / 2, footer_y + 32),
        note,
        font=CARD_FONTS["footer"],
        fill=CARD_SLATE,
    )

    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = SUMMARY_DIR / "tag_flow_insight_latest.jpg"
    dated_path = SUMMARY_DIR / f"tag_flow_insight_{payload['as_of']}.jpg"
    img.save(latest_path, "JPEG", quality=92, optimize=True, progressive=True)
    shutil.copyfile(latest_path, dated_path)
    return latest_path, dated_path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _shared_dates(data: dict) -> list[str]:
    by_etf = data.get("dates", {}).get("by_etf", {})
    date_sets = [set(by_etf.get(etf, [])) for etf in ETFS]
    if any(not values for values in date_sets):
        return []
    return sorted(set.intersection(*date_sets))


def _aggregate(data: dict, dates: list[str]) -> list[dict]:
    date_set = set(dates)
    sector_rows: dict[str, dict] = {}

    for observation in data.get("observations", []):
        etf = observation.get("etf")
        date = observation.get("date")
        if etf not in ETFS or date not in date_set:
            continue
        for move in observation.get("stocks", []):
            category = str(move.get("category") or "未分類")
            if category == "未分類":
                continue
            flow = float(move.get("flow") or 0.0)
            stock_id = str(move.get("id") or "")
            sector = sector_rows.setdefault(
                category,
                {
                    "category": category,
                    "by_etf": defaultdict(float),
                    "by_date": defaultdict(float),
                    "stocks": {},
                },
            )
            sector["by_etf"][etf] += flow
            sector["by_date"][date] += flow / len(ETFS)
            stock = sector["stocks"].setdefault(
                stock_id,
                {
                    "id": stock_id,
                    "name": move.get("name") or stock_id,
                    "by_etf": defaultdict(float),
                },
            )
            stock["by_etf"][etf] += flow

    prior_dates = dates[:-RECENT_DAYS]
    recent_dates = dates[-RECENT_DAYS:]
    results: list[dict] = []
    for sector in sector_rows.values():
        by_etf = dict(sector["by_etf"])
        strength = sum(by_etf.get(etf, 0.0) for etf in ETFS) / len(ETFS)
        prior_avg = statistics.fmean(
            sector["by_date"].get(date, 0.0) for date in prior_dates
        )
        recent_avg = statistics.fmean(
            sector["by_date"].get(date, 0.0) for date in recent_dates
        )
        acceleration = recent_avg - prior_avg
        daily = [sector["by_date"].get(date, 0.0) for date in dates]

        stock_buy_pool = []
        stock_sell_pool = []
        for stock in sector["stocks"].values():
            stock_by_etf = dict(stock["by_etf"])
            buyers = sum(stock_by_etf.get(etf, 0.0) > EPSILON for etf in ETFS)
            sellers = sum(stock_by_etf.get(etf, 0.0) < -EPSILON for etf in ETFS)
            stock_strength = sum(
                stock_by_etf.get(etf, 0.0) for etf in ETFS
            ) / len(ETFS)
            if buyers == len(ETFS) and stock_strength > EPSILON:
                stock_buy_pool.append(
                    {
                        "id": stock["id"],
                        "name": stock["name"],
                        "strength": round(stock_strength, 4),
                    }
                )
            if sellers == len(ETFS) and stock_strength < -EPSILON:
                stock_sell_pool.append(
                    {
                        "id": stock["id"],
                        "name": stock["name"],
                        "strength": round(stock_strength, 4),
                    }
                )
        stock_buy_pool.sort(key=lambda row: -row["strength"])
        stock_sell_pool.sort(key=lambda row: row["strength"])

        results.append(
            {
                "category": sector["category"],
                "strength": round(strength, 4),
                "acceleration": round(acceleration, 4),
                "buyers": sum(by_etf.get(etf, 0.0) > EPSILON for etf in ETFS),
                "sellers": sum(by_etf.get(etf, 0.0) < -EPSILON for etf in ETFS),
                "buy_days": sum(value > EPSILON for value in daily),
                "sell_days": sum(value < -EPSILON for value in daily),
                "latest_positive": daily[-1] > EPSILON,
                "latest_negative": daily[-1] < -EPSILON,
                "daily": [round(value, 4) for value in daily],
                "stocks_all_three": stock_buy_pool[:MAX_STOCKS],
                "stocks_all_three_selling": stock_sell_pool[:MAX_STOCKS],
            }
        )
    return results


def _sector_reason(row: dict, n_dates: int) -> str:
    if row["buyers"] == 3:
        breadth = "三檔主動 ETF 同步加碼"
    else:
        breadth = "多數主動 ETF 同向加碼"
    persistence_days = max(3, math.ceil(n_dates * 0.8))
    persistence = (
        "買盤具持續性" if row["buy_days"] >= persistence_days
        else "近期買盤轉強"
    )
    return (
        f"{breadth}，{persistence}，而且最近 {RECENT_DAYS} 日力道"
        f"高於前 {LOOKBACK - RECENT_DAYS} 日。"
    )


def _sell_reason(row: dict, n_dates: int) -> str:
    if row["sellers"] == 3:
        breadth = "三檔主動 ETF 同步減碼"
    else:
        breadth = "多數主動 ETF 同向減碼"
    persistence_days = max(3, math.ceil(n_dates * 0.8))
    persistence = (
        "賣壓具持續性" if row["sell_days"] >= persistence_days
        else "近期轉為明顯賣超"
    )
    if not row["latest_negative"]:
        acceleration = "但最新一日已轉正，賣壓正在緩和"
    elif row["acceleration"] < -MIN_ACCELERATION:
        acceleration = f"而且最近 {RECENT_DAYS} 日賣壓加重"
    else:
        acceleration = "最新一日仍在減碼"
    return f"{breadth}，{persistence}，{acceleration}。"


def _render_line(
    as_of: str,
    sectors: list[dict],
    selling: list[dict],
    cooling: dict | None,
) -> str:
    lines = [
        "🔥 吳大師｜ETF 類股洞察",
        f"截至 {as_of}｜近 {LOOKBACK} 個共同交易日",
        "",
        "🔴 買盤主線",
    ]
    if not sectors:
        lines.append(
            "目前沒有同時符合『淨加碼＋正在加速＋至少兩檔 ETF 同向』的明確主線。"
        )
    else:
        for index, row in enumerate(sectors, 1):
            lines.append(f"主線 {index}｜{row['category']}：強勢加速")
            lines.append(_sector_reason(row, LOOKBACK))
            names = [stock["name"] for stock in row["stocks_all_three"]]
            if names:
                lines.append("三檔共買池：" + "、".join(names))
            else:
                lines.append("三檔共買池：暫無同一檔個股獲三檔同步加碼")
            lines.append("")

    lines.extend(["", "🟢 賣壓警示"])
    if selling:
        for index, row in enumerate(selling, 1):
            lines.append(f"減碼 {index}｜{row['category']}：明確淨賣")
            lines.append(_sell_reason(row, LOOKBACK))
            names = [stock["name"] for stock in row["stocks_all_three_selling"]]
            if names:
                lines.append("三檔共賣池：" + "、".join(names))
            else:
                lines.append("三檔共賣池：類股有賣壓，但沒有同一檔股票形成 3/3 共識")
            lines.append("")
    else:
        lines.extend(
            [
                "本期沒有明確重度減碼類股。",
                f"條件：{LOOKBACK} 日明確淨賣，且至少兩檔 ETF 同向。",
                "",
            ]
        )

    if cooling:
        lines.append(
            f"降溫提醒｜{cooling['category']}：仍是淨買，但近期力道已放慢；這不是淨賣出。"
        )
        lines.append("")
    pool_names = []
    for row in sectors:
        for stock in row["stocks_all_three"]:
            if stock["name"] not in pool_names:
                pool_names.append(stock["name"])
    buy_summary = "、".join(pool_names[:6]) if pool_names else "尚未形成 3/3 同股共識"
    sell_summary = "、".join(row["category"] for row in selling) or "本期無明確重度減碼"
    lines.append(f"一句話｜紅：{buy_summary}｜綠：{sell_summary}")
    return "\n".join(lines).strip()


def generate() -> dict:
    data = _load(SOURCE)
    if data.get("schema_version") != 2:
        raise RuntimeError("tag_flow.json schema_version must be 2")
    dates = _shared_dates(data)
    if len(dates) < LOOKBACK:
        raise RuntimeError(f"need at least {LOOKBACK} common ETF sessions")
    selected_dates = dates[-LOOKBACK:]
    rows = _aggregate(data, selected_dates)
    candidates = [
        row for row in rows
        if row["strength"] >= MIN_STRENGTH
        and row["acceleration"] >= MIN_ACCELERATION
        and row["latest_positive"]
        and row["buyers"] >= 2
    ]
    candidates.sort(
        key=lambda row: (-row["buyers"], -row["strength"], -row["acceleration"])
    )
    selected = candidates[:MAX_SECTORS]
    selling_candidates = [
        row for row in rows
        if row["strength"] <= -MIN_STRENGTH
        and row["sellers"] >= 2
    ]
    selling_candidates.sort(
        key=lambda row: (-row["sellers"], row["strength"], row["acceleration"])
    )
    selling = selling_candidates[:MAX_SELL_SECTORS]
    cooling_rows = [
        row for row in rows
        if row["strength"] > MIN_STRENGTH
        and row["acceleration"] < -MIN_ACCELERATION
        and row["buyers"] >= 2
    ]
    cooling = max(cooling_rows, key=lambda row: row["strength"], default=None)
    line_text = _render_line(selected_dates[-1], selected, selling, cooling)
    payload = {
        "schema_version": 1,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_generated": data.get("generated"),
        "as_of": selected_dates[-1],
        "window": {
            "dates": selected_dates,
            "recent_days": RECENT_DAYS,
            "comparison_days": LOOKBACK - RECENT_DAYS,
            "etfs": ETFS,
        },
        "methodology": (
            "category-only; strong = positive equal-weight normalized flow; "
            f"accelerating = latest {RECENT_DAYS}-session daily average above "
            f"prior {LOOKBACK - RECENT_DAYS}; "
            f"heavy selling = negative {LOOKBACK}-session flow with at least 2 ETFs net "
            "selling; latest-session direction labels whether pressure is worsening, "
            "continuing, or easing; stock pools require same-direction "
            "normalized flow from all 3 ETFs"
        ),
        "sectors": selected,
        "selling": selling,
        "cooling": cooling,
        "line_text": line_text,
        "email_text": line_text,
    }
    latest_image, dated_image = _render_card(payload)
    payload["image"] = latest_image.name
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"[theme-insight] as_of={payload['as_of']} common_sessions={len(selected_dates)} "
        f"strong_accelerating={len(selected)} "
        f"leaders={','.join(row['category'] for row in selected) or 'none'} "
        f"heavy_selling={','.join(row['category'] for row in selling) or 'none'}"
    )
    print(f"Saved {latest_image.relative_to(ROOT)}")
    print(f"Saved {dated_image.relative_to(ROOT)}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print", choices=["line", "email"])
    args = parser.parse_args()
    if args.print:
        payload = _load(OUT)
        print(payload[f"{args.print}_text"])
        return 0
    generate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

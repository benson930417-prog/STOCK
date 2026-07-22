#!/usr/bin/env python3
"""Generate one window-independent 類股 rotation insight for email and LINE.

The input is the price-drift-free, category-only observation cache produced by
``build_tag_flow.py``.  This script deliberately does not use 概念股 labels.
It delegates every decision to ``src.tag_flow_rotation`` so the dashboard,
email, and LINE card cannot tell different stories when a chart interval moves.

The generated JSON is a cache: the daily email and LINE webhook both read the
same text so their interpretation cannot drift.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tag_flow_rotation import build_rotation_snapshot, phase_explanation

DATA = ROOT / "data"
SOURCE = DATA / "tag_flow.json"
OUT = DATA / "tag_flow_insight.json"
SUMMARY_DIR = DATA / "summaries"

ETFS = ["00403A", "00981A", "00991A"]
# The card keeps ten dated mini-bars for readability.  This is a viewport only;
# the rotation verdict uses all common history inside tag_flow_rotation.py.
LOOKBACK = 10
MAX_SECTORS = 3
MAX_SELL_SECTORS = 3
MAX_STOCKS = 5

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
        f"近 {LOOKBACK} 日走勢（僅供視覺）",
        font=CARD_FONTS["chart"],
        fill=CARD_MUTED,
    )
    total = float(row.get("window_totals", {}).get(str(LOOKBACK), 0.0))
    total_text = f"區間合計 {total:+.2f}%"
    total_w = _text_width(draw, total_text, CARD_FONTS["chart"])
    draw.text(
        (right - total_w, top),
        total_text,
        font=CARD_FONTS["chart"],
        fill=CARD_RED if total >= 0 else CARD_GREEN,
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
    badge_text = row.get("phase_short") or row.get("phase_label") or "輪動觀察"
    badge_w = _text_width(draw, badge_text, CARD_FONTS["badge"]) + 40
    badge_box = (right - badge_w - 28, top + 30, right - 28, top + 74)
    _rounded(draw, badge_box, 18, soft, None)
    draw.text(
        (badge_box[0] + 20, top + 39),
        badge_text,
        font=CARD_FONTS["badge"],
        fill=accent,
    )

    reason = phase_explanation(row)
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
    warning = payload.get("warning")
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
        + (130 if warning else 0) + 178 + 88
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
    _draw_section_title(draw, y, "買盤階段", "平滑方向已確認，且至少兩檔 ETF 同向", CARD_RED)
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
            "目前沒有已確認的買盤階段",
            font=CARD_FONTS["sector"],
            fill=CARD_TEXT,
        )
        draw.text(
            (84, y + 83),
            "沒有類股同時通過平滑方向、ETF 廣度與兩日確認。",
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )
        y += 142

    y += 30
    _draw_section_title(draw, y, "減碼階段", "近期賣壓已確認，不把背景降溫誤叫成賣出", CARD_GREEN)
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
            "目前沒有已確認的減碼階段",
            font=CARD_FONTS["sector"],
            fill=CARD_TEXT,
        )
        draw.text(
            (84, y + 83),
            "需同時通過平滑賣壓、至少兩檔 ETF 同向與兩日確認。",
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )
        y += 142

    if warning:
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
            "3日 EWMA 警示",
            font=CARD_FONTS["badge"],
            fill=CARD_GREEN,
        )
        draw.text(
            (310, y + 19),
            warning["category"],
            font=CARD_FONTS["cooling"],
            fill=CARD_TEXT,
        )
        alert_reason = (
            f"目前壓力 {warning.get('fast', 0.0):+.2f}%規模 · "
            f"{warning.get('sellers', 0)}/{warning.get('etf_count', 3)} ETF偏賣"
        )
        draw.text(
            (310, y + 61),
            alert_reason,
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
        "紅｜已確認買盤",
        font=CARD_FONTS["badge"],
        fill=CARD_RED,
    )
    pool_names: list[str] = []
    for row in sectors:
        for stock in row.get("stocks_all_three", []):
            if stock["name"] not in pool_names:
                pool_names.append(stock["name"])
    buy_conclusion = "、".join(pool_names[:6]) if pool_names else "目前沒有 3/3 ETF 同股買盤共識"
    draw.text(
        (330, conclusion_y + 17),
        buy_conclusion,
        font=CARD_FONTS["cooling"],
        fill=CARD_TEXT,
    )
    draw.line((84, conclusion_y + 73, CARD_WIDTH - 84, conclusion_y + 73), fill=CARD_LINE, width=1)
    draw.text(
        (84, conclusion_y + 94),
        "綠｜目前賣壓警示",
        font=CARD_FONTS["badge"],
        fill=CARD_GREEN,
    )
    sell_names = ([warning["category"]] if warning else []) + [
        row["category"] for row in selling
    ]
    sell_categories = "、".join(dict.fromkeys(sell_names))
    draw.text(
        (330, conclusion_y + 89),
        sell_categories or "目前沒有已確認的減碼類股",
        font=CARD_FONTS["cooling"],
        fill=CARD_TEXT,
    )

    footer_y = conclusion_y + 177
    footer = (
        f"類股 only · 輪動故事不採固定窗口 · 近 {LOOKBACK} 日僅為視覺 · "
        "共買／共賣池皆需 3/3 ETF"
    )
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


def _render_line(
    as_of: str,
    sectors: list[dict],
    selling: list[dict],
    warning: dict | None,
) -> str:
    lines = [
        "🔥 吳大師｜ETF 類股洞察",
        f"截至 {as_of}｜輪動故事不採固定窗口",
        "",
        "🔴 已確認買盤階段",
    ]
    if not sectors:
        lines.append("目前沒有同時通過平滑方向、ETF 廣度與兩日確認的買盤類股。")
    else:
        for index, row in enumerate(sectors, 1):
            lines.append(f"買盤 {index}｜{row['category']}：{row['phase_label']}")
            lines.append(phase_explanation(row))
            names = [stock["name"] for stock in row["stocks_all_three"]]
            if names:
                lines.append("三檔共買池：" + "、".join(names))
            else:
                lines.append("三檔共買池：目前沒有 3/3 ETF 同股買盤共識")
            lines.append("")

    if warning:
        lines.extend(
            [
                "🟢 目前減碼警示（3 日 EWMA）",
                (
                    f"{warning['category']}：{warning['fast']:+.2f}% 規模｜"
                    f"{warning['sellers']}/{warning['etf_count']} ETF 偏賣"
                ),
                "目前壓力已轉負，不必等 10／20 日 EWMA 完全翻空。",
                "",
            ]
        )

    lines.extend(["", "🟢 已確認減碼階段"])
    if selling:
        for index, row in enumerate(selling, 1):
            lines.append(f"減碼 {index}｜{row['category']}：{row['phase_label']}")
            lines.append(phase_explanation(row))
            names = [stock["name"] for stock in row["stocks_all_three_selling"]]
            if names:
                lines.append("三檔共賣池：" + "、".join(names))
            else:
                lines.append("三檔共賣池：類股有賣壓，但沒有同一檔股票形成 3/3 共識")
            lines.append("")
    else:
        lines.extend(["目前沒有已確認的減碼類股。", ""])
    pool_names = []
    for row in sectors:
        for stock in row["stocks_all_three"]:
            if stock["name"] not in pool_names:
                pool_names.append(stock["name"])
    buy_summary = "、".join(pool_names[:6]) if pool_names else "目前沒有 3/3 同股買盤共識"
    sell_names = ([warning["category"]] if warning else []) + [
        row["category"] for row in selling
    ]
    sell_summary = "、".join(dict.fromkeys(sell_names)) or "目前沒有近期減碼警示"
    lines.append(f"一句話｜紅：{buy_summary}｜綠：{sell_summary}")
    return "\n".join(lines).strip()


def generate() -> dict:
    data = _load(SOURCE)
    if data.get("schema_version") != 2:
        raise RuntimeError("tag_flow.json schema_version must be 2")
    rotation = build_rotation_snapshot(
        data, ETFS, chart_days=LOOKBACK, stock_pool_limit=MAX_STOCKS
    )
    rows = rotation["rows"]
    selected = [
        row for row in rows
        if row["phase_group"] == "buy" and row["confidence"] in {"高", "中"}
    ][:MAX_SECTORS]
    selling = [
        row for row in rows
        if row["phase_group"] == "sell" and row["confidence"] in {"高", "中"}
    ][:MAX_SELL_SECTORS]
    warning_rows = [row for row in rows if row.get("current_sell_alert")]
    warning = min(
        warning_rows,
        key=lambda row: float(row.get("fast", 0.0)),
        default=None,
    )
    line_text = _render_line(rotation["as_of"], selected, selling, warning)
    payload = {
        "schema_version": 2,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_generated": data.get("generated"),
        "as_of": rotation["as_of"],
        "window": {
            "dates": rotation["dates"][-LOOKBACK:],
            "display_only": True,
            "etfs": ETFS,
        },
        "rotation": rotation["methodology"],
        "methodology": (
            "category-only and window-independent; current pressure / underlying direction / "
            "background use EWMA half-lives 3 / 10 / 20 over every common session; "
            "magnitude is relative to the category's own prior pressure history; direction "
            "requires at least 2 ETFs and a phase change needs 2 consecutive sessions; "
            "the ten dated bars are display-only; stock pools require all 3 ETFs"
        ),
        "sectors": selected,
        "selling": selling,
        "warning": warning,
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
        f"[theme-insight] as_of={payload['as_of']} common_sessions={rotation['history_sessions']} "
        f"confirmed_buy={len(selected)} "
        f"leaders={','.join(row['category'] for row in selected) or 'none'} "
        f"confirmed_sell={','.join(row['category'] for row in selling) or 'none'} "
        f"current_pressure_warning={warning['category'] if warning else 'none'}"
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

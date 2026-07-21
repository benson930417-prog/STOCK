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
LOOKBACK = 5
RECENT_DAYS = 2
MAX_SECTORS = 3
MAX_STOCKS = 5
EPSILON = 1e-6
MIN_STRENGTH = 0.05
MIN_ACCELERATION = 0.005

CARD_WIDTH = 1200
CARD_HEIGHT = 1500
CARD_BG = (8, 13, 23)
CARD_PANEL = (18, 26, 39)
CARD_PANEL_ALT = (22, 31, 46)
CARD_LINE = (42, 55, 74)
CARD_TEXT = (244, 247, 252)
CARD_MUTED = (154, 166, 184)
CARD_RED = (239, 68, 68)
CARD_RED_SOFT = (72, 29, 35)
CARD_AMBER = (245, 158, 11)
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
    "badge": _font(21, True),
    "body": _font(25),
    "body_bold": _font(25, True),
    "chip": _font(24, True),
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


def _draw_trend_arrow(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    points = [(x, y + 38), (x + 30, y + 10), (x + 54, y + 28), (x + 92, y - 10)]
    draw.line(points, fill=CARD_RED, width=8, joint="curve")
    draw.polygon(
        [(x + 92, y - 10), (x + 70, y - 4), (x + 87, y + 13)],
        fill=CARD_RED,
    )


def _draw_sector_card(
    draw: ImageDraw.ImageDraw,
    row: dict,
    rank: int,
    top: int,
) -> None:
    left, right = 54, CARD_WIDTH - 54
    bottom = top + 276
    _rounded(draw, (left, top, right, bottom), 24, CARD_PANEL, CARD_LINE, 2)
    draw.rounded_rectangle((left, top, left + 9, bottom), radius=5, fill=CARD_RED)

    rank_box = (left + 28, top + 28, left + 92, top + 92)
    _rounded(draw, rank_box, 18, CARD_RED_SOFT, CARD_RED, 2)
    rank_text = str(rank)
    rank_w = _text_width(draw, rank_text, CARD_FONTS["rank"])
    draw.text(
        (rank_box[0] + (64 - rank_w) / 2, top + 34),
        rank_text,
        font=CARD_FONTS["rank"],
        fill=CARD_RED,
    )

    draw.text(
        (left + 116, top + 28),
        row["category"],
        font=CARD_FONTS["sector"],
        fill=CARD_TEXT,
    )
    badge_text = "強勢 × 加速"
    badge_w = _text_width(draw, badge_text, CARD_FONTS["badge"]) + 40
    badge_box = (right - badge_w - 132, top + 32, right - 132, top + 76)
    _rounded(draw, badge_box, 18, CARD_RED_SOFT, None)
    draw.text(
        (badge_box[0] + 20, top + 41),
        badge_text,
        font=CARD_FONTS["badge"],
        fill=CARD_RED,
    )
    _draw_trend_arrow(draw, right - 112, top + 46)

    reason = _sector_reason(row, LOOKBACK)
    reason_lines = _wrap_chars(draw, reason, CARD_FONTS["body"], right - left - 170)
    for index, line in enumerate(reason_lines):
        draw.text(
            (left + 116, top + 96 + index * 37),
            line,
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )

    label_y = top + 188
    draw.text(
        (left + 116, label_y),
        "三檔共買池",
        font=CARD_FONTS["body_bold"],
        fill=CARD_TEXT,
    )
    chip_x = left + 300
    names = [stock["name"] for stock in row.get("stocks_all_three", [])]
    if not names:
        draw.text(
            (chip_x, label_y),
            "尚未形成同股共識",
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
                CARD_RED_SOFT,
                CARD_RED,
                1,
            )
            draw.text(
                (chip_x + 21, label_y + 1),
                name,
                font=CARD_FONTS["chip"],
                fill=CARD_TEXT,
            )
            chip_x += chip_w + 12


def _render_card(payload: dict) -> tuple[Path, Path]:
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), CARD_BG)
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
        "抓主線，不看雜訊｜403 · 981 · 991｜紅色代表同步加碼",
        font=CARD_FONTS["subtitle"],
        fill=CARD_MUTED,
    )

    sectors = payload.get("sectors", [])[:MAX_SECTORS]
    start_y = 225
    if sectors:
        for index, row in enumerate(sectors, 1):
            _draw_sector_card(draw, row, index, start_y + (index - 1) * 294)
    else:
        _rounded(
            draw,
            (54, start_y, CARD_WIDTH - 54, start_y + 360),
            24,
            CARD_PANEL,
            CARD_LINE,
            2,
        )
        draw.text(
            (90, start_y + 70),
            "目前沒有明確主線",
            font=CARD_FONTS["sector"],
            fill=CARD_TEXT,
        )
        lines = _wrap_chars(
            draw,
            "沒有類股同時符合淨加碼、買盤加速與多數 ETF 同向。先觀察，不把單一 ETF 換股當成共識。",
            CARD_FONTS["body"],
            CARD_WIDTH - 180,
            3,
        )
        for index, line in enumerate(lines):
            draw.text(
                (90, start_y + 140 + index * 40),
                line,
                font=CARD_FONTS["body"],
                fill=CARD_MUTED,
            )

    cooling = payload.get("cooling")
    cooling_y = 1120
    if cooling:
        _rounded(
            draw,
            (54, cooling_y, CARD_WIDTH - 54, cooling_y + 112),
            22,
            CARD_PANEL_ALT,
            CARD_LINE,
            2,
        )
        draw.rounded_rectangle((54, cooling_y, 63, cooling_y + 112), radius=5, fill=CARD_AMBER)
        draw.text(
            (84, cooling_y + 24),
            "降溫提醒",
            font=CARD_FONTS["badge"],
            fill=CARD_AMBER,
        )
        draw.text(
            (225, cooling_y + 19),
            cooling["category"],
            font=CARD_FONTS["cooling"],
            fill=CARD_TEXT,
        )
        draw.text(
            (225, cooling_y + 61),
            "仍有買盤，但近期力道已放慢。",
            font=CARD_FONTS["body"],
            fill=CARD_MUTED,
        )

    conclusion_y = 1254
    _rounded(
        draw,
        (54, conclusion_y, CARD_WIDTH - 54, conclusion_y + 136),
        24,
        CARD_RED_SOFT,
        CARD_RED,
        2,
    )
    draw.text(
        (84, conclusion_y + 22),
        "今日優先觀察",
        font=CARD_FONTS["badge"],
        fill=CARD_RED,
    )
    pool_names: list[str] = []
    for row in sectors:
        for stock in row.get("stocks_all_three", []):
            if stock["name"] not in pool_names:
                pool_names.append(stock["name"])
    conclusion = "、".join(pool_names[:6]) if pool_names else "等待三檔 ETF 形成同股共識"
    conclusion_lines = _wrap_chars(
        draw, conclusion, CARD_FONTS["cooling"], CARD_WIDTH - 170, 2
    )
    for index, line in enumerate(conclusion_lines):
        draw.text(
            (84, conclusion_y + 62 + index * 40),
            line,
            font=CARD_FONTS["cooling"],
            fill=CARD_TEXT,
        )

    footer = "類股 only · 近 5 個共同交易日 · 最近 2 日高於前 3 日 · 共買池需 3/3 ETF"
    footer_w = _text_width(draw, footer, CARD_FONTS["footer"])
    draw.text(
        ((CARD_WIDTH - footer_w) / 2, 1435),
        footer,
        font=CARD_FONTS["footer"],
        fill=CARD_SLATE,
    )
    note = "此卡是資金行為摘要，不是投資建議"
    note_w = _text_width(draw, note, CARD_FONTS["footer"])
    draw.text(
        ((CARD_WIDTH - note_w) / 2, 1467),
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

        stock_pool = []
        for stock in sector["stocks"].values():
            stock_by_etf = dict(stock["by_etf"])
            buyers = sum(stock_by_etf.get(etf, 0.0) > EPSILON for etf in ETFS)
            stock_strength = sum(
                stock_by_etf.get(etf, 0.0) for etf in ETFS
            ) / len(ETFS)
            if buyers == len(ETFS) and stock_strength > EPSILON:
                stock_pool.append(
                    {
                        "id": stock["id"],
                        "name": stock["name"],
                        "strength": round(stock_strength, 4),
                    }
                )
        stock_pool.sort(key=lambda row: -row["strength"])

        results.append(
            {
                "category": sector["category"],
                "strength": round(strength, 4),
                "acceleration": round(acceleration, 4),
                "buyers": sum(by_etf.get(etf, 0.0) > EPSILON for etf in ETFS),
                "buy_days": sum(value > EPSILON for value in daily),
                "latest_positive": daily[-1] > EPSILON,
                "stocks_all_three": stock_pool[:MAX_STOCKS],
            }
        )
    return results


def _sector_reason(row: dict, n_dates: int) -> str:
    if row["buyers"] == 3:
        breadth = "三檔主動 ETF 同步加碼"
    else:
        breadth = "多數主動 ETF 同向加碼"
    persistence = (
        "買盤具持續性" if row["buy_days"] >= max(3, n_dates - 1)
        else "近期買盤轉強"
    )
    return f"{breadth}，{persistence}，而且最近兩日力道高於前三日。"


def _render_line(as_of: str, sectors: list[dict], cooling: dict | None) -> str:
    lines = [
        "🔥 吳大師｜ETF 類股洞察",
        f"截至 {as_of}｜近 5 個共同交易日",
        "",
    ]
    if not sectors:
        lines.extend(
            [
                "目前沒有同時符合『淨加碼＋正在加速＋至少兩檔 ETF 同向』的明確主線。",
                "結論：先觀察，不把單一 ETF 的換股誤認成市場共識。",
            ]
        )
        return "\n".join(lines)

    for index, row in enumerate(sectors, 1):
        lines.append(f"主線 {index}｜{row['category']}：強勢加速")
        lines.append(_sector_reason(row, LOOKBACK))
        names = [stock["name"] for stock in row["stocks_all_three"]]
        if names:
            lines.append("三檔共買池：" + "、".join(names))
        else:
            lines.append("三檔共買池：暫無同一檔個股獲三檔同步加碼")
        lines.append("")

    if cooling:
        lines.append(
            f"降溫提醒｜{cooling['category']}：仍有買盤，但近期力道已放慢。"
        )
        lines.append("")
    pool_names = []
    for row in sectors:
        for stock in row["stocks_all_three"]:
            if stock["name"] not in pool_names:
                pool_names.append(stock["name"])
    if pool_names:
        lines.append("一句話：優先追蹤三檔 ETF 同步買進的「" + "、".join(pool_names[:6]) + "」，其餘只列觀察。")
    else:
        lines.append("一句話：類股方向正在轉強，但尚未形成同一檔個股的三方共識，先觀察。")
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
    cooling_rows = [
        row for row in rows
        if row["strength"] > MIN_STRENGTH
        and row["acceleration"] < -MIN_ACCELERATION
        and row["buyers"] >= 2
    ]
    cooling = max(cooling_rows, key=lambda row: row["strength"], default=None)
    line_text = _render_line(selected_dates[-1], selected, cooling)
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
            "accelerating = latest 2-session daily average above prior 3; "
            "stock pool requires positive normalized flow from all 3 ETFs"
        ),
        "sectors": selected,
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
        f"leaders={','.join(row['category'] for row in selected) or 'none'}"
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

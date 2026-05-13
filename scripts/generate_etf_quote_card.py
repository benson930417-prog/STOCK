import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
IMAGE_DIR = DATA_DIR / "images"
QUOTE_CACHE_DIR = DATA_DIR / "quote_cache"

ETF_NAMES = {
    "00997A": "主動群益美國增長",
}

RED = (198, 36, 0)
GREEN = (37, 140, 24)
INK = (17, 24, 39)
MUTED = (102, 112, 128)
SOFT = (245, 247, 250)
LINE = (220, 226, 232)


def _font(size, weight="regular"):
    candidates = []
    if os.name == "nt":
        candidates.extend([
            r"C:\Windows\Fonts\msjh.ttc",
            r"C:\Windows\Fonts\msjhbd.ttc",
            r"C:\Windows\Fonts\NotoSansTC-Regular.otf",
        ])
    candidates.extend([
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ])
    if weight == "bold":
        candidates = [
            path.replace("Regular", "Bold").replace("msjh.ttc", "msjhbd.ttc")
            for path in candidates
        ] + candidates
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


FONTS = {
    "title": _font(46, "bold"),
    "h2": _font(28, "bold"),
    "body": _font(22),
    "body_bold": _font(22, "bold"),
    "small": _font(18),
    "small_bold": _font(18, "bold"),
    "tiny": _font(15),
}


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _ago(value):
    dt = _parse_time(value)
    if not dt:
        return "----"
    seconds = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}hr ago"
    return f"{hours // 24}d ago"


def _fmt_pct(value):
    if value is None:
        return "----"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _color_for_pct(value):
    if value is None:
        return MUTED
    if value > 0:
        return RED
    if value < 0:
        return GREEN
    return MUTED


def _text(draw, xy, text, font, fill=INK, anchor=None):
    draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)


def _round_rect(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _measure(draw, text, font):
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0], box[3] - box[1]


def _fit_text(draw, text, font, max_width):
    text = str(text)
    if _measure(draw, text, font)[0] <= max_width:
        return text
    ellipsis = "..."
    while text and _measure(draw, text + ellipsis, font)[0] > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def _bar(draw, x, y, width, height, pct, scale):
    _round_rect(draw, (x, y, x + width, y + height), 5, (250, 251, 253))
    zero = x + width // 2
    draw.line((zero, y - 3, zero, y + height + 3), fill=(196, 204, 214), width=2)
    for marker in (-0.5, 0.5):
        mx = int(zero + marker * width / 2)
        draw.line((mx, y + height - 5, mx, y + height), fill=(218, 224, 230), width=1)
    if pct is None:
        return
    clamped = max(-scale, min(scale, pct))
    bar_w = int(abs(clamped) / scale * (width / 2))
    color = _color_for_pct(pct)
    if clamped >= 0:
        _round_rect(draw, (zero, y + 5, zero + bar_w, y + height - 5), 3, color)
    else:
        _round_rect(draw, (zero - bar_w, y + 5, zero, y + height - 5), 3, color)


def _draw_stat(draw, x, y, w, label, value, accent):
    _round_rect(draw, (x, y, x + w, y + 70), 14, (255, 255, 255), (232, 236, 241))
    _text(draw, (x + 18, y + 14), label, FONTS["tiny"], MUTED)
    _text(draw, (x + 18, y + 38), value, FONTS["small_bold"], accent)


def _draw_row(draw, row, x, y, w, rank, scale):
    change = row.get("day_change_pct")
    country = row.get("country") or "--"
    session = row.get("market_session") or "--"
    age = _ago(row.get("quote_time_utc"))
    pct_text = _fmt_pct(change)
    weight = row.get("weight_pct")
    weight_text = f"{weight:.2f}%" if weight is not None else "--"
    ticker = row.get("id") or "--"
    name = row.get("name") or "--"

    _round_rect(draw, (x, y, x + w, y + 55), 8, (255, 255, 255))
    _text(draw, (x + 10, y + 9), f"{rank:02d}", FONTS["tiny"], MUTED)
    _text(draw, (x + 48, y + 7), _fit_text(draw, name, FONTS["small_bold"], 185), FONTS["small_bold"], INK)
    _text(draw, (x + 48, y + 31), _fit_text(draw, ticker, FONTS["tiny"], 130), FONTS["tiny"], MUTED)
    _text(draw, (x + 260, y + 9), country, FONTS["tiny"], MUTED)
    _text(draw, (x + 312, y + 9), weight_text, FONTS["tiny"], MUTED)
    _text(draw, (x + 395, y + 9), session, FONTS["tiny"], MUTED)
    _text(draw, (x + 455, y + 7), pct_text, FONTS["small_bold"], _color_for_pct(change))
    _text(draw, (x + w - 12, y + 10), age, FONTS["tiny"], MUTED, anchor="ra")
    _bar(draw, x + 260, y + 31, w - 275, 18, change, scale)


def generate_quote_card(ticker="00997A"):
    ticker = ticker.upper()
    cache_path = QUOTE_CACHE_DIR / f"etf_{ticker}_quotes.json"
    with cache_path.open("r", encoding="utf-8") as fh:
        cache = json.load(fh)

    rows = sorted(
        cache.get("holdings", []),
        key=lambda item: item.get("weight_pct") if item.get("weight_pct") is not None else -1,
        reverse=True,
    )
    rows = rows[:50]
    valid_changes = [abs(row["day_change_pct"]) for row in rows if row.get("day_change_pct") is not None]
    max_abs = max(valid_changes) if valid_changes else 5
    scale = max(5, min(30, int(math.ceil(max_abs / 5.0) * 5)))

    width = 1500
    height = 1980
    img = Image.new("RGB", (width, height), (246, 248, 251))
    draw = ImageDraw.Draw(img)

    _round_rect(draw, (28, 28, width - 28, height - 28), 28, (255, 255, 255), (224, 230, 237), 2)

    title = f"{ticker} {ETF_NAMES.get(ticker, '')}".strip()
    _text(draw, (72, 74), title, FONTS["title"], INK)
    _text(draw, (74, 132), f"Holdings date: {cache.get('holdings_date', '----')}", FONTS["body_bold"], MUTED)

    composite = cache.get("composite_move_pct")
    comp_text = _fmt_pct(composite)
    comp_color = _color_for_pct(composite)
    _round_rect(draw, (1110, 70, 1428, 150), 20, (255, 244, 242) if composite and composite > 0 else (239, 249, 237), None)
    _text(draw, (1138, 88), "Composite move", FONTS["small_bold"], MUTED)
    _text(draw, (1410, 94), comp_text, FONTS["h2"], comp_color, anchor="ra")

    _draw_stat(draw, 74, 185, 250, "newest data", _ago(cache.get("newest_quote_utc")), RED)
    _draw_stat(draw, 342, 185, 250, "oldest data", _ago(cache.get("oldest_quote_utc")), MUTED)
    _draw_stat(draw, 610, 185, 250, "ETF data refresh", _ago(cache.get("etf_refresh_utc")), MUTED)

    counts = cache.get("counts", {})
    up = counts.get("up", 0)
    down = counts.get("down", 0)
    flat = counts.get("flat", 0)
    summary = f"Up {up} / Down {down} / Flat {flat}"
    _draw_stat(draw, 878, 185, 310, "market breadth", summary, INK)
    _draw_stat(draw, 1206, 185, 222, "x-axis scale", f"±{scale}%", MUTED)

    draw.line((74, 290, width - 74, 290), fill=LINE, width=2)
    _text(draw, (74, 318), "Ranked by ETF weight. Red = up, green = down. Missing Yahoo data shows ----.", FONTS["small"], MUTED)

    left_x = 74
    right_x = 762
    col_w = 664
    start_y = 365
    row_h = 59

    _text(draw, (left_x + 260, 350), f"-{scale}%        0        +{scale}%", FONTS["tiny"], MUTED)
    _text(draw, (right_x + 260, 350), f"-{scale}%        0        +{scale}%", FONTS["tiny"], MUTED)

    for idx, row in enumerate(rows[:25]):
        _draw_row(draw, row, left_x, start_y + idx * row_h, col_w, idx + 1, scale)
    for idx, row in enumerate(rows[25:50]):
        _draw_row(draw, row, right_x, start_y + idx * row_h, col_w, idx + 26, scale)

    footer = f"Generated {_ago(cache.get('generated_utc'))} from server quote cache"
    _text(draw, (width // 2, height - 58), footer, FONTS["tiny"], MUTED, anchor="ma")

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = IMAGE_DIR / f"etf_{ticker}_quote_card.jpg"
    img.save(output_path, "JPEG", quality=92, optimize=True)
    return output_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", nargs="?", default="00997A")
    args = parser.parse_args()
    print(generate_quote_card(args.ticker))

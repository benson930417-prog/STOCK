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
PANEL = (255, 255, 255)
WASH = (248, 250, 252)


def _font(size, weight="regular"):
    candidates = []
    if os.name == "nt":
        candidates.extend([
            r"C:\Windows\Fonts\msjh.ttc",
            r"C:\Windows\Fonts\msjhbd.ttc",
            r"C:\Windows\Fonts\NotoSansTC-Regular.otf",
        ])
    candidates.extend([
        str(DATA_DIR / "fonts" / "NotoSansCJK-Regular.ttc"),
        str(DATA_DIR / "fonts" / "NotoSansCJK-Bold.ttc"),
        str(DATA_DIR / "fonts" / "NotoSansTC-Regular.otf"),
        str(DATA_DIR / "fonts" / "NotoSansTC-Bold.otf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Bold.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
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
    "title_small": _font(34, "bold"),
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
        return f"{seconds}秒前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分鐘前"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}小時前"
    return f"{hours // 24}天前"


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
    _round_rect(draw, (x, y, x + width, y + height), 6, (249, 251, 253))
    zero = x + width // 2
    draw.line((zero, y - 3, zero, y + height + 3), fill=(196, 204, 214), width=2)
    for marker in (-0.5, 0.5):
        mx = int(zero + marker * width / 2)
        draw.line((mx, y + 4, mx, y + height - 4), fill=(224, 229, 235), width=1)
    if pct is None:
        return
    clamped = max(-scale, min(scale, pct))
    bar_w = int(abs(clamped) / scale * (width / 2))
    color = _color_for_pct(pct)
    if clamped >= 0:
        _round_rect(draw, (zero, y + 6, zero + bar_w, y + height - 6), 4, color)
    else:
        _round_rect(draw, (zero - bar_w, y + 6, zero, y + height - 6), 4, color)


def _draw_stat(draw, x, y, w, label, value, accent):
    _round_rect(draw, (x, y, x + w, y + 70), 14, PANEL, (229, 234, 240))
    _text(draw, (x + 18, y + 14), label, FONTS["tiny"], MUTED)
    _text(draw, (x + 18, y + 38), value, FONTS["small_bold"], accent)


def _session_fill(session):
    return {
        "PRE": (255, 247, 237),
        "REG": (239, 246, 255),
        "POST": (245, 243, 255),
        "CLOSE": (243, 244, 246),
    }.get(session, (243, 244, 246))


def _session_text(session):
    return {
        "PRE": (180, 83, 9),
        "REG": (37, 99, 235),
        "POST": (109, 40, 217),
        "CLOSE": (75, 85, 99),
    }.get(session, MUTED)


def _session_label(session):
    return {
        "PRE": "盤前",
        "REG": "盤中",
        "POST": "盤後",
        "CLOSE": "收盤",
    }.get(session, "--")


def _draw_session(draw, x, y, session):
    session = session or "--"
    _round_rect(draw, (x, y, x + 66, y + 28), 14, _session_fill(session))
    _text(draw, (x + 33, y + 14), _session_label(session), FONTS["tiny"], _session_text(session), anchor="mm")


def _draw_col_header(draw, x, y, w, scale):
    _text(draw, (x + 48, y), "持股", FONTS["tiny"], MUTED)
    _text(draw, (x + 276, y), "市場", FONTS["tiny"], MUTED)
    _text(draw, (x + 328, y), "權重", FONTS["tiny"], MUTED)
    _text(draw, (x + 414, y), "狀態", FONTS["tiny"], MUTED)
    _text(draw, (x + 504, y), "漲跌", FONTS["tiny"], MUTED)
    _text(draw, (x + w - 12, y), "更新", FONTS["tiny"], MUTED, anchor="ra")


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

    draw.line((x, y + 92, x + w, y + 92), fill=(236, 240, 245), width=1)
    _text(draw, (x + 10, y + 13), f"{rank:02d}", FONTS["tiny"], MUTED)
    _text(draw, (x + 48, y + 7), _fit_text(draw, name, FONTS["small_bold"], 185), FONTS["small_bold"], INK)
    _text(draw, (x + 48, y + 31), _fit_text(draw, ticker, FONTS["tiny"], 130), FONTS["tiny"], MUTED)
    _text(draw, (x + 276, y + 11), country, FONTS["tiny"], MUTED)
    _text(draw, (x + 328, y + 11), weight_text, FONTS["tiny"], MUTED)
    _draw_session(draw, x + 402, y + 7, session)
    _text(draw, (x + 506, y + 8), pct_text, FONTS["small_bold"], _color_for_pct(change))
    _text(draw, (x + w - 12, y + 12), age, FONTS["tiny"], MUTED, anchor="ra")
    _bar(draw, x + 48, y + 61, w - 60, 22, change, scale)


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
    height = 3160
    img = Image.new("RGB", (width, height), (241, 244, 248))
    draw = ImageDraw.Draw(img)

    _round_rect(draw, (28, 28, width - 28, height - 28), 28, PANEL, (221, 228, 236), 2)
    _round_rect(draw, (54, 54, width - 54, 274), 24, (250, 252, 255), (234, 238, 244), 1)

    title = f"{ticker} {ETF_NAMES.get(ticker, '')}".strip()
    title_font = FONTS["title"] if _measure(draw, title, FONTS["title"])[0] < 880 else FONTS["title_small"]
    _text(draw, (82, 78), title, title_font, INK)
    _text(draw, (84, 138), f"持股日期：{cache.get('holdings_date', '----')}", FONTS["body_bold"], MUTED)

    composite = cache.get("composite_move_pct")
    comp_text = _fmt_pct(composite)
    comp_color = _color_for_pct(composite)
    comp_fill = (255, 244, 242) if composite and composite > 0 else (239, 249, 237)
    _round_rect(draw, (1110, 74, 1418, 154), 20, comp_fill, None)
    _text(draw, (1138, 92), "加權漲跌", FONTS["small_bold"], MUTED)
    _text(draw, (1398, 98), comp_text, FONTS["h2"], comp_color, anchor="ra")

    _draw_stat(draw, 84, 184, 250, "最新報價", _ago(cache.get("newest_quote_utc")), RED)
    _draw_stat(draw, 352, 184, 250, "最舊報價", _ago(cache.get("oldest_quote_utc")), MUTED)
    _draw_stat(draw, 620, 184, 250, "權重更新", _ago(cache.get("etf_refresh_utc")), MUTED)

    counts = cache.get("counts", {})
    up = counts.get("up", 0)
    down = counts.get("down", 0)
    flat = counts.get("flat", 0)
    summary = f"上漲 {up} / 下跌 {down} / 持平 {flat}"
    _draw_stat(draw, 888, 184, 310, "漲跌家數", summary, INK)
    _draw_stat(draw, 1216, 184, 202, "刻度範圍", f"±{scale}%", MUTED)

    draw.line((74, 302, width - 74, 302), fill=LINE, width=2)
    _text(draw, (74, 330), "依ETF持股權重排序，紅色代表上漲、綠色代表下跌；無報價資料以 ---- 表示。", FONTS["small"], MUTED)

    left_x = 74
    right_x = 762
    col_w = 664
    header_y = 375
    start_y = 440
    row_h = 104

    _draw_col_header(draw, left_x, header_y, col_w, scale)
    _draw_col_header(draw, right_x, header_y, col_w, scale)

    for idx, row in enumerate(rows[:25]):
        _draw_row(draw, row, left_x, start_y + idx * row_h, col_w, idx + 1, scale)
    for idx, row in enumerate(rows[25:50]):
        _draw_row(draw, row, right_x, start_y + idx * row_h, col_w, idx + 26, scale)

    footer = f"報表產生：{_ago(cache.get('generated_utc'))}"
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

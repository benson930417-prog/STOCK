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
    "00981A": "主動統一台股增長",
    "00997A": "主動群益美國增長",
    "0050": "元大台灣50",
    "00830": "國泰費城半導體",
    "00878": "國泰永續高股息",
    "MASTER": "吳大師展開持股",
}

RED = (198, 36, 0)
GREEN = (37, 140, 24)
INK = (17, 24, 39)
MUTED = (102, 112, 128)
SOFT = (245, 247, 250)
LINE = (220, 226, 232)
PANEL = (255, 255, 255)
WASH = (248, 250, 252)
HOLDING_TEXT_W = 400
FLAG_W = 61
FLAG_H = 40
INDEX_COL_W = 100
FLAG_COL_W = 90
NAME_COL_W = 500
WEIGHT_COL_W = 132
UPDATE_COL_W = 200
STATUS_COL_W = 180


def _font(size, weight="regular"):
    candidates = []
    if os.name == "nt":
        candidates.extend([
            r"C:\Windows\Fonts\seguiemj.ttf",
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
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
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
    "title": _font(64, "bold"),
    "title_small": _font(48, "bold"),
    "h2": _font(40, "bold"),
    "rank": _font(60, "bold"),
    "body": _font(30),
    "body_bold": _font(30, "bold"),
    "small": _font(24),
    "small_bold": _font(24, "bold"),
    "flag": _font(30),
    "tiny": _font(20),
    "tiny_bold": _font(20, "bold"),
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


def _composite_title(cache):
    if cache.get("composite_mode") != "live":
        return None
    prefix = "即時加權"
    scope = cache.get("composite_country_scope") or "--"
    scope = str(scope).replace("台積電期貨", "期")
    return f"{prefix}({scope}):"


def _composite_detail(cache):
    if cache.get("composite_mode") != "live":
        return None
    count = cache.get("composite_holding_count") or 0
    weight = cache.get("composite_weight_pct")
    weight_text = "--" if weight is None else f"{float(weight):.1f}%"
    return f"交易中{count}檔・權重{weight_text}"


def _draw_country_flag(draw, x, y, country):
    country = str(country or "").upper()
    w, h = FLAG_W, FLAG_H
    _round_rect(draw, (x, y, x + w, y + h), 4, PANEL, (190, 198, 208), 1)
    sx = w / 46
    sy = h / 30

    def px(value):
        return x + int(round(value * sx))

    def py(value):
        return y + int(round(value * sy))

    if country == "US":
        stripe_h = h / 7
        for i in range(7):
            color = (191, 10, 48) if i % 2 == 0 else PANEL
            draw.rectangle((x + 1, y + int(i * stripe_h), x + w - 1, y + int((i + 1) * stripe_h)), fill=color)
        draw.rectangle((x + 1, y + 1, px(21), py(16)), fill=(0, 40, 104))
    elif country == "TW":
        draw.rectangle((x + 1, y + 1, x + w - 1, y + h - 1), fill=(254, 0, 0))
        draw.rectangle((x + 1, y + 1, px(23), py(16)), fill=(0, 0, 149))
        draw.ellipse((px(9), py(5), px(15), py(11)), fill=PANEL)
    elif country == "JP":
        draw.rectangle((x + 1, y + 1, x + w - 1, y + h - 1), fill=PANEL)
        draw.ellipse((px(16), py(7), px(30), py(21)), fill=(188, 0, 45))
    elif country == "HK":
        draw.rectangle((x + 1, y + 1, x + w - 1, y + h - 1), fill=(222, 41, 16))
        draw.ellipse((px(17), py(9), px(29), py(21)), fill=PANEL)
    else:
        _text(draw, (x + w / 2, y + h / 2), country[:2] or "--", FONTS["tiny_bold"], INK, anchor="mm")


def _color_for_pct(value):
    if value is None:
        return INK
    if value > 0:
        return RED
    if value < 0:
        return GREEN
    return INK


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
    _round_rect(draw, (x, y, x + width, y + height), 6, (232, 237, 243))
    zero = x + width // 2
    draw.line((zero, y - 3, zero, y + height + 3), fill=(196, 204, 214), width=2)
    for marker in (-0.5, 0.5):
        mx = int(zero + marker * width / 2)
        draw.line((mx, y + 4, mx, y + height - 4), fill=(204, 212, 222), width=1)
    if pct is None:
        return zero
    clamped = max(-scale, min(scale, pct))
    bar_w = int(abs(clamped) / scale * (width / 2))
    color = _color_for_pct(pct)
    if clamped >= 0:
        _round_rect(draw, (zero, y + 6, zero + bar_w, y + height - 6), 4, color)
        return zero + bar_w
    else:
        _round_rect(draw, (zero - bar_w, y + 6, zero, y + height - 6), 4, color)
        return zero - bar_w


def _draw_stat(draw, x, y, w, label, value, accent):
    _round_rect(draw, (x, y, x + w, y + 140), 18, PANEL, (225, 231, 239))
    label_font = FONTS["h2"]
    _text(draw, (x + 20, y + 18), _fit_text(draw, label, label_font, w - 40), label_font, INK)
    value_font = FONTS["body_bold"]
    _text(draw, (x + 20, y + 82), _fit_text(draw, value, value_font, w - 40), value_font, accent)


def _session_fill(session):
    return {
        "PRE": (255, 237, 213),
        "REG": (239, 246, 255),
        "POST": (237, 233, 254),
        "FUT_NIGHT": (224, 242, 254),
        "FUT_NIGHT_CLOSE": (226, 232, 240),
        "POST_CLOSE": (229, 231, 235),
        "CLOSE": (229, 231, 235),
    }.get(session, (229, 231, 235))


def _session_outline(session):
    return {
        "PRE": (251, 146, 60),
        "REG": (37, 99, 235),
        "POST": (139, 92, 246),
        "FUT_NIGHT": (6, 182, 212),
        "FUT_NIGHT_CLOSE": (100, 116, 139),
        "POST_CLOSE": (156, 163, 175),
        "CLOSE": (156, 163, 175),
    }.get(session, (156, 163, 175))


def _session_text(session):
    return {
        "PRE": (154, 52, 18),
        "REG": (29, 78, 216),
        "POST": (91, 33, 182),
        "FUT_NIGHT": (14, 116, 144),
        "FUT_NIGHT_CLOSE": INK,
        "POST_CLOSE": INK,
        "CLOSE": INK,
    }.get(session, INK)


def _session_label(session, country=None):
    session = str(session or "").upper()
    country = str(country or "").upper()
    if country in {"TW", "JP", "HK"}:
        if session == "REG":
            return "盤中"
        if session in {"CLOSE", "POST_CLOSE"}:
            return "已收盤"
    labels = {
        "PRE": "盤前",
        "REG": "盤中",
        "POST": "盤後",
        "FUT_NIGHT": "夜盤中",
        "FUT_NIGHT_CLOSE": "夜盤收",
        "POST_CLOSE": "盤後收",
        "CLOSE": "盤後收",
    }
    return labels.get(session, "--")


def _draw_session(draw, x, y, session, country=None, pill_w=112, pill_h=46, font=None):
    session = session or "--"
    label = _session_label(session, country)
    font = font or FONTS["small_bold"]
    _round_rect(draw, (x, y, x + pill_w, y + pill_h), pill_h // 2, _session_fill(session), _session_outline(session), 2)
    _text(draw, (x + pill_w / 2, y + pill_h / 2), label, font, _session_text(session), anchor="mm")


def _draw_col_header(draw, x, y, w, scale):
    index_x = x
    flag_x = index_x + INDEX_COL_W
    name_x = flag_x + FLAG_COL_W
    weight_x = name_x + NAME_COL_W
    status_x = x + w - STATUS_COL_W
    update_x = status_x - UPDATE_COL_W
    _text(draw, (index_x, y), "序號", FONTS["body_bold"], INK)
    _text(draw, (flag_x, y), "市場", FONTS["body_bold"], INK)
    _text(draw, (name_x, y), "持股", FONTS["body_bold"], INK)
    _text(draw, (weight_x, y), "權重", FONTS["body_bold"], INK)
    _text(draw, (update_x + UPDATE_COL_W - 8, y), "更新", FONTS["body_bold"], INK, anchor="ra")
    _text(draw, (status_x + STATUS_COL_W - 4, y), "狀態", FONTS["body_bold"], INK, anchor="ra")
    draw.line((x, y + 54, x + w, y + 54), fill=(209, 216, 224), width=3)


def _draw_row(draw, row, x, y, w, rank, scale):
    change = row.get("day_change_pct")
    country = row.get("country") or "--"
    session = row.get("market_session") or "--"
    session_key = str(session or "").upper()
    age = _ago(row.get("quote_time_utc"))
    pct_text = _fmt_pct(change)
    weight = row.get("weight_pct")
    weight_text = f"{weight:.2f}%" if weight is not None else "--"
    ticker = row.get("id") or "--"
    if row.get("proxy"):
        ticker = f"{ticker} / {row['proxy'].get('symbol', 'QFF1!')} 延遲15分"
    name = row.get("name") or "--"
    index_x = x
    flag_x = index_x + INDEX_COL_W
    name_x = flag_x + FLAG_COL_W
    weight_x = name_x + NAME_COL_W
    status_x = x + w - STATUS_COL_W
    update_x = status_x - UPDATE_COL_W

    draw.line((x, y + 128, x + w, y + 128), fill=(232, 237, 243), width=2)
    _text(draw, (index_x, y + 4), f"{rank:02d}", FONTS["rank"], INK)
    _draw_country_flag(draw, flag_x, y + 32, country)
    _text(draw, (name_x, y + 10), _fit_text(draw, name, FONTS["body_bold"], NAME_COL_W - 18), FONTS["body_bold"], INK)
    _text(draw, (name_x, y + 54), _fit_text(draw, ticker, FONTS["small"], NAME_COL_W - 18), FONTS["small"], INK)
    _text(draw, (weight_x, y + 21), weight_text, FONTS["body_bold"], INK)
    _text(draw, (update_x + UPDATE_COL_W - 8, y + 21), age, FONTS["body_bold"], INK, anchor="ra")
    _draw_session(
        draw,
        status_x + STATUS_COL_W - 166,
        y + 12,
        session,
        country,
        pill_w=166,
        pill_h=58,
        font=FONTS["body_bold"],
    )
    bar_x = name_x
    bar_y = y + 90
    bar_w = w - (bar_x - x)
    endpoint = _bar(draw, bar_x, bar_y, bar_w, 30, change, scale)
    if change is not None:
        pct_color = _color_for_pct(change)
        pct_w, _ = _measure(draw, pct_text, FONTS["body_bold"])
        bar_end = bar_x + bar_w
        if change >= 0:
            tx = min(endpoint + 12, bar_end + 12)
            anchor = None
        else:
            tx = max(endpoint - 12, bar_x + pct_w + 2)
            anchor = "ra"
        _text(draw, (tx, bar_y - 9), pct_text, FONTS["body_bold"], pct_color, anchor=anchor)


def _draw_quote_card_page(ticker, cache, rows, scale, page_no, total_pages):
    extra_stat_slot = 360
    width = 1500 + extra_stat_slot
    height = 4052
    img = Image.new("RGB", (width, height), (241, 244, 248))
    draw = ImageDraw.Draw(img)

    _round_rect(draw, (28, 28, width - 28, height - 28), 28, PANEL, (221, 228, 236), 2)
    _round_rect(draw, (54, 54, width - 54, 490), 24, (238, 242, 247), (209, 216, 224), 2)

    etf_name = cache.get("display_name") or ETF_NAMES.get(ticker, "")
    title = cache.get("display_ticker") or ticker
    _text(draw, (82, 68), title, FONTS["title"], INK)
    _text(draw, (82, 140), etf_name, FONTS["title_small"], INK)
    if cache.get("subtitle_parts"):
        cx = 84
        for part in cache["subtitle_parts"]:
            text = part["text"]
            color_name = part.get("color", "MUTED")
            color = RED if color_name == "RED" else (GREEN if color_name == "GREEN" else MUTED)
            _text(draw, (cx, 218), text, FONTS["body_bold"], color)
            w, _ = _measure(draw, text, FONTS["body_bold"])
            cx += w
    else:
        _text(draw, (84, 218), cache.get("subtitle") or f"持股日期：{cache.get('holdings_date', '----')}", FONTS["body_bold"], MUTED)
    _text(draw, (width - 92, 76), f"{page_no}/{total_pages}", FONTS["title"], MUTED, anchor="ra")
    counts = cache.get("counts", {})
    up = counts.get("up", 0)
    down = counts.get("down", 0)
    flat = counts.get("flat", 0)
    no_change = flat

    box_w = 210
    composite_box_w = 340
    gap = 16
    x0 = 84
    y0 = 328
    _draw_stat(draw, x0 + (box_w + gap) * 0, y0, box_w, "上漲", str(up), RED)
    _draw_stat(draw, x0 + (box_w + gap) * 1, y0, box_w, "下跌", str(down), GREEN)
    _draw_stat(draw, x0 + (box_w + gap) * 2, y0, box_w, "無變動", str(no_change), INK)
    _draw_stat(draw, x0 + (box_w + gap) * 3, y0, box_w, "最新報價", _ago(cache.get("newest_quote_utc")), RED)
    _draw_stat(draw, x0 + (box_w + gap) * 4, y0, box_w, "最舊報價", _ago(cache.get("oldest_quote_utc")), INK)
    _draw_stat(draw, x0 + (box_w + gap) * 5, y0, box_w, "權重更新", _ago(cache.get("etf_refresh_utc")), INK)
    composite_title = _composite_title(cache)
    if composite_title:
        _draw_stat(
            draw,
            x0 + (box_w + gap) * 6,
            y0,
            composite_box_w,
            composite_title,
            _fmt_pct(cache.get("composite_move_pct")),
            _color_for_pct(cache.get("composite_move_pct")),
        )

    draw.line((74, 528, width - 74, 528), fill=LINE, width=2)
    _text(draw, (74, 558), cache.get("sort_note") or "依ETF持股權重排序", FONTS["small_bold"], INK)
    _text(draw, (314, 558), "紅色代表上漲，綠色代表下跌；無報價資料以 ---- 表示。", FONTS["small_bold"], INK)

    x = 74
    col_w = width - 148
    header_y = 624
    start_y = 704
    row_h = 132

    _draw_col_header(draw, x, header_y, col_w, scale)

    rank_offset = (page_no - 1) * 25
    for idx, row in enumerate(rows):
        _draw_row(draw, row, x, start_y + idx * row_h, col_w, rank_offset + idx + 1, scale)

    return img


def generate_quote_card_from_cache(ticker, cache, output_prefix=None):
    ticker = str(ticker).upper()
    rows = cache.get("holdings", [])
    has_live_rows = any(row.get("is_live_market") for row in rows)
    if has_live_rows:
        all_rows = sorted(
            rows,
            key=lambda item: (
                bool(item.get("is_live_market")),
                item.get("weight_pct") if item.get("weight_pct") is not None else -1,
            ),
            reverse=True,
        )
    else:
        all_rows = sorted(
            rows,
            key=lambda item: item.get("weight_pct") if item.get("weight_pct") is not None else -1,
            reverse=True,
        )
    valid_changes = [abs(row["day_change_pct"]) for row in all_rows if row.get("day_change_pct") is not None]
    max_abs = max(valid_changes) if valid_changes else 5
    scale = max(5, min(30, int(math.ceil((max_abs * 1.2) / 5.0) * 5)))

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    output_paths = []
    pages = [all_rows[index:index + 25] for index in range(0, len(all_rows), 25)] or [[]]
    output_prefix = output_prefix or f"etf_{ticker}_quote_card"
    for index, page_rows in enumerate(pages, start=1):
        img = _draw_quote_card_page(ticker, cache, page_rows, scale, index, len(pages))
        output_path = IMAGE_DIR / f"{output_prefix}_{index}.jpg"
        img.save(output_path, "JPEG", quality=92, optimize=True)
        output_paths.append(output_path)
    return output_paths


def generate_quote_card(ticker="00997A"):
    ticker = ticker.upper()
    cache_path = QUOTE_CACHE_DIR / f"etf_{ticker}_quotes.json"
    with cache_path.open("r", encoding="utf-8") as fh:
        cache = json.load(fh)
    return generate_quote_card_from_cache(ticker, cache)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", nargs="?", default="00997A")
    args = parser.parse_args()
    for path in generate_quote_card(args.ticker):
        print(path)

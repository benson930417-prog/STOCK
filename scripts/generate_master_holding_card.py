import json
import math
import os
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from PIL import Image, ImageDraw, ImageFont


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.monitor_etf_quotes import fetch_yahoo_quotes

DATA_DIR = ROOT_DIR / "data"
IMAGE_DIR = DATA_DIR / "images"
QUOTE_CACHE_DIR = DATA_DIR / "quote_cache"
MASTER_PATH = DATA_DIR / "master_trades.csv"

SELL_FEE_RATE = 0.001425 * 0.28
SELL_TAX_RATE = 0.003
ETF_NAME_TO_TICKER = {
    "主動統一台股增長": "00981A",
    "主動群益美國增長": "00997A",
    "元大台灣50": "0050",
}
ETF_TICKER_TO_NAME = {v: k for k, v in ETF_NAME_TO_TICKER.items()}

RED = (198, 36, 0)
GREEN = (37, 140, 24)
INK = (17, 24, 39)
MUTED = (102, 112, 128)
LINE = (220, 226, 232)
PANEL = (255, 255, 255)


def _font(size, weight="regular"):
    candidates = []
    if os.name == "nt":
        candidates.extend([
            r"C:\Windows\Fonts\msjh.ttc",
            r"C:\Windows\Fonts\msjhbd.ttc",
        ])
    candidates.extend([
        str(DATA_DIR / "fonts" / "NotoSansCJK-Regular.ttc"),
        str(DATA_DIR / "fonts" / "NotoSansCJK-Bold.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
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
    "title": _font(62, "bold"),
    "h2": _font(38, "bold"),
    "rank": _font(56, "bold"),
    "body": _font(28),
    "body_bold": _font(28, "bold"),
    "small": _font(23),
    "small_bold": _font(23, "bold"),
    "tiny": _font(19),
    "tiny_bold": _font(19, "bold"),
}


def _to_float(value):
    if pd.isna(value):
        return 0.0
    return float(str(value).replace(",", "").strip() or 0)


def _to_int(value):
    if pd.isna(value):
        return 0
    return int(float(str(value).replace(",", "").strip() or 0))


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


def _fmt_money(value):
    if value is None:
        return "----"
    return f"{value:,.0f}"


def _fmt_pct(value):
    if value is None or pd.isna(value):
        return "----"
    return f"{value:+.2f}%"


def _color_for_pct(value):
    if value is None or pd.isna(value):
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
    if pct is None or pd.isna(pct):
        return zero
    clamped = max(-scale, min(scale, pct))
    bar_w = int(abs(clamped) / scale * (width / 2))
    color = _color_for_pct(pct)
    if clamped >= 0:
        _round_rect(draw, (zero, y + 6, zero + bar_w, y + height - 6), 4, color)
        return zero + bar_w
    _round_rect(draw, (zero - bar_w, y + 6, zero, y + height - 6), 4, color)
    return zero - bar_w


def _draw_stat(draw, x, y, w, label, value, accent=INK):
    _round_rect(draw, (x, y, x + w, y + 104), 18, PANEL, (225, 231, 239))
    _text(draw, (x + 18, y + 16), label, FONTS["small_bold"], INK)
    value_font = FONTS["body_bold"]
    if _measure(draw, value, value_font)[0] > w - 36:
        value_font = FONTS["small_bold"]
    _text(draw, (x + 18, y + 60), _fit_text(draw, value, value_font, w - 36), value_font, accent)


def load_master_trades():
    return pd.read_csv(MASTER_PATH, encoding="utf-8-sig")


def calculate_open_positions(raw_trades):
    df = raw_trades.copy()
    if df.empty:
        return pd.DataFrame()

    df["日期"] = pd.to_datetime(df["日期"])
    df["成交股數"] = df["成交股數"].apply(_to_int)
    df["淨收付金額"] = df["淨收付金額"].apply(_to_float)
    df["股名"] = df["股名"].astype(str).str.strip()
    df = df.sort_values(["股名", "日期"]).reset_index(drop=True)

    inventory = defaultdict(deque)
    for stock, sdf in df.groupby("股名", sort=False):
        for _, row in sdf.sort_values("日期").iterrows():
            qty = int(row["成交股數"])
            cash = float(row["淨收付金額"])
            if qty <= 0:
                continue
            if cash < 0:
                inventory[stock].append({"qty": qty, "cost": -cash})
            elif cash > 0:
                remaining = qty
                while remaining > 0 and inventory[stock]:
                    lot = inventory[stock][0]
                    take = min(remaining, lot["qty"])
                    ratio = take / lot["qty"] if lot["qty"] else 0
                    lot["qty"] -= take
                    lot["cost"] -= lot["cost"] * ratio
                    remaining -= take
                    if lot["qty"] <= 0:
                        inventory[stock].popleft()

    rows = []
    for stock, lots in inventory.items():
        qty = sum(int(lot["qty"]) for lot in lots)
        if qty <= 0:
            continue
        total_cost = sum(float(lot["cost"]) for lot in lots)
        rows.append({
            "stock": stock,
            "shares": qty,
            "cost": total_cost,
            "avg_cost": total_cost / qty if qty else 0.0,
            "ticker": ETF_NAME_TO_TICKER.get(stock),
        })
    return pd.DataFrame(rows).sort_values("cost", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def _stock_name_symbol_map():
    options = {}
    try:
        r1 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", verify=False, timeout=5)
        if r1.status_code == 200:
            for item in r1.json():
                code = str(item.get("Code", "")).strip()
                name = str(item.get("Name", "")).strip()
                if code and name:
                    options[name] = {"code": code, "symbol": f"{code}.TW"}
    except Exception:
        pass
    try:
        r2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", verify=False, timeout=5)
        if r2.status_code == 200:
            for item in r2.json():
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanyName", "")).strip()
                if code and name:
                    options[name] = {"code": code, "symbol": f"{code}.TWO"}
    except Exception:
        pass
    return options


def enrich_positions_with_quotes(positions):
    if positions.empty:
        return positions

    name_map = _stock_name_symbol_map()
    rows = []
    symbols = []
    for _, row in positions.iterrows():
        item = row.to_dict()
        ticker = item.get("ticker")
        if ticker:
            symbol, country, code = f"{ticker}.TW", "TW", ticker
        else:
            info = name_map.get(str(item.get("stock", "")).strip())
            symbol = info["symbol"] if info else None
            country = "TW" if info else None
            code = info["code"] if info else item.get("stock")
        item.update({"symbol": symbol, "country": country, "code": code})
        rows.append(item)
        if symbol:
            symbols.append((symbol, country))

    quotes = fetch_yahoo_quotes(symbols, max_workers=10)
    for item in rows:
        quote = quotes.get(item.get("symbol")) or {}
        price = quote.get("regularMarketPrice")
        day_pct = quote.get("regularMarketChangePercent")
        if price is None and item.get("ticker"):
            price = _latest_etf_close(item["ticker"])
        item["price"] = float(price) if price is not None else None
        item["day_change_pct"] = float(day_pct) if day_pct is not None else None
        item["quote_time_utc"] = quote.get("regularMarketTimeUtc")
        item["market_value"] = item["shares"] * item["price"] if item["price"] is not None else None
        item["est_sell_fee"] = item["market_value"] * SELL_FEE_RATE if item["market_value"] is not None else None
        item["est_sell_tax"] = item["market_value"] * SELL_TAX_RATE if item["market_value"] is not None else None
        item["liquidation_value"] = (
            item["market_value"] - item["est_sell_fee"] - item["est_sell_tax"]
            if item["market_value"] is not None else None
        )
        item["unrealized_pnl"] = item["liquidation_value"] - item["cost"] if item["liquidation_value"] is not None else None

    out = pd.DataFrame(rows)
    if "market_value" in out and out["market_value"].dropna().sum():
        out["weight_pct"] = out["market_value"] / out["market_value"].sum() * 100.0
    return out


def _latest_history_payload(ticker):
    path = DATA_DIR / ("passive_0050_history.json" if ticker == "0050" else f"etf_{ticker}_history.json")
    if not path.exists():
        return None, {}
    history = json.loads(path.read_text(encoding="utf-8"))
    date_key = max(history.keys())
    return date_key, history[date_key]


def _latest_etf_close(ticker):
    _, payload = _latest_history_payload(ticker)
    meta = payload.get("meta", {})
    price = meta.get("closing_price") or meta.get("price")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def _quote_cache_by_holding(ticker):
    path = QUOTE_CACHE_DIR / f"etf_{ticker}_quotes.json"
    if not path.exists():
        return {}
    cache = json.loads(path.read_text(encoding="utf-8"))
    return {str(row.get("id")): row for row in cache.get("holdings", [])}


def _normalize_underlying_key(holding_id, country=None):
    raw = str(holding_id or "").strip().upper()
    parts = raw.split()
    symbol = parts[0] if parts else raw
    market = parts[1] if len(parts) > 1 else ""
    inferred_country = country or None
    if not inferred_country:
        if market in {"US", "JP", "HK", "TW", "TWO"}:
            inferred_country = "TW" if market == "TWO" else market
        elif symbol.isdigit():
            inferred_country = "TW"
        else:
            inferred_country = "US"
    return f"{inferred_country}:{symbol}", inferred_country, symbol


def build_expanded_exposure(position_quotes):
    exposures = {}
    for _, pos in position_quotes.dropna(subset=["market_value"]).iterrows():
        ticker = pos.get("ticker")
        if ticker not in {"00981A", "00997A", "0050"}:
            key, country, code = _normalize_underlying_key(pos.get("code"), pos.get("country"))
            exposures[key] = {
                "key": key,
                "code": code,
                "name": pos.get("stock"),
                "country": country,
                "value_twd": exposures.get(key, {}).get("value_twd", 0.0) + float(pos["market_value"]),
                "source_parts": ["直接持股"],
                "day_change_pct": pos.get("day_change_pct"),
                "quote_time_utc": pos.get("quote_time_utc"),
            }
            continue

        _, payload = _latest_history_payload(ticker)
        quote_map = _quote_cache_by_holding(ticker)
        for holding in payload.get("holdings", []):
            weight = holding.get("weight_pct")
            if weight is None:
                continue
            quote_row = quote_map.get(str(holding.get("id")), {})
            key, country, code = _normalize_underlying_key(holding.get("id"), quote_row.get("country"))
            value = float(pos["market_value"]) * float(weight) / 100.0
            if key not in exposures:
                exposures[key] = {
                    "key": key,
                    "code": code,
                    "name": holding.get("name"),
                    "country": country,
                    "value_twd": 0.0,
                    "source_parts": [],
                    "weighted_move_sum": 0.0,
                    "move_weight": 0.0,
                    "quote_time_utc": quote_row.get("quote_time_utc"),
                }
            exposures[key]["value_twd"] += value
            exposures[key]["source_parts"].append(f"{ticker} {float(weight):.2f}%")
            if quote_row.get("day_change_pct") is not None:
                exposures[key]["weighted_move_sum"] += value * float(quote_row["day_change_pct"])
                exposures[key]["move_weight"] += value
            if quote_row.get("quote_time_utc"):
                exposures[key]["quote_time_utc"] = quote_row.get("quote_time_utc")

    total = sum(item.get("value_twd", 0.0) for item in exposures.values())
    rows = []
    for item in exposures.values():
        day_change = item.get("day_change_pct")
        if day_change is None and item.get("move_weight"):
            day_change = item["weighted_move_sum"] / item["move_weight"]
        rows.append({
            "name": item.get("name") or item.get("code"),
            "code": item.get("code"),
            "country": item.get("country") or "--",
            "source": " / ".join(sorted(set(item.get("source_parts", [])))),
            "value_twd": item.get("value_twd", 0.0),
            "weight_pct": item.get("value_twd", 0.0) / total * 100.0 if total else 0.0,
            "day_change_pct": day_change,
            "quote_time_utc": item.get("quote_time_utc"),
        })
    return sorted(rows, key=lambda row: row["weight_pct"], reverse=True)


def load_master_snapshot():
    positions = calculate_open_positions(load_master_trades())
    positions = enrich_positions_with_quotes(positions)
    total_market = float(positions["market_value"].dropna().sum()) if not positions.empty else 0.0
    total_liq = float(positions["liquidation_value"].dropna().sum()) if not positions.empty else 0.0
    total_cost = float(positions["cost"].sum()) if not positions.empty else 0.0
    unrealized = total_liq - total_cost
    unrealized_pct = unrealized / total_cost * 100.0 if total_cost else 0.0
    exposures = build_expanded_exposure(positions)[:50]
    return {
        "positions": positions,
        "total_market": total_market,
        "total_liq": total_liq,
        "total_cost": total_cost,
        "unrealized": unrealized,
        "unrealized_pct": unrealized_pct,
        "holding_count": int(len(positions)),
        "exposures": exposures,
    }


def build_master_text(snapshot):
    return (
        "吳大師持股\n"
        f"目前淨值(扣費稅)：{_fmt_money(snapshot['total_liq'])}\n"
        f"總成本：{_fmt_money(snapshot['total_cost'])}\n"
        f"未實損益：{_fmt_money(snapshot['unrealized'])} ({snapshot['unrealized_pct']:+.2f}%)\n"
        f"持股檔數：{snapshot['holding_count']}\n"
        "展開明細：前50大，依權重排序"
    )


def _draw_header(draw, width, snapshot, page_no, total_pages):
    _round_rect(draw, (28, 28, width - 28, 470), 28, (250, 252, 255), (221, 228, 236), 2)
    _text(draw, (74, 70), "吳大師", FONTS["title"], INK)
    _text(draw, (74, 146), "展開持股前50大", FONTS["h2"], INK)
    _text(draw, (width - 92, 76), f"{page_no}/{total_pages}", FONTS["title"], MUTED, anchor="ra")

    pnl_color = _color_for_pct(snapshot["unrealized"])
    _draw_stat(draw, 74, 316, 280, "目前淨值(扣費稅)", _fmt_money(snapshot["total_liq"]), INK)
    _draw_stat(draw, 374, 316, 230, "總成本", _fmt_money(snapshot["total_cost"]), INK)
    _draw_stat(draw, 624, 316, 260, "未實損益", _fmt_money(snapshot["unrealized"]), pnl_color)
    _draw_stat(draw, 904, 316, 220, "未實%", _fmt_pct(snapshot["unrealized_pct"]), pnl_color)
    _draw_stat(draw, 1144, 316, 180, "持股檔數", str(snapshot["holding_count"]), INK)


def _draw_col_header(draw, x, y, w):
    _text(draw, (x + 112, y), "成分股", FONTS["body_bold"], INK)
    _text(draw, (x + 545, y), "市場", FONTS["body_bold"], INK)
    _text(draw, (x + 660, y), "權重", FONTS["body_bold"], INK)
    _text(draw, (x + 800, y), "來源", FONTS["body_bold"], INK)
    _text(draw, (x + 1120, y), "更新", FONTS["body_bold"], INK)
    draw.line((x, y + 54, x + w, y + 54), fill=(209, 216, 224), width=3)


def _draw_row(draw, row, x, y, w, rank, scale):
    draw.line((x, y + 128, x + w, y + 128), fill=(232, 237, 243), width=2)
    _text(draw, (x + 8, y + 6), f"{rank:02d}", FONTS["rank"], INK)
    _text(draw, (x + 112, y + 8), _fit_text(draw, row["name"], FONTS["body_bold"], 390), FONTS["body_bold"], INK)
    _text(draw, (x + 112, y + 51), _fit_text(draw, row["code"], FONTS["small"], 390), FONTS["small"], INK)
    _text(draw, (x + 545, y + 22), row["country"], FONTS["small_bold"], INK)
    _text(draw, (x + 660, y + 22), f"{row['weight_pct']:.2f}%", FONTS["small"], INK)
    _text(draw, (x + 800, y + 22), _fit_text(draw, row["source"], FONTS["tiny"], 280), FONTS["tiny"], MUTED)
    _text(draw, (x + 1120, y + 22), _ago(row.get("quote_time_utc")), FONTS["small"], INK)

    bar_x = x + 112
    bar_y = y + 91
    bar_w = w - 150
    pct = row.get("day_change_pct")
    endpoint = _bar(draw, bar_x, bar_y, bar_w, 30, pct, scale)
    if pct is not None and not pd.isna(pct):
        text = _fmt_pct(pct)
        color = _color_for_pct(pct)
        text_w, _ = _measure(draw, text, FONTS["body_bold"])
        if pct >= 0:
            tx = min(endpoint + 12, bar_x + bar_w - text_w)
            anchor = None
        else:
            tx = max(endpoint - 12, bar_x + text_w)
            anchor = "ra"
        _text(draw, (tx, bar_y - 9), text, FONTS["body_bold"], color, anchor=anchor)


def _draw_page(snapshot, rows, page_no, total_pages, scale):
    width = 1500
    height = 4052
    img = Image.new("RGB", (width, height), (241, 244, 248))
    draw = ImageDraw.Draw(img)
    _round_rect(draw, (28, 28, width - 28, height - 28), 28, PANEL, (221, 228, 236), 2)
    _draw_header(draw, width, snapshot, page_no, total_pages)
    draw.line((74, 520, width - 74, 520), fill=LINE, width=2)
    _text(draw, (74, 552), "依展開後權重排序，紅色代表上漲、綠色代表下跌；無報價資料以 ---- 表示。", FONTS["small_bold"], INK)
    x = 74
    col_w = width - 148
    _draw_col_header(draw, x, 620, col_w)
    start_y = 700
    row_h = 132
    rank_offset = (page_no - 1) * 25
    for idx, row in enumerate(rows):
        _draw_row(draw, row, x, start_y + idx * row_h, col_w, rank_offset + idx + 1, scale)
    return img


def generate_master_holding_card(limit=50):
    snapshot = load_master_snapshot()
    rows = snapshot["exposures"][:limit]
    changes = [abs(row["day_change_pct"]) for row in rows if row.get("day_change_pct") is not None and not pd.isna(row["day_change_pct"])]
    max_abs = max(changes) if changes else 5
    scale = max(5, min(30, int(math.ceil((max_abs * 1.2) / 5.0) * 5)))

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    pages = [rows[i:i + 25] for i in range(0, len(rows), 25)] or [[]]
    output_paths = []
    for index, page_rows in enumerate(pages, start=1):
        img = _draw_page(snapshot, page_rows, index, len(pages), scale)
        output_path = IMAGE_DIR / f"master_holding_top50_{index}.jpg"
        img.save(output_path, "JPEG", quality=92, optimize=True)
        output_paths.append(output_path)
    return build_master_text(snapshot), output_paths


if __name__ == "__main__":
    text, paths = generate_master_holding_card()
    print(text)
    for path in paths:
        print(path)

#!/usr/bin/env python3
"""
Three-page LINE Bot Rich Menu setup.

Page 1 top:    油價  | 黃金   | 匯率   | 債券
Page 1 bottom: 市場脈動 | 吳大師 | ETF動作 | 那斯達克24h | ▶更多
Page 2 top:    00878 | 00981A | 00988A | 00403A
Page 2 bottom: ◀上一頁 | 00891 | 00830 | ▶更多
Page 3 top:    009805 | 009820 | 0056 | 00918
Page 3 bottom: ◀上一頁 | 0050 | 00991A | 首頁

Colour convention
  TW stocks  →  blue  (37, 99, 235)
  US stocks  →  red   (220, 38, 38)
  Oil        →  orange-red  (194, 65, 12)
  FX         →  teal  (13, 148, 136)
  Bond       →  green (21, 128, 61)
  Gold       →  amber (202, 138, 4)
  Nav        →  slate (71, 85, 105)

Usage:
    cd /home/ubuntu/STOCK && source venv/bin/activate
    python scripts/setup_rich_menu.py
"""
import io
import json
import os
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR    = Path(__file__).resolve().parents[1]
DATA_DIR    = ROOT_DIR / "data"
SECRETS_FILE = Path("/home/ubuntu/.stock_secrets")

# ── Spec ──────────────────────────────────────────────────────────────────
W, H = 2500, 1686
GAP  = 10

ALIAS_P1 = "richmenu-alias-stock-page1"
ALIAS_P2 = "richmenu-alias-stock-page2"
ALIAS_P3 = "richmenu-alias-stock-page3"

# ── Palette ───────────────────────────────────────────────────────────────
BG       = (11,  17,  32)
CELL_BG  = (22,  33,  55)
NAV_BG   = (35,  45,  65)
DIM_BG   = (18,  26,  42)
WHITE    = (255, 255, 255)
SUBTEXT  = (140, 155, 180)
DIM_TEXT = (55,  70,  95)

# ── Cell definitions ──────────────────────────────────────────────────────
# (x, y, w, h, icon, label, subtitle, accent_rgb, action_type, tap, is_nav)
# action_type: "message" | "richmenuswitch" | "none"
# tap:         text to send  | alias id           | None

PAGE1 = [
    # Row 1 — fast market
    (0,    0,   625, 843, "油", "油價",   "輕原油／布蘭特",   (194,  65,  12), "message",        "油價",   False),
    (625,  0,   625, 843, "金", "黃金",   "黃金現貨",         (202, 138,   4), "message",        "黃金",   False),
    (1250, 0,   625, 843, "匯", "匯率",   "美元／日圓／瑞郎", ( 13, 148, 136), "message",        "匯率",   False),
    (1875, 0,   625, 843, "債", "債券",   "美10年期公債",     ( 21, 128,  61), "message",        "債券",   False),
    # Row 2 — five direct actions; ETF 動作 returns the cached buy/hold/sell text.
    (0,    843, 500, 843, "脈", "市場脈動", "加權狀態",       ( 99, 102, 241), "message",        "市場脈動", False),
    (500,  843, 500, 843, "師", "吳大師",   "持股總覽",       (180,  83,   9), "message",        "吳大師", False),
    (1000, 843, 500, 843, "動", "ETF 動作", "買・抱・賣",      (239,  68,  68), "message",        "ETF動作", False),
    (1500, 843, 500, 843, "納", "那斯達克", "24 小時",         (  8, 145, 178), "message",        "那斯達克", False),
    (2000, 843, 500, 843, ">", "更多",     "ETF第二頁",      ( 71,  85, 105), "richmenuswitch", ALIAS_P2, True),
]

PAGE2 = [
    # Row 1 — primary ETF watchlist
    (0,    0,   625, 843, "息", "00878",  "永續高股息",       ( 37,  99, 235), "message",        "878",    False),
    (625,  0,   625, 843, "台", "00981A", "主動台股增長",     ( 37,  99, 235), "message",        "981",    False),
    (1250, 0,   625, 843, "全", "00988A", "主動全球創新",     (220,  38,  38), "message",        "988",    False),
    (1875, 0,   625, 843, "升", "00403A", "主動升級50",       ( 37,  99, 235), "message",        "403",    False),
    # Row 2 — previous at bottom-left, next at bottom-right
    (0,    843, 625, 843, "<", "上一頁", "回主選單",         ( 71,  85, 105), "richmenuswitch", ALIAS_P1, True),
    (625,  843, 625, 843, "晶", "00891",  "中信關鍵半導體",   ( 37,  99, 235), "message",        "891",    False),
    (1250, 843, 625, 843, "半", "00830",  "費城半導體",       (220,  38,  38), "message",        "830",    False),
    (1875, 843, 625, 843, ">", "更多",   "ETF 第三頁",       ( 71,  85, 105), "richmenuswitch", ALIAS_P3, True),
]

PAGE3 = [
    # Row 1 — ETF overflow
    (0,    0,   625, 843, "電", "009805", "美國電力基建",     (220,  38,  38), "message",        "9805",   False),
    (625,  0,   625, 843, "精", "009820", "元大納斯達克精選", (220,  38,  38), "message",        "9820",   False),
    (1250, 0,   625, 843, "高", "0056",   "元大高股息",       ( 37,  99, 235), "message",        "0056",   False),
    (1875, 0,   625, 843, "填", "00918",  "大華優利高填息30", ( 37,  99, 235), "message",        "918",    False),
    # Row 2 — previous at bottom-left, home only because there is no next page yet
    (0,    843, 625, 843, "<", "上一頁", "ETF 第二頁",       ( 71,  85, 105), "richmenuswitch", ALIAS_P2, True),
    (625,  843, 625, 843, "台", "0050",   "元大台灣50",       ( 37,  99, 235), "message",        "0050",   False),
    (1250, 843, 625, 843, "未", "00991A", "主動復華未來50",   ( 37,  99, 235), "message",        "991",    False),
    (1875, 843, 625, 843, "家", "首頁",   "回主選單",         ( 71,  85, 105), "richmenuswitch", ALIAS_P1, True),
]


def get_secret(key: str) -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        for line in SECRETS_FILE.read_text().splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip("'\"")
            if k.strip() == "LINE_TOKEN" and key == "LINE_CHANNEL_ACCESS_TOKEN":
                return v
            if k.strip() == key:
                return v
    except Exception:
        pass
    return ""


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    suffix = "Bold" if bold else "Regular"
    candidates = [
        str(DATA_DIR / "fonts" / f"NotoSansCJK-{suffix}.ttc"),
        str(DATA_DIR / "fonts" / f"NotoSansTC-{suffix}.otf"),
        "C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/mingliub.ttc" if bold else "C:/Windows/Fonts/mingliu.ttc",
        f"/usr/share/fonts/opentype/noto/NotoSansCJK-{suffix}.ttc",
        f"/usr/share/fonts/truetype/noto/NotoSansCJK-{suffix}.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_rounded_rect(draw, x0, y0, x1, y1, r, fill):
    r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    for cx, cy in [(x0+r, y0+r), (x1-r, y0+r), (x0+r, y1-r), (x1-r, y1-r)]:
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=fill)


def text_h(draw, text, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


# ── Image builder ─────────────────────────────────────────────────────────
def _fit_text_font(draw, text, max_width, *, base, minimum, bold):
    """Largest font whose rendered width stays inside one menu tile."""
    size = base
    while size >= minimum:
        font = load_font(size, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 4
    return load_font(minimum, bold=bold)


def build_image(cells: list) -> Image.Image:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    font_icon  = load_font(230, bold=True)
    font_label = load_font(86,  bold=True)
    font_sub   = load_font(68,  bold=False)
    font_dim   = load_font(72,  bold=False)

    for x, y, w, h, char, label, subtitle, accent, action_type, _, is_nav in cells:
        bx0 = x + GAP
        by0 = y + GAP
        bx1 = x + w - GAP
        by1 = y + h - GAP
        cx  = (bx0 + bx1) // 2

        bg = DIM_BG if action_type == "none" else (NAV_BG if is_nav else CELL_BG)
        draw_rounded_rect(draw, bx0, by0, bx1, by1, r=28, fill=bg)

        if action_type == "none":
            r_dim = 140 if w < 700 else 160
            draw.ellipse([cx - r_dim, by0 + 190 - r_dim,
                          cx + r_dim, by0 + 190 + r_dim], fill=(45, 58, 78))
            draw.text((cx, by0 + 190), char, font=font_icon, fill=(110, 125, 148), anchor="mm")
            draw.text((cx, by0 + 455), label, font=font_label, fill=(95, 110, 132), anchor="mt")
            draw.text((cx, by0 + 565), subtitle, font=font_sub, fill=DIM_TEXT, anchor="mt")
            continue

        # Accent bottom strip
        draw_rounded_rect(draw, bx0, by1 - 10, bx1, by1, r=5, fill=accent)

        # Vertical left edge bar
        draw_rounded_rect(draw, bx0, by0 + 35, bx0 + 6, by1 - 35, r=3, fill=accent)

        # Vertically centre the content block
        r_circle = 190 if w < 700 else 210
        gap1     = 36
        gap2     = 18
        text_width = (bx1 - bx0) - 56
        label_font = _fit_text_font(
            draw, label, text_width, base=86, minimum=42, bold=True
        )
        subtitle_font = _fit_text_font(
            draw, subtitle, text_width, base=68, minimum=38, bold=False
        )
        lh = text_h(draw, label,    label_font)
        sh = text_h(draw, subtitle, subtitle_font)
        block_h  = r_circle * 2 + gap1 + lh + gap2 + sh
        top_pad  = ((by1 - by0) - block_h) // 2
        icon_cy  = by0 + top_pad + r_circle

        # Circle
        draw.ellipse([cx - r_circle, icon_cy - r_circle,
                      cx + r_circle, icon_cy + r_circle], fill=accent)

        # Icon char
        draw.text((cx, icon_cy), char, font=font_icon, fill=WHITE, anchor="mm")

        # Label
        label_y = icon_cy + r_circle + gap1
        draw.text((cx, label_y), label, font=label_font, fill=WHITE, anchor="mt")

        # Subtitle
        sub_y = label_y + lh + gap2
        draw.text((cx, sub_y), subtitle, font=subtitle_font, fill=SUBTEXT, anchor="mt")

    return img


# ── LINE API ──────────────────────────────────────────────────────────────
def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def delete_all_menus(token):
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get("https://api.line.me/v2/bot/richmenu/list", headers=h, timeout=10)
    if r.status_code != 200:
        return
    for menu in r.json().get("richmenus", []):
        mid = menu["richMenuId"]
        requests.delete(f"https://api.line.me/v2/bot/richmenu/{mid}", headers=h, timeout=10)
        print(f"  Deleted menu: {mid}")


def delete_alias(token, alias_id):
    h = {"Authorization": f"Bearer {token}"}
    requests.delete(f"https://api.line.me/v2/bot/richmenu/alias/{alias_id}", headers=h, timeout=10)


def create_alias(token, alias_id, menu_id):
    r = requests.post(
        "https://api.line.me/v2/bot/richmenu/alias",
        headers=_headers(token),
        data=json.dumps({"richMenuAliasId": alias_id, "richMenuId": menu_id}).encode(),
        timeout=10,
    )
    if r.status_code not in (200, 201):
        print(f"  Alias warning: {r.status_code} {r.text[:80]}")
    else:
        print(f"  Alias created: {alias_id} → {menu_id}")


def create_menu(token, cells, label, chat_bar_text):
    areas = []
    for x, y, w, h, _, _, _, _, action_type, tap, _ in cells:
        if action_type == "none":
            continue
        if action_type == "message":
            action = {"type": "message", "text": tap}
        else:  # richmenuswitch
            action = {"type": "richmenuswitch", "richMenuAliasId": tap, "data": f"nav-{tap}"}
        areas.append({"bounds": {"x": x, "y": y, "width": w, "height": h}, "action": action})

    payload = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": label,
        "chatBarText": chat_bar_text,
        "areas": areas,
    }
    r = requests.post(
        "https://api.line.me/v2/bot/richmenu",
        headers=_headers(token),
        data=json.dumps(payload, ensure_ascii=False).encode(),
        timeout=15,
    )
    r.raise_for_status()
    menu_id = r.json()["richMenuId"]
    print(f"  Created [{label}]: {menu_id}")
    return menu_id


def upload_image(token, menu_id, img: Image.Image):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=93)
    buf.seek(0)
    r = requests.post(
        f"https://api-data.line.me/v2/bot/richmenu/{menu_id}/content",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
        data=buf.read(),
        timeout=30,
    )
    r.raise_for_status()
    print(f"  Image uploaded → {menu_id}")


def set_default(token, menu_id):
    r = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{menu_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    print(f"  Set as default: {menu_id}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    token = get_secret("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        sys.exit("LINE_CHANNEL_ACCESS_TOKEN not found")

    print("Building images...")
    img1 = build_image(PAGE1)
    img2 = build_image(PAGE2)
    img3 = build_image(PAGE3)

    preview_dir = DATA_DIR / "images"
    preview_dir.mkdir(parents=True, exist_ok=True)
    img1.save(preview_dir / "rich_menu_page1.jpg", format="JPEG", quality=93)
    img2.save(preview_dir / "rich_menu_page2.jpg", format="JPEG", quality=93)
    img3.save(preview_dir / "rich_menu_page3.jpg", format="JPEG", quality=93)
    print("  Previews saved → data/images/rich_menu_page1.jpg + page2.jpg + page3.jpg")

    print("Cleaning up old menus & aliases...")
    delete_alias(token, ALIAS_P1)
    delete_alias(token, ALIAS_P2)
    delete_alias(token, ALIAS_P3)
    delete_all_menus(token)

    print("Creating menus...")
    mid1 = create_menu(token, PAGE1, "Stock Menu Page 1", "查詢選單 ▲")
    mid2 = create_menu(token, PAGE2, "Stock Menu Page 2", "查詢選單 ▲")
    mid3 = create_menu(token, PAGE3, "Stock Menu Page 3", "查詢選單 ▲")

    print("Uploading images...")
    upload_image(token, mid1, img1)
    upload_image(token, mid2, img2)
    upload_image(token, mid3, img3)

    print("Creating aliases...")
    create_alias(token, ALIAS_P1, mid1)
    create_alias(token, ALIAS_P2, mid2)
    create_alias(token, ALIAS_P3, mid3)

    print("Setting page 1 as default...")
    set_default(token, mid1)

    print(f"\nDone.\n  Page 1: {mid1}\n  Page 2: {mid2}\n  Page 3: {mid3}")


if __name__ == "__main__":
    main()

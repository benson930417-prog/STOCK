#!/usr/bin/env python3
"""Retained overseas markets and public ETF menus; replace only after verification."""
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
    (0, 0, 833, 843, "油", "油價", "輕原油／布蘭特", (194,65,12), "message", "油價", False),
    (833, 0, 833, 843, "金", "黃金", "黃金現貨", (202,138,4), "message", "黃金", False),
    (1666, 0, 834, 843, "匯", "匯率", "美元／日圓／瑞郎", (13,148,136), "message", "匯率", False),
    (0, 843, 833, 843, "債", "債券", "美10年期公債", (21,128,61), "message", "債券", False),
    (833, 843, 833, 843, "納", "那斯達克", "24 小時", (8,145,178), "message", "那斯達克", False),
    (1666, 843, 834, 843, ">", "ETF", "公開持股資料", (71,85,105), "richmenuswitch", ALIAS_P2, True),
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
    base = "https://api.line.me/v2/bot"
    headers = _headers(token)
    response = requests.get(base + "/richmenu/list", headers=headers, timeout=15)
    response.raise_for_status()
    old_menus = response.json()["richmenus"]
    labels = ["Stock Menu Page 1", "Stock Menu Page 2", "Stock Menu Page 3"]
    aliases = [ALIAS_P1, ALIAS_P2, ALIAS_P3]
    response = requests.get(base + "/richmenu/alias/list", headers=headers, timeout=15)
    response.raise_for_status()
    existing = {item["richMenuAliasId"] for item in response.json()["aliases"]}
    created = []
    preview_dir = DATA_DIR / "images"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for index, cells in enumerate([PAGE1, PAGE2, PAGE3]):
        img = build_image(cells)
        img.save(preview_dir / f"rich_menu_page{index+1}.jpg", format="JPEG", quality=93)
        menu = create_menu(token, cells, labels[index], "查詢選單 ▲")
        upload_image(token, menu, img)
        created.append(menu)
    # LINE requires every new menu image to exist before its alias is updated.
    # API contract: https://developers.line.biz/en/reference/messaging-api/#update-rich-menu-alias
    for alias, menu in zip(aliases, created):
        if alias in existing:
            response = requests.post(base + "/richmenu/alias/" + alias, headers=headers,
                                     json={"richMenuId": menu}, timeout=15)
        else:
            response = requests.post(base + "/richmenu/alias", headers=headers,
                                     json={"richMenuAliasId": alias, "richMenuId": menu}, timeout=15)
        response.raise_for_status()
    set_default(token, created[0])
    for alias, menu in zip(aliases, created):
        response = requests.get(base + "/richmenu/alias/" + alias, headers=headers, timeout=15)
        response.raise_for_status()
        assert response.json()["richMenuId"] == menu
    response = requests.get(base + "/user/all/richmenu", headers=headers, timeout=15)
    response.raise_for_status()
    assert response.json()["richMenuId"] == created[0]
    # Delete only this application's replaced menus, after verified cutover.
    for old in old_menus:
        if old.get("name") in labels and old["richMenuId"] not in created:
            response = requests.delete(base + "/richmenu/" + old["richMenuId"], headers=headers, timeout=15)
            response.raise_for_status()
    print("Three retained-feature menus published and aliases verified; no broadcast sent.")


if __name__ == "__main__":
    main()

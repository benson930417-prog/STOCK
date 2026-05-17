#!/usr/bin/env python3
"""
Two-page LINE Bot Rich Menu setup.

Page 1: 00981A | 00997A | 吳大師 / 油價 | 匯率 | 債券 | 更多▶
Page 2: 0050   | 00830  | id     / ◀返回 | [coming soon]

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
    # Row 1 — 3 equal columns (833 + 834 + 833 = 2500)
    (0,    0,   833, 843, "台", "00981A", "主動台股增長",    ( 37,  99, 235), "message",        "981",    False),
    (833,  0,   834, 843, "美", "00997A", "主動美股增長",    (109,  40, 217), "message",        "997",    False),
    (1667, 0,   833, 843, "師", "吳大師", "Master Holding",  (180,  83,   9), "message",        "吳大師", False),
    # Row 2 — 4 equal columns (625 × 4 = 2500)
    (0,    843, 625, 843, "油", "油  價", "WTI / Brent",     (194,  65,  12), "message",        "油價",   False),
    (625,  843, 625, 843, "匯", "匯  率", "USD · JPY",       (  3, 105, 161), "message",        "匯率",   False),
    (1250, 843, 625, 843, "債", "債  券", "10yr US Bond",    ( 21, 128,  61), "message",        "債券",   False),
    (1875, 843, 625, 843, "▶", "更  多", "Page 2",          ( 71,  85, 105), "richmenuswitch", ALIAS_P2, True),
]

PAGE2 = [
    # Row 1 — 3 equal columns
    (0,    0,   833, 843, "萬", "0050",    "元大台灣50",      ( 37,  99, 235), "message",        "0050",   False),
    (833,  0,   834, 843, "半", "00830",   "費城半導體",      (109,  40, 217), "message",        "830",    False),
    (1667, 0,   833, 843, "碼", "查詢 ID", "User / Group ID", ( 71,  85, 105), "message",        "id",     False),
    # Row 2 — nav button + wide decorative cell
    (0,    843, 625, 843, "◀", "返  回",  "Page 1",          ( 71,  85, 105), "richmenuswitch", ALIAS_P1, True),
    (625,  843,1875, 843, "⋯", "更多功能", "即將推出",        ( 18,  26,  42), "none",           None,     True),
]


# ── Helpers ───────────────────────────────────────────────────────────────
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
def build_image(cells: list) -> Image.Image:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    font_icon  = load_font(145, bold=True)
    font_label = load_font(82,  bold=True)
    font_sub   = load_font(46,  bold=False)
    font_dim   = load_font(52,  bold=False)

    for x, y, w, h, char, label, subtitle, accent, action_type, _, is_nav in cells:
        bx0 = x + GAP
        by0 = y + GAP
        bx1 = x + w - GAP
        by1 = y + h - GAP
        cx  = (bx0 + bx1) // 2

        bg = DIM_BG if action_type == "none" else (NAV_BG if is_nav else CELL_BG)
        draw_rounded_rect(draw, bx0, by0, bx1, by1, r=28, fill=bg)

        # Decorative "coming soon" cell — just dim text, no circle
        if action_type == "none":
            draw.text((cx, by0 + (by1 - by0) // 2 - 30), char,     font=font_dim, fill=DIM_TEXT, anchor="mm")
            draw.text((cx, by0 + (by1 - by0) // 2 + 40), subtitle, font=font_sub, fill=DIM_TEXT, anchor="mm")
            continue

        # Accent bottom strip
        draw_rounded_rect(draw, bx0, by1 - 10, bx1, by1, r=5, fill=accent)

        # Vertical left edge bar
        draw_rounded_rect(draw, bx0, by0 + 35, bx0 + 6, by1 - 35, r=3, fill=accent)

        # Vertically centre the content block
        r_circle = 110 if w < 700 else 120
        gap1     = 32
        gap2     = 14
        lh = text_h(draw, label,    font_label)
        sh = text_h(draw, subtitle, font_sub)
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
        draw.text((cx, label_y), label, font=font_label, fill=WHITE, anchor="mt")

        # Subtitle
        sub_y = label_y + lh + gap2
        draw.text((cx, sub_y), subtitle, font=font_sub, fill=SUBTEXT, anchor="mt")

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

    preview_dir = DATA_DIR / "images"
    preview_dir.mkdir(parents=True, exist_ok=True)
    img1.save(preview_dir / "rich_menu_page1.jpg", format="JPEG", quality=93)
    img2.save(preview_dir / "rich_menu_page2.jpg", format="JPEG", quality=93)
    print("  Previews saved → data/images/rich_menu_page1.jpg + page2.jpg")

    print("Cleaning up old menus & aliases...")
    delete_alias(token, ALIAS_P1)
    delete_alias(token, ALIAS_P2)
    delete_all_menus(token)

    print("Creating menus...")
    mid1 = create_menu(token, PAGE1, "Stock Menu Page 1", "查詢選單 ▲")
    mid2 = create_menu(token, PAGE2, "Stock Menu Page 2", "查詢選單 ▲")

    print("Uploading images...")
    upload_image(token, mid1, img1)
    upload_image(token, mid2, img2)

    print("Creating aliases...")
    create_alias(token, ALIAS_P1, mid1)
    create_alias(token, ALIAS_P2, mid2)

    print("Setting page 1 as default...")
    set_default(token, mid1)

    print(f"\nDone.\n  Page 1: {mid1}\n  Page 2: {mid2}")


if __name__ == "__main__":
    main()

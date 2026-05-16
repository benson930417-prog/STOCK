#!/usr/bin/env python3
"""
One-shot setup script: generates the Rich Menu image and registers it
with the LINE Messaging API as the persistent bottom panel for all users.

Usage (on OCI server):
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

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
SECRETS_FILE = Path("/home/ubuntu/.stock_secrets")

# ── LINE Rich Menu spec ────────────────────────────────────────────────────
W, H = 2500, 1686
COLS, ROWS = 3, 2
CW = W // COLS   # 833
CH = H // ROWS   # 843
GAP = 10         # gap between cells (renders as divider line)

# ── Palette ───────────────────────────────────────────────────────────────
BG      = (11,  17,  32)    # #0B1120 - deep navy
CELL_BG = (22,  33,  55)    # #162137 - card background
WHITE   = (255, 255, 255)
SUBTEXT = (140, 155, 180)   # muted blue-gray

# (row, col, icon_char, label, subtitle, accent_rgb, tap_text)
CELLS = [
    (0, 0, "台", "00981A",  "主動台股增長",    ( 37,  99, 235), "981"),
    (0, 1, "美", "00997A",  "主動美股增長",    (109,  40, 217), "997"),
    (0, 2, "師", "吳大師",  "Master Holding",  (180,  83,   9), "吳大師"),
    (1, 0, "油", "油  價",  "WTI / Brent",     (194,  65,  12), "油價"),
    (1, 1, "匯", "匯  率",  "USD · TWD · JPY", (  3, 105, 161), "匯率"),
    (1, 2, "債", "債  券",  "10yr US Bond",    ( 21, 128,  61), "債券"),
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
    suffix_bold = "Bold"
    suffix_reg  = "Regular"
    candidates = [
        str(DATA_DIR / "fonts" / f"NotoSansCJK-{suffix_bold if bold else suffix_reg}.ttc"),
        str(DATA_DIR / "fonts" / f"NotoSansTC-{suffix_bold if bold else suffix_reg}.otf"),
        f"/usr/share/fonts/opentype/noto/NotoSansCJK-{suffix_bold if bold else suffix_reg}.ttc",
        f"/usr/share/fonts/truetype/noto/NotoSansCJK-{suffix_bold if bold else suffix_reg}.ttc",
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
    for cx, cy in [(x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def text_h(draw, text, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


# ── Image generation ──────────────────────────────────────────────────────
def build_image() -> Image.Image:
    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    font_icon  = load_font(155, bold=True)
    font_label = load_font(88,  bold=True)
    font_sub   = load_font(50,  bold=False)

    for row, col, char, label, subtitle, accent, _ in CELLS:
        # Cell bounds
        x0 = col * CW + GAP
        y0 = row * CH + GAP
        x1 = (col + 1) * CW - GAP
        y1 = (row + 1) * CH - GAP
        cx = (x0 + x1) // 2

        # Card background
        draw_rounded_rect(draw, x0, y0, x1, y1, r=32, fill=CELL_BG)

        # Colored accent strip at bottom of card
        draw_rounded_rect(draw, x0, y1 - 10, x1, y1, r=16, fill=accent)

        # Vertical colored left-edge bar (subtle depth cue)
        draw_rounded_rect(draw, x0, y0 + 40, x0 + 6, y1 - 40, r=3, fill=accent)

        # --- Vertically centre the content block ---
        # Content block = circle diameter + gap + label height + gap + subtitle height
        r_circle = 125
        gap1     = 38   # circle bottom → label top
        gap2     = 16   # label bottom → subtitle top
        lh = text_h(draw, label, font_label)
        sh = text_h(draw, subtitle, font_sub)
        block_h  = r_circle * 2 + gap1 + lh + gap2 + sh
        cell_h   = y1 - y0
        top_pad  = (cell_h - block_h) // 2

        icon_cy  = y0 + top_pad + r_circle

        # Circle
        draw.ellipse([cx - r_circle, icon_cy - r_circle,
                      cx + r_circle, icon_cy + r_circle], fill=accent)

        # Icon character centred inside circle
        draw.text((cx, icon_cy), char, font=font_icon, fill=WHITE, anchor="mm")

        # Label
        label_y = icon_cy + r_circle + gap1
        draw.text((cx, label_y), label, font=font_label, fill=WHITE, anchor="mt")

        # Subtitle
        sub_y = label_y + lh + gap2
        draw.text((cx, sub_y), subtitle, font=font_sub, fill=SUBTEXT, anchor="mt")

    return img


# ── LINE API ──────────────────────────────────────────────────────────────
def delete_all_menus(token: str):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get("https://api.line.me/v2/bot/richmenu/list", headers=headers, timeout=10)
    if r.status_code != 200:
        return
    for menu in r.json().get("richmenus", []):
        mid = menu["richMenuId"]
        requests.delete(f"https://api.line.me/v2/bot/richmenu/{mid}", headers=headers, timeout=10)
        print(f"  Deleted old menu: {mid}")


def register(token: str, img: Image.Image) -> str:
    headers_json = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 1. Create menu skeleton
    areas = []
    for row, col, _, _, _, _, tap in CELLS:
        areas.append({
            "bounds": {"x": col * CW, "y": row * CH, "width": CW, "height": CH},
            "action": {"type": "message", "text": tap},
        })
    payload = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": "Stock Menu",
        "chatBarText": "查詢選單 ▲",
        "areas": areas,
    }
    r = requests.post(
        "https://api.line.me/v2/bot/richmenu",
        headers=headers_json,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=15,
    )
    r.raise_for_status()
    menu_id = r.json()["richMenuId"]
    print(f"  Created menu:  {menu_id}")

    # 2. Upload image
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
    print("  Image uploaded")

    # 3. Set as default for all users
    r = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{menu_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    print("  Set as default")

    return menu_id


def main():
    token = get_secret("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        sys.exit("LINE_CHANNEL_ACCESS_TOKEN not found in environment or .stock_secrets")

    print("Building image...")
    img = build_image()

    preview = DATA_DIR / "images" / "rich_menu_preview.jpg"
    preview.parent.mkdir(parents=True, exist_ok=True)
    img.save(preview, format="JPEG", quality=93)
    print(f"  Preview saved → {preview}")

    print("Removing old menus...")
    delete_all_menus(token)

    print("Registering with LINE API...")
    menu_id = register(token, img)
    print(f"\nDone — rich menu ID: {menu_id}")


if __name__ == "__main__":
    main()

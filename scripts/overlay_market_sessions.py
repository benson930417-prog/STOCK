"""Overlay trading-session markers on the NASDAQ 24h chart.

Local iteration tool: load a TradingView NASDAQ snapshot (x-axis = Taipei time,
left edge = 07:00 TW) and draw faint colored session bands plus thick
double-headed dimension arrows (along the top, below the title) marking the TW
stock session and the US pre / regular / post-market sessions in TW time.

US session times are defined in US Eastern and converted to Taipei with a
DST-aware offset derived from the chart's date (+12 in summer / +13 in winter).

Run locally:
    python scripts/overlay_market_sessions.py INPUT.jpg -o OUTPUT.png [--date YYYY-MM-DD]

Tune the CONFIG block until it lines up, then we port the mapping to the server.
"""
import argparse
import os
from datetime import date as date_cls, datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

# Bundled CJK font the chart service ships in data/fonts (downloaded at runtime
# on the server). Preferred so the Chinese labels render on Linux too.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_CJK_FONT = os.path.join(_REPO_ROOT, "data", "fonts", "NotoSansTC-Regular.otf")

# ----------------------------------------------------------------------------
# CONFIG -- everything you'd nudge pixel-by-pixel lives here.
# ----------------------------------------------------------------------------

# Time -> pixel-x mapping, calibrated from the 1200x420 snapshot.
# x-axis tick centers measured: 09:00=120 ... 06:00=1051 -> 44.333 px/hour.
# 07:00 sits at the plot's left edge; we anchor on 09:00 to avoid the
# left-clipped first label.
ANCHOR_HOUR = 2.0        # hours after 07:00 for the anchor tick (09:00)
ANCHOR_X = 120.0         # pixel-x of that tick
PX_PER_HOUR = 44.333     # horizontal pixels per hour
PLOT_LEFT_X = 31         # left edge of plotting area (07:00)
PLOT_RIGHT_X = 1095      # right edge (07:00 next day, t=24h)

ET = ZoneInfo("America/New_York")
TW = ZoneInfo("Asia/Taipei")

# Sessions. tz="TW": hours are a TW clock. tz="ET": hours are a US Eastern clock
# and get shifted onto the TW axis by the DST-aware offset for the chart date.
# (label, color, start_hour, end_hour, tz)
SESSIONS = [
    ("台股",     (242, 153, 0),  9.0,  13.5, "TW"),   # amber
    ("美股盤前", (66, 165, 245), 4.0,  9.5,  "ET"),   # light blue
    ("美股盤中", (38, 166, 154), 9.5,  16.0, "ET"),   # teal
    ("美股盤後", (171, 71, 188), 16.0, 20.0, "ET"),   # purple
]

# Vertical layout is computed from the actual image height so it stays correct
# even if the snapshot's bottom-trim changes the body height. The chart body
# starts below the title bar that chart_service stamps on top.
TITLE_BAR_H = 70         # height of the title bar chart_service adds on top

# Extra white strip appended below the chart so the bigger arrows/labels have
# breathing room (the original snapshot's bottom gap is too short for them).
ADD_BOTTOM_SPACE = 56

# Shaded session bands (kept). Faint full-plot-height rectangles.
DRAW_BANDS = True
BAND_TOP_MARGIN = 0      # px below the title bar where the band starts
BAND_BOTTOM_MARGIN = 4   # px from the (expanded) image bottom where bands end
BAND_ALPHA = 24

# Thick double-headed dimension arrows in the white strip at the BOTTOM,
# with the label sitting in a break in the middle of the shaft (CAD style).
ARROW_BOTTOM_MARGIN = 26 # px from the (expanded) image bottom to the arrow line
ARROW_W = 8              # shaft thickness (px)
ARROW_HEAD = 13          # arrowhead half-length / half-height (px)
LABEL_GAP = 10           # gap between label and shaft on each side (px)
FONT_SIZE = 24           # arrow label text size


def tw_offset_hours(chart_date):
    """Hours to add to a US Eastern clock time to get the Taipei clock time."""
    d = datetime(chart_date.year, chart_date.month, chart_date.day, 9, 30, tzinfo=ET)
    return (d.astimezone(TW).utcoffset() - d.utcoffset()).total_seconds() / 3600.0


def x_of(tw_hour):
    """TW clock hour (may exceed 24 = next day) -> pixel x."""
    hours_after_0700 = tw_hour - 7.0
    return ANCHOR_X + (hours_after_0700 - ANCHOR_HOUR) * PX_PER_HOUR


def resolve_session(s, offset):
    label, color, start, end, tz = s
    if tz == "ET":
        start += offset
        end += offset
    return label, color, start, end


def load_font(size):
    candidates = [
        REPO_CJK_FONT,                       # server (and local, if present)
        "C:/Windows/Fonts/msjhbd.ttc",       # local Windows iteration
        "C:/Windows/Fonts/msjh.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # generic Linux
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def a(color, alpha):
    return (color[0], color[1], color[2], alpha)


def dimension_with_label(d, x0, x1, y, color, label, font):
    """CAD-style dimension: outward arrowheads at x0/x1, shaft broken in the
    middle where the centered label sits."""
    h, w = ARROW_HEAD, ARROW_W
    cx = (x0 + x1) / 2
    tb = d.textbbox((0, 0), label, font=font)
    tw_, th = tb[2] - tb[0], tb[3] - tb[1]
    lx0, lx1 = cx - tw_ / 2, cx + tw_ / 2

    # arrowheads pointing outward at both ends
    d.polygon([(x0, y), (x0 + h, y - h), (x0 + h, y + h)], fill=color)
    d.polygon([(x1, y), (x1 - h, y - h), (x1 - h, y + h)], fill=color)
    # shaft on each side of the label break
    d.line([(x0 + h, y), (lx0 - LABEL_GAP, y)], fill=color, width=w)
    d.line([(lx1 + LABEL_GAP, y), (x1 - h, y)], fill=color, width=w)
    # label centered on the line
    d.text((lx0, y - th / 2 - tb[1]), label, font=font, fill=color)


def draw_overlay(img, chart_date=None):
    if chart_date is None:
        chart_date = datetime.now(TW).date()
    offset = tw_offset_hours(chart_date)
    src = img.convert("RGBA")
    width, orig_h = src.size

    # Append a white strip at the bottom for the enlarged arrows/labels.
    height = orig_h + ADD_BOTTOM_SPACE
    base = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    base.paste(src, (0, 0))

    band_top = TITLE_BAR_H + BAND_TOP_MARGIN
    band_bottom = height - BAND_BOTTOM_MARGIN
    arrow_y = height - ARROW_BOTTOM_MARGIN

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font = load_font(FONT_SIZE)

    for s in SESSIONS:
        label, color, start, end = resolve_session(s, offset)
        x0 = max(PLOT_LEFT_X, x_of(start))
        x1 = min(PLOT_RIGHT_X, x_of(end))
        if x1 - x0 < 2 * ARROW_HEAD:
            continue

        if DRAW_BANDS:
            d.rectangle([x0, band_top, x1, band_bottom], fill=a(color, BAND_ALPHA))

        dimension_with_label(d, x0, x1, arrow_y, a(color, 255), label, font)

    return Image.alpha_composite(base, overlay).convert("RGB")


def overlay_sessions_on_file(image_path, chart_date=None):
    """Open a saved chart snapshot, draw the session overlay, save in place.

    Imported by chart_service.py after the title bar is stamped on the NASDAQ
    snapshot. Safe no-op semantics: raises on real failures so the caller can
    log, but does not alter the image unless drawing succeeds.
    """
    out = draw_overlay(Image.open(image_path), chart_date)
    out.save(image_path)
    return image_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default="overlay_out.png")
    ap.add_argument("--date", help="chart start date (YYYY-MM-DD) for DST; default=today")
    args = ap.parse_args()
    chart_date = (datetime.strptime(args.date, "%Y-%m-%d").date()
                  if args.date else date_cls.today())
    out = draw_overlay(Image.open(args.input), chart_date)
    out.save(args.output)
    print(f"wrote {args.output} ({out.size[0]}x{out.size[1]}) "
          f"offset=+{tw_offset_hours(chart_date):.0f}h")


if __name__ == "__main__":
    main()

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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

# Bundled CJK font the chart service ships in data/fonts (downloaded at runtime
# on the server). Preferred so the Chinese labels render on Linux too.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_CJK_FONT = os.path.join(_REPO_ROOT, "data", "fonts", "NotoSansTC-Regular.otf")

# ----------------------------------------------------------------------------
# CONFIG -- everything you'd nudge pixel-by-pixel lives here.
# ----------------------------------------------------------------------------

# Time -> pixel-x mapping.
#
# The IG overview "1 day" chart shows a FIXED session window -- 07:00 TW (the
# session open, left edge) to that session's close -- fitted across a FIXED plot
# pixel area [PLOT_LEFT_X .. PLOT_RIGHT_X]. It is NOT a rolling "07:00 .. now"
# window: early in the session the right part of the axis is simply empty (a
# Monday-morning server capture shows the axis already spanning 07:00->06:00 with
# price only out to ~09:00). So px/hour is CONSTANT for a given session and does
# NOT depend on wall-clock now.
#
# What changes px/hour is the session LENGTH. The whole session is fit into the
# same plot width, so a shorter session is more compressed (larger px/hour):
#   - Normal weekday: 07:00 -> 06:00 next day = 23h  -> px/hour ~ 44.33
#   - Friday:         07:00 -> 05:00 Sat      = 22h  -> px/hour ~ 46.4
# This is the "Friday drift": the original code used the 23h constant (44.33) on
# every day, so on Friday it placed bands too far left and the error grew toward
# the right edge (US open appearing before 21:00). The fix keys px/hour off the
# displayed session's span, not off now.
#
# Pixel edges are invariants of the 1200-wide IG snapshot layout, from measured
# x-axis tick centers (09:00=120, 06:00=1051) -> 07:00=31 and the normal-day close
# (06:00) at 1051, which also reproduces the proven normal-day 44.33 px/hour.
SESSION_OPEN_H = 7.0         # TW clock hour the IG cash session opens (left edge)
PLOT_LEFT_X = 31             # pixel-x of the left edge (SESSION_OPEN_H = 07:00)
PLOT_RIGHT_X = 1051          # pixel-x of the right edge (session close, fixed)
PX_PER_HOUR_FALLBACK = 44.333  # used only if the session span is degenerate

# IG:NASDAQ ("US Tech 100" cash) session schedule, expressed on the TW axis (the
# server pins the browser to Asia/Taipei). Opens ~07:00 TW each weekday and runs
# into the next morning; Friday is the last session before the weekend and the 1D
# chart stays FROZEN on it until Monday's reopen (so weekend/pre-open captures use
# Friday's 22h span, which is why displayed_session() rolls back to it).
#
# CONFIRMED from chart hover tooltips (the user's TradingView renders in UTC):
#   weekend gap = Fri 21:00 UTC (last bar 20:55) -> Sun 22:00 UTC reopen; on the
#   Friday overview the close reads 22:59 GMT+2 = 04:59 TW. In TW: Friday weekly
#   close = ~05:00 TW Sat, reopen = Mon 06:00 TW. Matches IG dealing hours (22:00
#   London Fri close / 23:00 London Sun open, BST). (Fri 19 Jun 2026 closed early
#   at 19:55 UTC = Juneteenth holiday, not a rule.)
#   - Normal weekday close: 06:00 TW next morning (23h session).
#   - Friday weekly close:  05:00 TW Sat (22h session) -- the source of the drift.
DAILY_CLOSE_H = 6.0          # next-day TW hour a normal weekday session closes
FRIDAY_CLOSE_H = 5.0         # next-day (Sat) TW hour the Friday weekly close lands

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

# Vertical boundary lines at each end of a region, spanning the full shaded
# band (band_top -> band_bottom). CAD-style region delimiters.
DRAW_END_TICKS = True
TICK_W = 2               # vertical line thickness (px)


def tw_offset_hours(chart_date):
    """Hours to add to a US Eastern clock time to get the Taipei clock time."""
    d = datetime(chart_date.year, chart_date.month, chart_date.day, 9, 30, tzinfo=ET)
    return (d.astimezone(TW).utcoffset() - d.utcoffset()).total_seconds() / 3600.0


def _at_hour(dt, hour_float):
    """Return dt at the given fractional TW hour (e.g. 5.5 -> 05:30)."""
    return dt.replace(hour=int(hour_float),
                      minute=int(round((hour_float % 1) * 60)),
                      second=0, microsecond=0)


def displayed_session(capture_dt):
    """(open_dt, close_dt): the trading session the 1D chart currently shows.

    The IG cash session opens ~07:00 TW each weekday and runs into the next
    morning. After midnight but before 07:00 the live session opened the PREVIOUS
    day. Weekends/holidays have no open, so the chart stays frozen on the last
    weekday session (Friday) -- we roll back to it. Friday closes earlier (weekly
    close) than the daily close on other mornings.
    """
    open_today = _at_hour(capture_dt, SESSION_OPEN_H)
    open_dt = open_today if capture_dt >= open_today else open_today - timedelta(days=1)
    while open_dt.weekday() >= 5:        # Sat(5)/Sun(6) never open -> last weekday
        open_dt -= timedelta(days=1)
    close_h = FRIDAY_CLOSE_H if open_dt.weekday() == 4 else DAILY_CLOSE_H
    close_dt = _at_hour(open_dt + timedelta(days=1), close_h)
    return open_dt, close_dt


def axis_scale(capture_dt):
    """Pixels per axis-hour for this snapshot.

    The 1D chart fits the WHOLE displayed session [open .. close] across the fixed
    plot width [PLOT_LEFT_X .. PLOT_RIGHT_X], so px/hour is constant for the session
    and depends only on its span -- NOT on wall-clock now (early in the session the
    right of the axis is just empty). Friday's 22h span is more compressed than a
    normal 23h day; that difference is the whole bug. Returns
    (px_per_hour, open_dt, close_dt).
    """
    open_dt, close_dt = displayed_session(capture_dt)
    span_h = (close_dt - open_dt).total_seconds() / 3600.0
    if span_h <= 0.1:
        return PX_PER_HOUR_FALLBACK, open_dt, close_dt
    return (PLOT_RIGHT_X - PLOT_LEFT_X) / span_h, open_dt, close_dt


def x_of(tw_hour, px_per_hour):
    """TW clock hour on the 07:00-anchored axis (>=24 = next day) -> pixel x."""
    return PLOT_LEFT_X + (tw_hour - SESSION_OPEN_H) * px_per_hour


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


def draw_overlay(img, capture_dt=None):
    if capture_dt is None:
        capture_dt = datetime.now(TW)
    elif capture_dt.tzinfo is None:
        capture_dt = capture_dt.replace(tzinfo=TW)
    px_per_hour, open_dt, close_dt = axis_scale(capture_dt)
    # DST offset is anchored on the displayed session's open date (its 07:00 left
    # edge), not the wall-clock date, so a post-midnight or weekend-frozen capture
    # still uses the right day (e.g. Friday on a Sunday).
    offset = tw_offset_hours(open_dt.date())
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
        x0 = max(PLOT_LEFT_X, x_of(start, px_per_hour))
        x1 = min(PLOT_RIGHT_X, x_of(end, px_per_hour))
        if x1 - x0 < 2 * ARROW_HEAD:
            continue

        if DRAW_BANDS:
            d.rectangle([x0, band_top, x1, band_bottom], fill=a(color, BAND_ALPHA))

        # full-height vertical boundary lines spanning the shaded region
        if DRAW_END_TICKS:
            d.line([(x0, band_top), (x0, band_bottom)], fill=a(color, 255), width=TICK_W)
            d.line([(x1, band_top), (x1, band_bottom)], fill=a(color, 255), width=TICK_W)

        dimension_with_label(d, x0, x1, arrow_y, a(color, 255), label, font)

    return Image.alpha_composite(base, overlay).convert("RGB")


def overlay_sessions_on_file(image_path, capture_dt=None):
    """Open a saved chart snapshot, draw the session overlay, save in place.

    Imported by chart_service.py right after the title bar is stamped on the
    NASDAQ snapshot, so `capture_dt` defaults to now (TW) — which is exactly the
    chart's right-edge time and what the dynamic px/hour scale needs. Safe no-op
    semantics: raises on real failures so the caller can log, but does not alter
    the image unless drawing succeeds.
    """
    out = draw_overlay(Image.open(image_path), capture_dt)
    out.save(image_path)
    return image_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--output", default="overlay_out.png")
    ap.add_argument("--capture", help="capture moment 'YYYY-MM-DD HH:MM' (TW) = "
                                      "chart right edge; default=now. This drives "
                                      "the dynamic time->pixel scale.")
    ap.add_argument("--date", help="(legacy) chart start date YYYY-MM-DD; assumes "
                                   "capture at 07:00, i.e. a full 24h span")
    args = ap.parse_args()
    if args.capture:
        capture_dt = datetime.strptime(args.capture, "%Y-%m-%d %H:%M").replace(tzinfo=TW)
    elif args.date:
        d = datetime.strptime(args.date, "%Y-%m-%d").date()
        capture_dt = datetime(d.year, d.month, d.day, 7, 0, tzinfo=TW)
    else:
        capture_dt = datetime.now(TW)
    px_per_hour, open_dt, close_dt = axis_scale(capture_dt)
    span_h = (close_dt - open_dt).total_seconds() / 3600.0
    out = draw_overlay(Image.open(args.input), capture_dt)
    out.save(args.output)
    print(f"wrote {args.output} ({out.size[0]}x{out.size[1]}) "
          f"session={open_dt:%a %m-%d %H:%M}->{close_dt:%H:%M} span={span_h:.0f}h "
          f"offset=+{tw_offset_hours(open_dt.date()):.0f}h px/hour={px_per_hour:.2f}")


if __name__ == "__main__":
    main()

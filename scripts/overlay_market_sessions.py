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
# The IG overview "1 day" chart maps the trading "day" [07:00 TW open .. frame end]
# onto a FIXED plot pixel area [PLOT_LEFT_X .. PLOT_RIGHT_X]. The left edge (07:00)
# and the right edge (PLOT_RIGHT_X) are fixed pixels; the FRAME END time differs by
# weekday, so px/hour is constant within a day but differs Friday vs Mon-Thu:
#   - Mon-Thu: the market is continuous, so the grapher frame is a full 24h,
#     07:00 -> 07:00 next day. px/hour = (1095-31)/24 = 44.33.
#   - Friday:  the REAL weekly close is ~05:00 TW Sat (= Fri 21:00 UTC = 22:00
#     London), so the frame is 07:00 -> 05:00 = 22h, fit into the SAME width and
#     therefore more compressed. px/hour = (1095-31)/22 = 48.4.
# Using the 24h constant on Friday (the bug) placed bands too far left, with the
# error growing rightward (US open drawn before 21:00).
#
# Two hard constraints pin every number, no free parameters:
#   1. The user confirmed a Monday (weekday) capture was correct at 44.33 -> the
#      weekday frame is 24h and (PLOT_RIGHT_X-31)/24=44.33 -> PLOT_RIGHT_X=1095.
#   2. The Friday overview hover reads close 22:59 GMT+2 = 04:59 TW -> Friday frame
#      is 07:00->05:00 = 22h -> px/hour 48.4 at the SAME PLOT_RIGHT_X=1095.
# The "04:40"-type label on a Friday capture is just the latest data point floating
# inside the frame; it is NOT the frame end -- do not scale to it (that and the old
# "scale to now" idea were both wrong).
#
# Pixel edges are invariants of the 1200-wide IG snapshot layout (measured tick
# centers 09:00=120, 06:00=1051 on a weekday 24h frame -> 07:00=31, 07:00-next=1095).
SESSION_OPEN_H = 7.0         # TW clock hour the chart frame starts (left edge)
PLOT_LEFT_X = 31             # pixel-x of the left edge (SESSION_OPEN_H = 07:00)
PLOT_RIGHT_X = 1095          # pixel-x of the right edge (the frame end)
WEEKDAY_FRAME_H = 24.0       # Mon-Thu continuous: frame 07:00 -> 07:00 next day
FRIDAY_FRAME_H = 22.0        # Friday: real weekly close ~05:00 TW -> 07:00 -> 05:00

# IG:NASDAQ ("US Tech 100" cash) trades ~07:00 TW each weekday into the next
# morning (server pins the browser to Asia/Taipei). Over the weekend the chart
# stays FROZEN on Friday's 22h frame, so displayed_session_open() rolls weekend/
# pre-open captures back to the last weekday (Friday) -- this both picks the DST
# date for the US-session offset AND selects the Friday frame length. Schedule
# (confirmed from hover tooltips, rendered in UTC): weekend gap = Fri 21:00 UTC ->
# Sun 22:00 UTC; Friday data ends ~05:00 TW Sat, reopen Mon 06:00 TW. Matches IG
# dealing hours (22:00 London Fri / 23:00 London Sun, BST).

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

# Vertical layout is computed from the actual image height + font metrics so it
# stays correct even if the snapshot's bottom-trim changes the body height. The
# chart body starts below the title bar that chart_service stamps on top.
TITLE_BAR_H = 70         # height of the title bar chart_service adds on top

# Shaded session bands (faint) + vertical end-ticks. Both run from band_top down
# to the arrow line ONLY, so the label row below them is never crossed by a line.
DRAW_BANDS = True
BAND_TOP_MARGIN = 0      # px below the title bar where the band starts
BAND_ALPHA = 24
DRAW_END_TICKS = True
TICK_W = 2               # vertical boundary-line thickness (px)

# Dimension marker: a FULL double-headed arrow spanning the region in the white
# strip below the chart, with the label centered BELOW it. A narrow region (e.g.
# Friday 美股盤後, ~1h wide) gets a short arrow and the label simply overflows
# sideways into neighbours -- because it sits below every vertical line, nothing
# crosses it. The bottom strip height is derived from the font so labels fit.
ARROW_TOP_GAP = 16       # px from the chart-body bottom down to the arrow line
ARROW_W = 8              # shaft thickness (px)
ARROW_HEAD = 13          # arrowhead half-length / half-height (px)
ARROW_TO_LABEL_GAP = 3   # px from the arrowhead bottom to the label top (tight)
LABEL_BOTTOM_PAD = 8     # px of white below the label
FONT_SIZE = 24           # arrow label text size


def tw_offset_hours(chart_date):
    """Hours to add to a US Eastern clock time to get the Taipei clock time."""
    d = datetime(chart_date.year, chart_date.month, chart_date.day, 9, 30, tzinfo=ET)
    return (d.astimezone(TW).utcoffset() - d.utcoffset()).total_seconds() / 3600.0


def _at_hour(dt, hour_float):
    """Return dt at the given fractional TW hour (e.g. 5.5 -> 05:30)."""
    return dt.replace(hour=int(hour_float),
                      minute=int(round((hour_float % 1) * 60)),
                      second=0, microsecond=0)


def displayed_session_open(capture_dt):
    """TW datetime of the chart frame's left edge (07:00).

    The frame opens 07:00 TW. After midnight but before 07:00 the live frame opened
    the PREVIOUS day. Weekends have no open, so the chart stays frozen on the last
    weekday (Friday) frame -- we roll back to it. Used only to pick the DST date for
    the US-session offset; the pixel scale is the same fixed 24h frame regardless.
    """
    open_today = _at_hour(capture_dt, SESSION_OPEN_H)
    open_dt = open_today if capture_dt >= open_today else open_today - timedelta(days=1)
    while open_dt.weekday() >= 5:        # Sat(5)/Sun(6) never open -> last weekday
        open_dt -= timedelta(days=1)
    return open_dt


def axis_scale(capture_dt):
    """(px_per_hour, open_dt) for this snapshot.

    The frame [07:00 .. frame end] is fit across the fixed plot width, so px/hour is
    constant within a day but depends on the frame length: 24h Mon-Thu (44.33), 22h
    on Friday (48.4, because the real weekly close is ~05:00 TW). It does NOT depend
    on wall-clock now. open_dt is the frame's left edge (also picks the DST date).
    """
    open_dt = displayed_session_open(capture_dt)
    frame_h = FRIDAY_FRAME_H if open_dt.weekday() == 4 else WEEKDAY_FRAME_H
    return (PLOT_RIGHT_X - PLOT_LEFT_X) / frame_h, open_dt


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


def dimension_with_label(d, x0, x1, arrow_y, label_y, color, label, font):
    """Full double-headed dimension arrow from x0..x1 at arrow_y, with the label
    centered BELOW it (its visual top at label_y). The label may be wider than the
    arrow on a narrow region; it overflows sideways but sits below all the vertical
    extension lines, so nothing crosses it."""
    h, w = ARROW_HEAD, ARROW_W
    # outward arrowheads at both ends, with a continuous shaft between them
    d.polygon([(x0, arrow_y), (x0 + h, arrow_y - h), (x0 + h, arrow_y + h)], fill=color)
    d.polygon([(x1, arrow_y), (x1 - h, arrow_y - h), (x1 - h, arrow_y + h)], fill=color)
    if x1 - x0 > 2 * h:
        d.line([(x0 + h, arrow_y), (x1 - h, arrow_y)], fill=color, width=w)
    # label centered below the arrow
    cx = (x0 + x1) / 2
    tb = d.textbbox((0, 0), label, font=font)
    d.text((cx - (tb[2] - tb[0]) / 2 - tb[0], label_y - tb[1]), label, font=font, fill=color)


def draw_overlay(img, capture_dt=None):
    if capture_dt is None:
        capture_dt = datetime.now(TW)
    elif capture_dt.tzinfo is None:
        capture_dt = capture_dt.replace(tzinfo=TW)
    px_per_hour, open_dt = axis_scale(capture_dt)
    # DST offset is anchored on the displayed frame's open date (its 07:00 left
    # edge), not the wall-clock date, so a post-midnight or weekend-frozen capture
    # still uses the right day (e.g. Friday on a Sunday).
    offset = tw_offset_hours(open_dt.date())
    src = img.convert("RGBA")
    width, orig_h = src.size

    font = load_font(FONT_SIZE)
    try:
        lb = font.getbbox("美股盤後")
        label_h = lb[3] - lb[1]
    except Exception:
        label_h = FONT_SIZE

    # Append a white strip sized for: arrow line, then the label row beneath it.
    arrow_y = orig_h + ARROW_TOP_GAP
    label_y = arrow_y + ARROW_HEAD + ARROW_TO_LABEL_GAP
    height = int(label_y + label_h + LABEL_BOTTOM_PAD)
    base = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    base.paste(src, (0, 0))

    band_top = TITLE_BAR_H + BAND_TOP_MARGIN
    band_bottom = arrow_y          # bands + end-ticks stop at the arrow line

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    for s in SESSIONS:
        label, color, start, end = resolve_session(s, offset)
        x0 = max(PLOT_LEFT_X, x_of(start, px_per_hour))
        x1 = min(PLOT_RIGHT_X, x_of(end, px_per_hour))
        if x1 - x0 < 2 * ARROW_HEAD:
            continue

        if DRAW_BANDS:
            d.rectangle([x0, band_top, x1, band_bottom], fill=a(color, BAND_ALPHA))

        # vertical boundary lines: band_top down to the arrow line only (so they
        # never cross the label row below)
        if DRAW_END_TICKS:
            d.line([(x0, band_top), (x0, band_bottom)], fill=a(color, 255), width=TICK_W)
            d.line([(x1, band_top), (x1, band_bottom)], fill=a(color, 255), width=TICK_W)

        dimension_with_label(d, x0, x1, arrow_y, label_y, a(color, 255), label, font)

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
    px_per_hour, open_dt = axis_scale(capture_dt)
    out = draw_overlay(Image.open(args.input), capture_dt)
    out.save(args.output)
    print(f"wrote {args.output} ({out.size[0]}x{out.size[1]}) "
          f"frame_open={open_dt:%a %m-%d %H:%M} (24h) "
          f"offset=+{tw_offset_hours(open_dt.date()):.0f}h px/hour={px_per_hour:.2f}")


if __name__ == "__main__":
    main()

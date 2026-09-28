# Persistent chart browser (2026-09-29)

This supersedes historical README statements about a single rotating tab,
three-navigation/two-minute browser resets, and two-hour service termination.

## Preserved chart contract

The deployed `CHART_TABS`, quote sources, DOM extraction, snapshot rendering,
crop, same-moment quote/text, image validation, 1-day selection, and overlays
are unchanged. A 29-definition AST contract in
`tests/fixtures/chart_rendering_contract.json` is captured from the deployed
baseline, not from the new implementation. `overlay_market_sessions.py` and
`src/market_chart_cache.py` remain unchanged, including Friday 22-hour vs
Mon–Thu 24-hour frames, weekend carry-forward, DST and immutable LINE images.

## Lifecycle and scheduling

- Chromium/context remain alive, with at most eight lazily opened symbol pages.
  A healthy page is reused. Its unchanged snapshot function runs on each update.
- Refresh failed pages on the next eligible request, and refresh a page after
  ten minutes to bound retained-document age. No source substitution occurs.
  A reused NASDAQ page first removes only our own injected screenshot styles,
  so the original 1-day click/verification can run again.
- At 2 GiB service-cgroup usage, recycle Chromium between requests. cgroup usage
  includes browser child processes. `MemoryHigh=2304M`, `MemoryMax=2560M`,
  `TasksMax=512` are final host protection; limits do not imply failure-free
  operation at the ceiling. Only this background chart service receives them.
- Remove the chart's 2h and chart-monitor's 12h forced lifetime. Other monitor
  units are unaffected. `KillMode=mixed`, Uvicorn graceful timeout 190s and
  `TimeoutStopSec=210` allow an in-flight capture to finish before child cleanup.
- Pure ASGI serialization bounds queue wait to 5s and a complete browser request
  to 180s. Deadline cancellation finishes before the next capture acquires the
  lock. A failed browser/page is invalidated for the next request.
- Monitor has per-symbol next-due times (60s target), instead of sleeping another
  minute after a full eight-symbol pass. A slow cold load can exceed the target;
  missed intervals are not replayed in bursts. HTTP budget is 200s, longer than
  the server queue + work budget. Requests remain serial to protect resources.
- Each failing symbol cools down independently. Two blocked symbols in one pass
  also open a 300s provider circuit. All eight URLs are TradingView URLs;
  `IG:NASDAQ` is not misrepresented as a separate website. Existing provider
  cooldown is migrated; per-symbol cooldown survives monitor restarts.
- Existing checksum, atomic metadata commit, stale-cache labeling, weekend
  freshness and content-versioned reply images remain untouched.

`GET /healthz` on the loopback chart API reports browser generation/age, page
count, cgroup memory and whether the worker is busy. It does not claim market
data is fresh; image cache timestamps and checksum remain the evidence for that.

## Verification

Local regression: 35 tests and 6 subtests passed across resource lifecycle,
request cancellation/queue handling, independent cooldowns, source/rendering
contract, weekend axes and existing cache/LINE image consistency. Tests do not
send LINE messages or call market websites. An upstream 403 can still prevent
a fresh chart; persistence is not an access-block bypass.

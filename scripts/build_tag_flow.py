#!/usr/bin/env python3
"""Build data/tag_flow.json — what themes the 3 TW active ETFs are buying/selling.

Signal (reuses the website's existing 操作日報 measure, see src/ui/etf_tab.py):

    ActiveWeight = Δshares * (weight_pct / shares)
                 = (money traded on the stock) / fund_size * 100

i.e. the net money an ETF put into (or pulled out of) a stock, expressed as a
percent of the fund's own size. This is price-drift free (it counts the traded
shares only, not the revaluation of the existing position) and self-normalising
across funds of different size — a 10億 buy is "big" or "small" purely relative to
how large the fund is.

"How big is big" is judged per-ETF against its OWN trailing-7-session distribution
of per-trade |ActiveWeight|:
    加碼 / 減碼        : |flow| >= mean + 1σ
    大幅加碼 / 大幅減碼 : |flow| >= mean + 2σ
so the threshold self-adjusts as the fund grows or shrinks.

Flows are aggregated to themes via data/stock_tags.json (cmoney 類股 category as
the primary one-tag-per-stock bucket; 概念股 concepts as secondary).

Outputs today (cur vs prev session) and a 5-session window, plus a per-session
theme heatmap. Streamlit only reads the JSON — no computation at render time.

Run daily from the 18:30 job (free, local, no network).
"""
from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "tag_flow.json"
TAGS_FILE = DATA / "stock_tags.json"

ETFS = {
    "00403A": DATA / "etf_00403A_history.json",
    "00981A": DATA / "etf_00981A_history.json",
    "00991A": DATA / "etf_00991A_history.json",
}
WINDOW = 5          # sessions for the medium-term view
BASELINE_SESSIONS = 7   # sessions used for the per-ETF "usual trade size" baseline
UNTAGGED = "未分類"


def load_history(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def per_share_weight(h: dict) -> float:
    s = h.get("shares") or 0
    return (h.get("weight_pct", 0.0) / s) if s else 0.0


def flow_between(cur_day: dict, base_day: dict) -> dict[str, dict]:
    """Per-stock ActiveWeight flow from base_day -> cur_day for one ETF.

    Returns {id: {name, flow, dshares}}. flow > 0 = net buy (% of fund)."""
    cur = {h["id"]: h for h in cur_day.get("holdings", [])}
    base = {h["id"]: h for h in base_day.get("holdings", [])}
    out: dict[str, dict] = {}
    for sid, c in cur.items():
        b = base.get(sid)
        if b is None:  # brand-new position: the whole current weight is the buy
            out[sid] = {"name": c["name"], "flow": c.get("weight_pct", 0.0),
                        "dshares": c.get("shares", 0)}
            continue
        ds = c.get("shares", 0) - b.get("shares", 0)
        if ds == 0:
            continue
        perw = per_share_weight(c) if c.get("shares") else per_share_weight(b)
        out[sid] = {"name": c["name"], "flow": ds * perw, "dshares": ds}
    for sid, b in base.items():  # fully removed position: a full sell
        if sid not in cur:
            out[sid] = {"name": b["name"], "flow": -b.get("weight_pct", 0.0),
                        "dshares": -b.get("shares", 0)}
    return out


def baseline_stats(dates: list[str], hist: dict) -> dict:
    """Distribution of per-trade |flow| over the last BASELINE_SESSIONS pairs."""
    mags: list[float] = []
    recent = dates[-(BASELINE_SESSIONS + 1):]
    for prev, cur in zip(recent, recent[1:]):
        for v in flow_between(hist[cur], hist[prev]).values():
            m = abs(v["flow"])
            if m > 1e-9:
                mags.append(m)
    if not mags:
        return {"mean": 0.0, "std": 0.0, "one_sigma": 0.0, "two_sigma": 0.0, "n": 0}
    mean = statistics.fmean(mags)
    std = statistics.pstdev(mags) if len(mags) > 1 else 0.0
    return {"mean": mean, "std": std, "one_sigma": mean + std,
            "two_sigma": mean + 2 * std, "n": len(mags)}


def magnitude(flow: float, base: dict) -> str:
    m = abs(flow)
    buy = flow > 0
    if base["two_sigma"] and m >= base["two_sigma"]:
        return "大幅加碼" if buy else "大幅減碼"
    if base["one_sigma"] and m >= base["one_sigma"]:
        return "加碼" if buy else "減碼"
    return "買進" if buy else "賣出"


def aggregate(hist_flows: dict[str, dict[str, dict]], tags: dict,
              baselines: dict) -> tuple[list, list, list]:
    """Combine per-ETF stock flows into per-stock, per-tag, per-concept rows.

    hist_flows: {etf: {id: {name, flow, dshares}}}
    """
    etfs = list(hist_flows)
    # ---- per stock ----
    stock_rows: dict[str, dict] = {}
    for etf, flows in hist_flows.items():
        for sid, v in flows.items():
            tg = tags.get(sid, {})
            r = stock_rows.setdefault(sid, {
                "id": sid, "name": v["name"],
                "category": tg.get("category") or UNTAGGED,
                "group": tg.get("group") or "",
                "concepts": [c["name"] for c in tg.get("concepts", [])][:4],
                "flow_by_etf": {}, "flow_total": 0.0,
                "mag_by_etf": {}, "n_buyers": 0, "n_sellers": 0,
            })
            r["flow_by_etf"][etf] = round(v["flow"], 4)
            r["flow_total"] += v["flow"]
            r["mag_by_etf"][etf] = magnitude(v["flow"], baselines[etf])
            if v["flow"] > 0:
                r["n_buyers"] += 1
            elif v["flow"] < 0:
                r["n_sellers"] += 1
    for r in stock_rows.values():
        r["flow_total"] = round(r["flow_total"], 4)
        # strongest single-ETF magnitude label, worst (大幅) first
        order = {"大幅加碼": 3, "大幅減碼": 3, "加碼": 2, "減碼": 2, "買進": 1, "賣出": 1}
        r["mag"] = max(r["mag_by_etf"].values(), key=lambda m: order.get(m, 0)) \
            if r["mag_by_etf"] else ""
    stocks = sorted(stock_rows.values(), key=lambda r: -abs(r["flow_total"]))

    # ---- per category tag ----
    def group_rows(key_fn):
        acc: dict[str, dict] = {}
        for r in stock_rows.values():
            for key, group in key_fn(r):
                a = acc.setdefault(key, {"tag": key, "group": group,
                                         "flow_total": 0.0,
                                         "flow_by_etf": {e: 0.0 for e in etfs},
                                         "stocks": []})
                a["flow_total"] += r["flow_total"]
                for e, f in r["flow_by_etf"].items():
                    a["flow_by_etf"][e] += f
                a["stocks"].append({"id": r["id"], "name": r["name"],
                                    "flow": r["flow_total"]})
        rows = []
        for a in acc.values():
            a["flow_total"] = round(a["flow_total"], 4)
            a["flow_by_etf"] = {e: round(v, 4) for e, v in a["flow_by_etf"].items()}
            a["n_etf"] = sum(1 for v in a["flow_by_etf"].values() if abs(v) > 1e-6)
            a["stocks"] = sorted(a["stocks"], key=lambda s: -abs(s["flow"]))[:8]
            rows.append(a)
        return sorted(rows, key=lambda a: -abs(a["flow_total"]))

    tag_rows = group_rows(lambda r: [(r["category"], r["group"])])
    concept_rows = group_rows(
        lambda r: [(c, "概念股") for c in r["concepts"]]
    )
    return stocks, tag_rows, concept_rows


def main() -> int:
    tags = load_history(TAGS_FILE).get("tags", {})
    hists = {e: load_history(p) for e, p in ETFS.items() if p.exists()}
    hists = {e: h for e, h in hists.items() if h}
    if not hists:
        print("no ETF histories found")
        return 1

    # common session axis = union of dates, sorted; each ETF uses its own nearest
    all_dates = sorted({d for h in hists.values() for d in h})
    cur = all_dates[-1]
    prev = all_dates[-2]
    base5 = all_dates[-min(WINDOW + 1, len(all_dates))]
    sessions = all_dates[-min(WINDOW, len(all_dates) - 1):]  # last WINDOW pairs' cur dates

    baselines = {e: baseline_stats(sorted(h), h) for e, h in hists.items()}

    def flows_for(cur_d: str, base_d: str) -> dict[str, dict[str, dict]]:
        res = {}
        for e, h in hists.items():
            hd = sorted(h)
            # nearest available <= requested date for this ETF
            c = max([d for d in hd if d <= cur_d], default=None)
            b = max([d for d in hd if d <= base_d], default=None)
            if c and b and c != b:
                res[e] = flow_between(h[c], h[b])
        return res

    today_stocks, today_tags, today_concepts = aggregate(
        flows_for(cur, prev), tags, baselines)
    d5_stocks, d5_tags, d5_concepts = aggregate(
        flows_for(cur, base5), tags, baselines)

    # ---- heatmap: per-session net flow per top category tag ----
    session_pairs = list(zip(all_dates[-(WINDOW + 1):], all_dates[-WINDOW:]))
    per_session_tag: list[dict] = []
    for b, c in session_pairs:
        _, tg, _ = aggregate(flows_for(c, b), tags, baselines)
        per_session_tag.append({t["tag"]: t["flow_total"] for t in tg})
    heat_tags = [t["tag"] for t in d5_tags if t["tag"] != UNTAGGED][:14]
    heatmap = {
        "tags": heat_tags,
        "dates": [c for _, c in session_pairs],
        "matrix": [[round(ps.get(t, 0.0), 4) for ps in per_session_tag]
                   for t in heat_tags],
    }

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "etfs": list(hists),
        "dates": {"cur": cur, "prev": prev, "base5": base5, "sessions": sessions},
        "baseline": {e: {k: round(v, 4) if isinstance(v, float) else v
                         for k, v in b.items()} for e, b in baselines.items()},
        "today": {"stocks": today_stocks, "tags": today_tags,
                  "concepts": today_concepts},
        "d5": {"stocks": d5_stocks, "tags": d5_tags, "concepts": d5_concepts},
        "heatmap": heatmap,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}  cur={cur} prev={prev} base5={base5} "
          f"tags={len(today_tags)} stocks={len(today_stocks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

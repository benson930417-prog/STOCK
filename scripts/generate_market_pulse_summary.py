"""Generate cached 市場脈動 screenshot images for LINE.

This follows the same deployment idea as generate_etf_summary.py: render once
during the daily update job, commit the image, and let LINE serve the cached
latest image quickly instead of driving Streamlit on every tap.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]
SUMMARY_DIR = ROOT_DIR / "data" / "summaries"
DB_PATH = ROOT_DIR / "data" / "etf_bench" / "etf_bench.sqlite"
MARKET_PULSE_URL = os.environ.get("MARKET_PULSE_URL", "http://127.0.0.1:8501")
MARKET_PULSE_LABEL = "\u5e02\u5834\u8108\u52d5"  # 市場脈動


def latest_taiex_date() -> str:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE ticker = '^TWII'").fetchone()
    if not row or not row[0]:
        raise RuntimeError("No ^TWII price date found in etf_bench DB")
    return str(row[0])


def compress_image(path: Path) -> None:
    img = Image.open(path).convert("RGB")
    max_width = 1200
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.Resampling.LANCZOS)
    img.save(path, format="JPEG", quality=88, optimize=True, progressive=True)


def click_market_pulse_tab(page) -> None:
    page.wait_for_selector("body", state="attached", timeout=60_000)
    page.wait_for_function("document.readyState !== 'loading'", timeout=30_000)
    try:
        page.wait_for_selector('[data-testid="stAppViewContainer"], .stApp, [role="tab"]', state="attached", timeout=60_000)
    except Exception as app_exc:
        print(f"Streamlit app shell not detected yet, trying tab click anyway: {app_exc}", flush=True)

    try:
        page.get_by_role("tab", name=MARKET_PULSE_LABEL).click(timeout=15_000)
        return
    except Exception as role_exc:
        print(f"role-tab click failed, falling back to text click: {role_exc}", flush=True)

    clicked = page.evaluate(
        """(label) => {
            const nodes = Array.from(document.querySelectorAll('button, [role="tab"], div, span, p'));
            const target = nodes.find((node) => {
                const text = (node.innerText || node.textContent || '').trim();
                return text === label || text.includes(label);
            });
            if (!target) return false;
            target.click();
            return true;
        }""",
        MARKET_PULSE_LABEL,
    )
    if not clicked:
        body = page.locator("body").inner_text(timeout=5_000)
        raise RuntimeError(f"Market pulse tab not found. Body head: {body[:500]}")


def generate() -> tuple[str, Path, Path]:
    latest_date = latest_taiex_date()
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = SUMMARY_DIR / "market_pulse_latest.jpg"
    dated_path = SUMMARY_DIR / f"market_pulse_{latest_date}.jpg"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1800}, device_scale_factor=1)
        page.goto(MARKET_PULSE_URL, wait_until="domcontentloaded", timeout=60_000)
        click_market_pulse_tab(page)
        page.wait_for_timeout(4_000)
        page.add_style_tag(content="""
            html, body, [data-testid="stAppViewContainer"] {
                visibility: visible !important;
                opacity: 1 !important;
            }
            header, [data-testid="stToolbar"], [data-testid="stDecoration"],
            [data-testid="stStatusWidget"], #MainMenu, footer {
                display: none !important;
            }
            .block-container {
                padding-top: 1.2rem !important;
                padding-bottom: 1.2rem !important;
                max-width: 1500px !important;
            }
        """)
        page.screenshot(path=str(latest_path), type="jpeg", quality=88, full_page=False)
        browser.close()

    compress_image(latest_path)
    shutil.copyfile(latest_path, dated_path)
    print(f"Saved {latest_path.relative_to(ROOT_DIR)}")
    print(f"Saved {dated_path.relative_to(ROOT_DIR)}")
    return latest_date, latest_path, dated_path


if __name__ == "__main__":
    try:
        generate()
    except Exception as exc:
        print(f"generate_market_pulse_summary failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

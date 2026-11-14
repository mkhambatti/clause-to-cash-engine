"""Regenerate docs/img/*.png from docs/demo/index.html (needs `playwright` + chromium; optional)."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
html, out = root / "docs" / "demo" / "index.html", root / "docs" / "img"
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=2)
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(html.as_uri())
    pg.wait_for_timeout(400)
    pg.screenshot(path=str(out / "overview.png"), full_page=True)
    pg.click('nav button[data-k="review"]')
    pg.click(".frow >> nth=3")
    pg.wait_for_timeout(500)
    pg.screenshot(path=str(out / "review.png"))
    b.close()
    if errs:
        sys.exit(f"JS errors: {errs}")
print("ok")

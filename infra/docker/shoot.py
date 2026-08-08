#!/usr/bin/env python3
"""Capture console screenshots for docs (used in CI/docs refresh)."""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = "docs/screenshots"

SHOTS = [
    ("overview", None, 2.5),
    ("graph", "◈ RISK GRAPH", 5.0),
    ("cases", "▤ INVESTIGATIONS", 2.0),
    ("sim", "◬ SIMULATION", 1.0),
    ("console", "▚ DECISION CONSOLE", 1.5),
]


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1500, "height": 940},
                                      device_scale_factor=1.5)
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(3500)
        for name, button, wait in SHOTS:
            if button:
                await page.click(f"button:has-text('{button}')")
            await page.wait_for_timeout(int(wait * 1000))
            if name == "sim":
                await page.click("#sim-run")
                await page.wait_for_timeout(4000)
            if name == "console":
                await page.click("#eval-form button[type=submit]")
                await page.wait_for_timeout(1200)
            await page.screenshot(path=f"{OUT}/{name}.png")
            print(f"captured {name}")
        await browser.close()


asyncio.run(main())

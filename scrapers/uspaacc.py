from __future__ import annotations
import os, time
from typing import Iterable, Optional, List

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from bs4 import BeautifulSoup, Tag
from urllib.parse import urlparse

from scraper_base import Scraper, Company

DEFAULT_NAME_SELECTOR = "p.font-semibold.text-gray-700.mt-2.leading-snug"
ALT_NAME_SELECTORS = [
    DEFAULT_NAME_SELECTOR,
    "p.font-semibold.leading-snug",
    "p.font-semibold",
]

DEBUG = os.getenv("SCRAPER_DEBUG", "0") == "1"

def _text(el: Tag) -> str:
    return (el.get_text(strip=True) or "").strip()

class USPAACCScraper(Scraper):
    """
    Playwright-based scraper for USPAACC Members (names only).
    We extract the company names and let the SearchResolver find websites.
    """

    def __init__(self, url: str, name_selector: str = DEFAULT_NAME_SELECTOR, container_ancestors: int = 5, **_):
        self.url = url
        self.name_selector = name_selector or DEFAULT_NAME_SELECTOR
        self.alt_selectors = list(dict.fromkeys([self.name_selector] + ALT_NAME_SELECTORS))

    def _open_page(self):
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        return pw, browser, ctx, page

    def _close_page(self, pw, browser, ctx):
        try:
            ctx.close()
            browser.close()
        finally:
            pw.stop()

    def iter_companies(self, max_items: int | None = None) -> Iterable[Company]:
        pw, browser, ctx, page = self._open_page()
        try:
            page.goto(self.url, wait_until="domcontentloaded", timeout=60000)

            # Scroll to load more
            prev_h = 0
            for _ in range(20):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(700)
                cur_h = page.evaluate("document.body.scrollHeight")
                if cur_h == prev_h:
                    break
                prev_h = cur_h

            page.wait_for_load_state("networkidle", timeout=6000)
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            nodes = []
            chosen = ""
            for css in self.alt_selectors:
                nodes = soup.select(css)
                if nodes:
                    chosen = css
                    break

            if DEBUG:
                print(f"[DEBUG] name_nodes found: {len(nodes)} using selector: {chosen}")

            seen = set()
            count = 0
            for el in nodes:
                name = _text(el)
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())

                yield {"name": name, "website": None}  # website to be resolved later
                count += 1
                if max_items and count >= max_items:
                    break
                time.sleep(0.02)
        finally:
            self._close_page(pw, browser, ctx)

from __future__ import annotations
import os
import time
from typing import Iterable, Optional, List

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup, Tag

from scraper_base import Scraper, Company

# The page uses anchors like:
# <a class="popup-modal" href="/membership-directory/corporate/2745907#page-member-ajax">
#   A&A Tax & Accounting Group LLC
# </a>

# Default selectors for company anchors in the AACC directory
DEFAULT_NAME_SELECTOR = 'a.popup-modal[href*="/membership-directory/"]'
ALT_NAME_SELECTORS = [
    DEFAULT_NAME_SELECTOR,
    'a[href*="/membership-directory/"]',
]

# Any anchors with these labels should be ignored (case-insensitive)
IGNORE_LABELS = {
    "more info",
    "more information",
    "learn more",
    "view more",
    "view details",
    "details",
    "view profile",
    "profile",
}

DEBUG = os.getenv("SCRAPER_DEBUG", "0") == "1"


def _text(el: Tag) -> str:
    return (el.get_text(strip=True) or "").strip()


class AACCScraper(Scraper):
    """
    Playwright-based scraper for the AACC corporate directory:
      https://www.asian-americanchamber.org/membership-directory/corporate

    We ONLY extract company names; on-page links are not reliable.
    Website resolution is handled later by SearchResolver.
    """

    def __init__(
        self,
        url: str,
        name_selector: str = DEFAULT_NAME_SELECTOR,
        container_ancestors: int = 0,  # ignored but kept for compat with main.py
        **_,
    ):
        self.url = url
        # normalize to a list of selectors (preferred first)
        self.name_selectors: List[str] = list(
            dict.fromkeys([name_selector] + ALT_NAME_SELECTORS)
        )
        self.scroll_rounds = 22  # a few extra scrolls to load more cards

    # ---------- Playwright helpers ----------
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

    # ---------- Public API ----------
    def iter_companies(self, max_items: int | None = None) -> Iterable[Company]:
        pw, browser, ctx, page = self._open_page()
        try:
            page.goto(self.url, wait_until="domcontentloaded", timeout=60000)

            # Lazy-load by scrolling
            prev_h = 0
            for _ in range(self.scroll_rounds):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(700)
                cur_h = page.evaluate("document.body.scrollHeight")
                if cur_h == prev_h:
                    break
                prev_h = cur_h

            page.wait_for_load_state("networkidle", timeout=6000)
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Find candidate name nodes
            nodes: List[Tag] = []
            used_css = ""
            for css in self.name_selectors:
                cand = soup.select(css)
                if cand:
                    nodes = cand
                    used_css = css
                    break

            if DEBUG:
                print(f"[AACC DEBUG] name_nodes found: {len(nodes)} using selector: {used_css}")

            seen = set()
            count = 0

            for el in nodes:
                label = _text(el)
                if not label:
                    continue

                # Skip “More Info” and similar non-name labels
                if label.strip().lower() in IGNORE_LABELS:
                    continue

                # Basic sanity on company name
                if len(label) < 2:
                    continue

                key = label.lower()
                if key in seen:
                    continue
                seen.add(key)

                # Return name only; SearchResolver will find the website
                yield {"name": label, "website": None}

                count += 1
                if max_items and count >= max_items:
                    break

                time.sleep(0.02)

        finally:
            self._close_page(pw, browser, ctx)

from __future__ import annotations
import os
import re
import time
from typing import Iterable, Optional, List, Set

import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from scraper_base import Scraper, Company

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}
DEBUG = os.getenv("SCRAPER_DEBUG", "0") == "1"
REQUEST_TIMEOUT = int(os.getenv("AACCIL_REQUEST_TIMEOUT_SECS", "15"))
REQUEST_DELAY = float(os.getenv("AACCIL_REQUEST_DELAY_SECS", "0.2"))  # polite crawl


def _text(el: Tag) -> str:
    return (el.get_text(strip=True) or "").strip()


def _is_business_anchor(a: Tag) -> bool:
    """Accept anchors like: <a href='https://aaccil.org/business/...'>Name</a>"""
    href = (a.get("href") or "").strip()
    if not href:
        return False
    # Exact business item pages live under /business/
    return "/business/" in href and "sf_paged=" not in href  # exclude pagination


def _page_url(base_url: str, page_num: int) -> str:
    """
    Build page URL:
      page 1: base url (no param)
      page N>=2: base?sf_paged=N
    """
    if page_num <= 1:
        return base_url
    parts = list(urlparse(base_url))
    q = parse_qs(parts[4], keep_blank_values=True)
    q["sf_paged"] = [str(page_num)]
    # rebuild query in stable order
    parts[4] = urlencode({k: v[0] for k, v in q.items()}, doseq=False)
    return urlunparse(parts)


class AACCILScraper(Scraper):
    """
    Scraper for the AACCIL business directory:
      https://aaccil.org/business-directory/
    Emits: {"name": <company_name>, "website": None}
    """

    def __init__(self, url: str, name_selector: str = "", container_ancestors: int = 0, **_):
        self.url = url

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                return None
            return r.text
        except requests.RequestException:
            return None

    def _detect_last_page(self, soup: BeautifulSoup) -> int:
        """
        Prefer parsing 'Page 1 of 39' text; fallback to last page link.
        """
        # 1) 'Page X of Y'
        page_span = soup.select_one(".wp-pagenavi .pages")
        if page_span:
            m = re.search(r"Page\s+\d+\s+of\s+(\d+)", _text(page_span), re.I)
            if m:
                try:
                    return max(1, int(m.group(1)))
                except ValueError:
                    pass

        # 2) 'Last »' link with sf_paged=Y
        last_a = soup.select_one(".wp-pagenavi a.last, .pagination a.last")
        if last_a and last_a.get("href"):
            href = last_a["href"]
            qs = parse_qs(urlparse(href).query)
            if "sf_paged" in qs:
                try:
                    return max(1, int(qs["sf_paged"][0]))
                except (ValueError, IndexError):
                    pass

        # 3) Fallback: check the max numeric page link visible
        nums = []
        for a in soup.select(".wp-pagenavi a.page, .wp-pagenavi a.larger"):
            try:
                nums.append(int(_text(a)))
            except ValueError:
                pass
        return max(nums) if nums else 1

    def _extract_names_from_page(self, soup: BeautifulSoup) -> List[str]:
        names: List[str] = []
        for a in soup.select('a[href*="/business/"]'):
            if not _is_business_anchor(a):
                continue
            label = _text(a)
            if not label or len(label) < 2:
                continue
            # Filter out obvious non-names (rare, but safe)
            lo = label.lower()
            if lo in {"more info", "learn more", "view more", "view details", "details", "profile"}:
                continue
            names.append(label)
        return names

    def iter_companies(self, max_items: int | None = None) -> Iterable[Company]:
        # 1) Fetch page 1 to detect total pages
        first_html = self._fetch_html(self.url)
        if not first_html:
            if DEBUG:
                print("[AACCIL DEBUG] Failed to fetch page 1")
            return

        soup = BeautifulSoup(first_html, "html.parser")
        last_page = self._detect_last_page(soup)
        if DEBUG:
            print(f"[AACCIL DEBUG] detected last_page={last_page}")

        seen: Set[str] = set()
        yielded = 0

        # 2) Iterate pages
        for p in range(1, last_page + 1):
            page_url = _page_url(self.url, p)
            html = first_html if p == 1 else self._fetch_html(page_url)
            if not html:
                if DEBUG:
                    print(f"[AACCIL DEBUG] skip p{p}: fetch failed")
                continue

            psoup = soup if p == 1 else BeautifulSoup(html, "html.parser")
            page_names = self._extract_names_from_page(psoup)

            if DEBUG:
                print(f"[AACCIL DEBUG] p{p}: found {len(page_names)} names")

            for name in page_names:
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)

                yield {"name": name, "website": None}
                yielded += 1
                if max_items and yielded >= max_items:
                    return

            time.sleep(REQUEST_DELAY)

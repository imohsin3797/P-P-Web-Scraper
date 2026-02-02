from __future__ import annotations
import os
import sys
import time
import signal
from typing import List, Tuple, Optional
from urllib.parse import urlparse, urlunparse

import requests

from config import AppConfig
from gpt_filter import GPTFilter
from sheets import SheetWriter
from scrapers.uspaacc import USPAACCScraper
from scrapers.aacc import AACCScraper
from search_resolver import SearchResolver
from scrapers.aaccil import AACCILScraper

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)

if not Path(__file__).with_name(".env").exists():
    from dotenv import find_dotenv
    load_dotenv(find_dotenv(), override=False)

# Register all scrapers here
SCRAPER_REGISTRY = {
    "uspaacc": USPAACCScraper,
    "aacc": AACCScraper,
    "aaccil": AACCILScraper,            
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

TIMEOUT_SECS = int(os.getenv("URL_CHECK_TIMEOUT_SECS", "10"))
DROP_DEAD_LINKS = os.getenv("DROP_DEAD_LINKS", "1") == "1"
ALLOW_HTTP = os.getenv("ALLOW_HTTP", "0") == "1"

# Hard budget per company (search + live check + GPT), seconds
PER_COMPANY_BUDGET_SECS = float(os.getenv("PER_COMPANY_BUDGET_SECS", "15"))
# How much of the budget to allocate to search resolution (rest goes to live check + GPT)
RESOLVER_BUDGET_FRACTION = float(os.getenv("RESOLVER_BUDGET_FRACTION", "0.65"))
MIN_RESOLVER_SCORE = int(os.getenv("MIN_RESOLVER_SCORE", "35"))  # lower to 30 if too strict

# Use Unix signal-based hard timeouts (works on macOS/Linux main thread)
USE_SIGNAL_TIMEOUT = os.getenv("USE_SIGNAL_TIMEOUT", "1") == "1"


def _normalize_url(url: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    lo = url.lower()
    if lo.startswith(("mailto:", "tel:", "javascript:", "data:", "about:")):
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    scheme = parsed.scheme
    if scheme == "http" and not ALLOW_HTTP:
        scheme = "https"
    cleaned = parsed._replace(scheme=scheme, fragment="")
    return urlunparse(cleaned)


def _check_url_live(url: str, timeout: int = TIMEOUT_SECS):
    try:
        head = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=timeout)
        if 200 <= head.status_code < 400:
            return True, head.url, head.status_code
        if head.status_code in (400, 401, 403, 405, 500):
            get = requests.get(
                url, headers=HEADERS, allow_redirects=True, timeout=timeout, stream=True
            )
            sc2, final2 = get.status_code, get.url
            try:
                get.close()
            except Exception:
                pass
            if 200 <= sc2 < 400:
                return True, final2, sc2
        return False, head.url, head.status_code
    except requests.RequestException:
        return False, url, None


def _contains_any(s: str, needles: List[str]) -> bool:
    lo = (s or "").lower()
    return any(n in lo for n in needles)


# ---- Hard timeout helpers (Unix) ----
class _Timeout(Exception):
    pass


def _sigalrm_handler(signum, frame):
    raise _Timeout()


def _run_with_alarm(seconds: float, fn, *args, **kwargs):
    """Run fn(...) with a hard timeout using SIGALRM. Unix/macOS only."""
    # SIGALRM only takes integer seconds
    secs = max(1, int(seconds))
    prev_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _sigalrm_handler)
        signal.alarm(secs)
        return fn(*args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev_handler)


def run(scraper_key: str):
    cfg = AppConfig.load()
    if scraper_key not in SCRAPER_REGISTRY:
        raise SystemExit(f"Unknown scraper '{scraper_key}'. Options: {list(SCRAPER_REGISTRY.keys())}")

    scraper_cls = SCRAPER_REGISTRY[scraper_key]
    scfg = cfg.scrapers.get(scraper_key, {})
    url = scfg.get("url")
    if not url:
        raise SystemExit("Missing scraper URL in config.yaml")

    # Build scraper (name-only scrapers are fine; website is resolved via SearchResolver)
    scraper = scraper_cls(
        url=url,
        name_selector=scfg.get("name_selector") or "p.font-semibold.text-gray-700.mt-2.leading-snug",
        container_ancestors=int(scfg.get("container_ancestors", 5)),
        external_link_keywords=scfg.get("external_link_keywords"),
        profile_link_keywords=scfg.get("profile_link_keywords"),
        blacklist_domains=scfg.get("blacklist_domains"),
        name_stopwords=scfg.get("name_stopwords"),
    )

    # Website resolver (search)
    resolver = SearchResolver()

    # GPT (optional)
    gpt = None
    if cfg.enable_gpt:
        if not cfg.openai_api_key:
            raise SystemExit("OPENAI_API_KEY not set. Fill .env.")
        gpt = GPTFilter(api_key=cfg.openai_api_key, model=cfg.openai_model, thesis=cfg.search_thesis)

    # Sheets (optional)
    sheet = None
    if cfg.enable_sheets:
        if not cfg.sheet_id:
            raise SystemExit("GOOGLE_SHEET_ID not set. Fill .env.")
        sheet = SheetWriter(sheet_id=cfg.sheet_id, tab_name=cfg.sheet_tab)

    blacklist_substrings = [b.lower() for b in scfg.get("blacklist_domains", [])]

    rows: List[List[str]] = []
    preview_rows: List[Tuple[str, str, str, str]] = []
    count_total = count_included = count_dead = count_no_site = 0

    max_items = cfg.max_companies or scfg.get("max_listings")

    for company in scraper.iter_companies(max_items=max_items):
        start_ts = time.monotonic()
        name = (company.get("name") or "").strip()
        if not name:
            continue
        count_total += 1

        # ---- 1) Resolve website via search (hard time budget on Unix) ----
        website = None
        search_budget = max(1.0, PER_COMPANY_BUDGET_SECS * RESOLVER_BUDGET_FRACTION)

        try:
            if USE_SIGNAL_TIMEOUT and hasattr(signal, "SIGALRM"):
                website = _run_with_alarm(
                    search_budget,
                    resolver.resolve,
                    name,
                    min_score=MIN_RESOLVER_SCORE,
                )
            else:
                # Soft budget fallback: just call; we'll check total elapsed later
                website = resolver.resolve(name, min_score=MIN_RESOLVER_SCORE)
        except _Timeout:
            preview_rows.append((name, "Unknown", "", f"Timed out (> {int(search_budget)}s) during search"))
            continue
        except Exception:
            website = None

        if not website:
            count_no_site += 1
            preview_rows.append((name, "Unknown", "", "No site found via search"))
            continue

        link = _normalize_url(website)
        if not link:
            preview_rows.append((name, "Unknown", website, "SKIP: non-http(s)"))
            continue

        if _contains_any(link, blacklist_substrings):
            preview_rows.append((name, "Unknown", link, "SKIP: blacklist domain"))
            continue

        # If we already overspent budget (soft fallback path), bail
        if (time.monotonic() - start_ts) > PER_COMPANY_BUDGET_SECS and not USE_SIGNAL_TIMEOUT:
            preview_rows.append((name, "Unknown", link, f"Timed out (> {PER_COMPANY_BUDGET_SECS:.0f}s) during search"))
            continue

        # ---- 2) Live check (hard timeout using alarm with remaining budget) ----
        remaining = PER_COMPANY_BUDGET_SECS - (time.monotonic() - start_ts)
        if remaining <= 0 and not USE_SIGNAL_TIMEOUT:
            preview_rows.append((name, "Unknown", link, "Timed out before live check"))
            continue
        live_budget = max(1.0, min(remaining, TIMEOUT_SECS))

        try:
            if USE_SIGNAL_TIMEOUT and hasattr(signal, "SIGALRM"):
                is_live, final_url, status = _run_with_alarm(
                    live_budget, _check_url_live, link, int(live_budget)
                )
            else:
                is_live, final_url, status = _check_url_live(link, int(live_budget))
        except _Timeout:
            preview_rows.append((name, "Unknown", link, f"Timed out (> {int(live_budget)}s) during live check"))
            continue

        if not is_live:
            preview_rows.append((name, "Unknown", link, f"DEAD link (status={status})"))
            if DROP_DEAD_LINKS:
                count_dead += 1
                continue
        else:
            link = final_url

        # ---- 3) GPT filter: include + 3–5 word industry (use whatever time remains) ----
        industry = "Unknown"
        include = True
        if gpt is not None:
            # Keep GPT call quick—let OpenAI handle internal timeouts
            decision = gpt.decide({"name": name, "website": link})
            include = bool(decision.get("include", False))
            industry = (decision.get("industry_short") or "Unknown").strip()
        else:
            industry = "TBD"
            include = True

        if not include:
            preview_rows.append((name, industry, link, "Filtered out by GPT"))
            continue

        rows.append([name, industry, link])
        count_included += 1

        if sheet and len(rows) >= 50:
            sheet.append_rows(rows)
            rows.clear()

        # soft rate-limit
        time.sleep(0.03)

    if sheet and rows:
        sheet.append_rows(rows)

    if not sheet:
        preview_n = 25
        print("\n--- Preview (first {} rows that WOULD be written to Sheet) ---".format(min(preview_n, len(rows))))
        for r in rows[:preview_n]:
            print(f"Name: {r[0]}  |  Industry: {r[1]}  |  Link: {r[2]}")
        if len(rows) == 0:
            print("(No rows passed filters / live-link checks.)")
        elif len(rows) > preview_n:
            print(f"... and {len(rows) - preview_n} more rows.\n")

        print("\n--- Skipped / Info (first 20) ---")
        for name, ind, link, note in preview_rows[:20]:
            print(f"- {name} | {ind} | {link} -> {note}")
        if len(preview_rows) > 20:
            print(f"... and {len(preview_rows) - 20} more skipped/info rows.\n")

    print({
        "scraper": scraper_key,
        "total_found": count_total,
        "resolved_site_missing": count_no_site,
        "dead_links_skipped": count_dead,
        "included": count_included,
        "pushed_to_sheets": bool(sheet),
        "drop_dead_links": DROP_DEAD_LINKS,
        "per_company_budget_secs": PER_COMPANY_BUDGET_SECS,
        "resolver_budget_fraction": RESOLVER_BUDGET_FRACTION,
    })


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python main.py <scraper_key>  # e.g., python main.py uspaacc | aacc")
    run(sys.argv[1])

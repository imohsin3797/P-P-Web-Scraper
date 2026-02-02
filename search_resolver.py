from __future__ import annotations
import os
import re
import json
import pathlib
from typing import List, Optional, Dict

import httpx
import backoff
import tldextract
from rapidfuzz import fuzz

# -------------------------
# Config & small utilities
# -------------------------

# Common “non-official” domains to de-prioritize
SOCIAL_HOSTS = {
    "linkedin.com","facebook.com","instagram.com","x.com","twitter.com",
    "youtube.com","crunchbase.com","bloomberg.com","zoominfo.com",
    "manta.com","yelp.com","glassdoor.com","indeed.com","angel.co",
    "wikipedia.org","maps.google.com","google.com","goo.gl"
}

# Blacklist obvious non-targets
BLACKLIST_SUBSTR = {
    "eventbrite","hubspot","forms.gle","zoom.us"
}

# On-disk cache to avoid re-querying the same name
CACHE_PATH = pathlib.Path(os.getenv("SEARCH_CACHE_PATH", ".search_cache.json"))
try:
    _CACHE: Dict[str, str] = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
except Exception:
    _CACHE = {}

def normalize_company_name(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[,.\-&/|]+", " ", s)
    s = re.sub(
        r"\b(incorporated|inc|co|corp|corporation|llc|l\.l\.c|ltd|limited|group|holdings|partners|technologies|technology|tech|systems|solutions|services|company)\b",
        "",
        s,
    )
    s = re.sub(r"\s+", " ", s).strip()
    return s

def extract_registrable_host(url: str) -> str:
    try:
        ext = tldextract.extract(url)
        if not ext.domain:
            return ""
        return ".".join([p for p in [ext.domain, ext.suffix] if p])
    except Exception:
        return ""

def token_set_ratio(a: str, b: str) -> int:
    return int(fuzz.token_set_ratio(a, b))

def penalty_for_url(url: str) -> int:
    u = url.lower()
    host = extract_registrable_host(url)
    if host in SOCIAL_HOSTS:
        return -60
    if any(x in u for x in BLACKLIST_SUBSTR):
        return -40
    # Penalize very deep paths and query fragments
    depth = u.count("/") - 2
    q = ("?" in u) + ("#" in u)
    return - (depth * 4 + q * 5)

def score_candidate(company_norm: str, title: str, url: str, snippet: str) -> float:
    title = (title or "").lower()
    snippet = (snippet or "").lower()
    host = extract_registrable_host(url)
    host_core = host.split(".")[0] if host else ""
    sim_host = token_set_ratio(company_norm, host_core)
    sim_title = token_set_ratio(company_norm, title)

    score = 0.0
    score += 0.7 * sim_host
    score += 0.3 * sim_title
    if "official" in title or "home" in title:
        score += 5
    score += min(8, token_set_ratio(company_norm, snippet) / 12)
    score += penalty_for_url(url)
    return score

# -------------------------
# Resolver implementation
# -------------------------

class SearchResolver:
    """
    Resolves an "official" website for a company name using a web search API.
    Supports:
      - Google CSE JSON API (recommended: cheap, reliable)
      - SerpAPI (alternative)
    Adds:
      - 1 query/company by default (keeps cost down)
      - On-disk JSON cache across runs
      - Tight HTTP timeouts + exponential backoff
    """

    def __init__(self):
        self.provider = os.getenv("SEARCH_PROVIDER", "google_cse").lower()
        self.debug = os.getenv("SEARCH_DEBUG", "0") == "1"

        # Tight but reasonable timeouts
        self._timeout = httpx.Timeout(connect=4.0, read=6.0, write=4.0, pool=4.0)

        if self.provider == "google_cse":
            self.key = os.getenv("GOOGLE_CSE_API_KEY")
            self.cx = os.getenv("GOOGLE_CSE_CX")
            if not (self.key and self.cx):
                raise RuntimeError("GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX required for SEARCH_PROVIDER=google_cse")
        elif self.provider == "serpapi":
            self.key = os.getenv("SERPAPI_API_KEY")
            if not self.key:
                raise RuntimeError("SERPAPI_API_KEY missing while SEARCH_PROVIDER=serpapi")
        else:
            raise RuntimeError(f"Unknown SEARCH_PROVIDER={self.provider}")

        # Whether to run a second (optional) query per name
        self.extra_query = os.getenv("SEARCH_EXTRA_QUERY", "0") == "1"

    @backoff.on_exception(backoff.expo, (httpx.HTTPError,), max_time=8, max_tries=3)
    def _http_json(self, url: str, params: Dict[str, str]) -> dict:
        with httpx.Client(timeout=self._timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r.json()

    # ---------- Providers ----------
    def _google_cse(self, query: str) -> List[dict]:
        try:
            data = self._http_json(
                "https://www.googleapis.com/customsearch/v1",
                {"key": self.key, "cx": self.cx, "q": query, "num": "10"},
            )
        except Exception:
            return []
        out = []
        for it in data.get("items", [])[:10]:
            out.append({"title": it.get("title"), "link": it.get("link"), "snippet": it.get("snippet")})
        return out

    def _serpapi(self, query: str) -> List[dict]:
        try:
            data = self._http_json(
                "https://serpapi.com/search.json",
                {"engine": "google", "q": query, "api_key": self.key, "num": "10"},
            )
        except Exception:
            return []
        out = []
        for it in data.get("organic_results", [])[:10]:
            out.append(
                {
                    "title": it.get("title"),
                    "link": it.get("link"),
                    "snippet": it.get("snippet") or (it.get("snippet_highlighted_words", [""]) or [""])[0],
                }
            )
        return out

    def _search(self, query: str) -> List[dict]:
        return self._google_cse(query) if self.provider == "google_cse" else self._serpapi(query)

    # ---------- Public ----------
    def resolve(self, company_name: str, min_score: int = 35) -> Optional[str]:
        """
        Conservative selection of the official site via search.
        Uses 1 query by default to save quota; opt-in a second query with SEARCH_EXTRA_QUERY=1.
        Results are cached on disk to avoid repeat charges across runs.
        """
        if not company_name:
            return None

        # Cache hit?
        cached = _CACHE.get(company_name)
        if cached is not None:
            return cached or None

        name_norm = normalize_company_name(company_name)
        queries = [f"{company_name} official site"]
        if self.extra_query:
            queries.append(f"{company_name} company")

        best_url, best_score = None, -10_000.0

        for q in queries:
            results = self._search(q)
            for r in results:
                url = (r.get("link") or "").strip()
                if not url:
                    continue
                s = score_candidate(name_norm, r.get("title") or "", url, r.get("snippet") or "")
                if self.debug:
                    host = extract_registrable_host(url)
                    print(f"[SEARCH] {company_name} | {host:25} | score={s:.1f} | {r.get('title','')}")
                if s > best_score:
                    best_score, best_url = s, url

        res = best_url if best_url and best_score >= min_score else None

        # Persist cache (best-effort)
        _CACHE[company_name] = res or ""
        try:
            CACHE_PATH.write_text(json.dumps(_CACHE))
        except Exception:
            pass

        return res

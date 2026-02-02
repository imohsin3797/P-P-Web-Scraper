from __future__ import annotations
from typing import Iterable, Dict

Company = Dict[str, str]

class Scraper:
    """Interface for site scrapers. Implement `iter_companies` in subclasses."""
    def iter_companies(self, max_items: int | None = None) -> Iterable[Company]:
        raise NotImplementedError
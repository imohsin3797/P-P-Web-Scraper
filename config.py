from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv
import yaml

load_dotenv()

@dataclass
class AppConfig:
    openai_api_key: str
    openai_model: str
    enable_gpt: bool
    enable_sheets: bool
    max_companies: int | None
    sheet_id: str | None
    sheet_tab: str
    search_thesis: dict
    scrapers: dict

    @staticmethod
    def load(path: str = "config.yaml") -> "AppConfig":
        with open(path, "r", encoding="utf-8") as f:
            y = yaml.safe_load(f)
        return AppConfig(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            enable_gpt=os.getenv("ENABLE_GPT", "1") == "1",
            enable_sheets=os.getenv("ENABLE_SHEETS", "1") == "1",
            max_companies=int(os.getenv("MAX_COMPANIES", "150")) if os.getenv("MAX_COMPANIES") else None,
            sheet_id=os.getenv("GOOGLE_SHEET_ID"),
            sheet_tab=os.getenv("GOOGLE_SHEET_TAB", "Prospects"),
            search_thesis=y.get("search_thesis", {}),
            scrapers=y.get("scrapers", {}),
        )
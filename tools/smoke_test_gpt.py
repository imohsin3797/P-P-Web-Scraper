from __future__ import annotations
import os, sys, json
from pathlib import Path
from dotenv import load_dotenv

# Load .env explicitly from project root
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT / ".env")

# Import your code
sys.path.append(str(ROOT))
from gpt_filter import GPTFilter
from config import AppConfig

def main():
    cfg = AppConfig.load(path=str(ROOT / "config.yaml"))
    if not cfg.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing. Add it to .env")

    gpt = GPTFilter(api_key=cfg.openai_api_key, model=cfg.openai_model, thesis=cfg.search_thesis)

    # A small, mixed bag to see pass/fail and industry tagging
    samples = [
        {"name": "Acme Industrial Compliance Services", "website": "https://acme-compliance.example"},
        {"name": "Sunset Fashion Boutique", "website": "https://sunset-boutique.example"},
        {"name": "Regulatory Testing Labs, Inc.", "website": "https://regtestlabs.example"},
        {"name": "CryptoYOLO", "website": "https://cryptoyolo.example"},
        {"name": "Precision HVAC Service Partners", "website": "https://precision-hvac.example"},
    ]

    print("MODEL:", cfg.openai_model)
    print("---- GPT Filter Smoke Test ----")
    for c in samples:
        decision = gpt.decide(c)  # expects {"include": bool, "industry_short": str}
        include = decision.get("include")
        industry = decision.get("industry_short")
        print(f"- {c['name']:<40}  include={include!s:<5}  industry='{industry}'")

    print("\n✅ If you see sensible include/industry values above, the GPT filter is wired correctly.")

if __name__ == "__main__":
    main()

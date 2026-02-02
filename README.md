# Search Fund Scraper + GPT Filter + Google Sheets

## Prereqs
- Python 3.10+
- A Google Cloud **service account** with Sheets API access and the JSON key file
- An OpenAI API key

## Setup
1. `git clone` (or copy these files) into a folder, `cd` into it
2. `python -m venv .venv && source .venv/bin/activate` (Windows: `.venv\\Scripts\\activate`)
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill values
5. Edit `config.yaml` if needed
6. Share your Google Sheet with the **service account email** from the JSON key (Editor access)
7. Create the target tab (default `Prospects`)
8. Run: `python main.py uspaacc`

## Output Columns
- Company Name
- Industry (few words)
- Website (direct link)

## Notes
- The **scraping logic** is split into `scrapers/uspaacc.py` (site-specific) and `scraper_base.py` (interfaces)
- **GPT filter** returns a strict JSON decision `{ include: bool, industry_short: str }`
- You can add scrapers for other catalogs by copying the pattern in `scrapers/uspaacc.py`
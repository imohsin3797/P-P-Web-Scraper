from __future__ import annotations
import json
import os
from typing import Dict
from openai import OpenAI

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "include": {"type": "boolean"},
        "industry_short": {"type": "string"}
    },
    "required": ["include", "industry_short"],
    "additionalProperties": False
}

def _build_system_prompt(mode: str) -> str:
    core = (
        "You are an associate at a search fund.\n"
        "Return STRICT JSON ONLY per schema. Decide if the company should be INCLUDED in the pipeline and give a 3–5 word industry tag."
    )
    rules = (
        "INCLUSION RULES:\n"
        "- Include when it aligns with positive_signals.\n"
        "- For unclear cases: include ONLY if it appears to be a bona fide commercial company (own website, products/services pages).\n"
        "- Exclude: associations, events/conferences, government programs, and non-profits unless there is a clear fee-for-service business line likely within SMB scale.\n"
        "- Exclude: obvious consumer-only trends without defensibility, crypto, adult, gambling.\n"
        "INDUSTRY TAG: concise (e.g., 'HVAC services', 'Compliance testing').\n"
    )
    strictness = f"STRICTNESS MODE: {mode.upper()} (balanced favors precision over recall; strict is most conservative)."
    return f"{core}\n\n{rules}\n{strictness}\n"

def _build_user_prompt(thesis: dict, company: Dict[str, str]) -> str:
    name = company.get("name", "").strip()
    url = company.get("website", "").strip()
    return (
        "THESIS:\n"
        f"{json.dumps(thesis, indent=2)}\n\n"
        "TASK:\n"
        "Respond EXACTLY as JSON: {\"include\": <bool>, \"industry_short\": <str>}.\n"
        "COMPANY:\n"
        f"Name: {name}\n"
        f"Website: {url}\n"
        "NOTES:\n"
        "- Favor inclusion only for true commercial entities; exclude associations/events unless substantial fee-for-service is evident.\n"
    )

class GPTFilter:
    def __init__(self, api_key: str, model: str, thesis: dict):
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.thesis = thesis
        self.mode = os.getenv("GPT_INCLUSION_MODE", "balanced").strip().lower()
        if self.mode not in {"balanced", "strict"}:
            self.mode = "balanced"

    def decide(self, company: Dict[str, str]) -> Dict[str, str | bool]:
        sys_prompt = _build_system_prompt(self.mode)
        user_prompt = _build_user_prompt(self.thesis, company)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_schema", "json_schema": {"name": "Decision", "schema": JSON_SCHEMA, "strict": True}},
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = resp.choices[0].message.content
            data = json.loads(content)
            if not isinstance(data.get("include"), bool):
                data["include"] = False
            if not isinstance(data.get("industry_short"), str) or not data.get("industry_short").strip():
                data["industry_short"] = "Unknown"
            return data
        except Exception:
            return {"include": False, "industry_short": "Unknown"}

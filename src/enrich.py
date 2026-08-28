"""
Stage 4 + 5: clean the Chinese listing, then translate it.

One model call per product returns both languages as structured JSON, so the
cleaning and the translation can never disagree with each other. Brand names
are preserved verbatim; store names, promo copy, contact details and links are
stripped before anything is written to KDX.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4.1-mini"

SYSTEM_PROMPT = """You clean and translate product listings taken from 1688 for a Saudi online store.

Remove completely, never translate:
- shop or factory names, and phrases like 旗舰店 / 专营店 / 厂家直销
- any URL, WeChat/QQ/phone/contact detail
- promotional copy: discounts, coupons, "free shipping", "add me for a better price"
- decorative symbols, bracket spam like 【】★☆, and truncated or garbled fragments
- anything sexual, religious, weapon, drug, tobacco or counterfeit related

Keep and translate faithfully:
- what the product actually is, its material, dimensions, capacity, contents
- brand names stay EXACTLY as written in the original, untranslated
- technical and commercial terms stay accurate (220V, 50/60Hz, cotton, nylon)

Write like a real store, not like a translation. Arabic must read naturally to a
Saudi customer. Never invent a feature that is not in the source.

Return ONLY this JSON object:
{"name_ar":"","name_en":"","description_ar":"","description_en":"","keywords":[],"category":""}
name_* max 70 characters. description_* 2-4 clean sentences. keywords: 5-8 Arabic search terms."""


class EnrichError(RuntimeError):
    pass


def _strip_code_fence(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def enrich(title_zh: str, description_zh: str, api_key: str | None = None,
           timeout: int = 60) -> dict:
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise EnrichError("no API key configured")

    payload = {
        "model": MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"TITLE:\n{title_zh}\n\nDESCRIPTION:\n{description_zh}"},
        ],
    }

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))

    content = _strip_code_fence(body["choices"][0]["message"]["content"])
    result = json.loads(content)

    for field in ("name_ar", "name_en", "description_ar", "description_en"):
        if not result.get(field):
            raise EnrichError(f"model returned an empty {field}")

    result["_usage"] = body.get("usage", {})
    return result

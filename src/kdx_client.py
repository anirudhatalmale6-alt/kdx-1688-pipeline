"""
Client for the KDX Laravel endpoint that receives products.

The field mapping lives in one dictionary on purpose: the moment you send me
the endpoint contract (URL, auth header, expected JSON), only FIELD_MAP and
ENDPOINTS change - no logic anywhere else moves.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

# Placeholder mapping: our internal name -> the name your API expects.
FIELD_MAP = {
    "sku": "sku",
    "name_ar": "name_ar",
    "name_en": "name_en",
    "description_ar": "description_ar",
    "description_en": "description_en",
    "price": "price",
    "stock": "stock",
    "weight_kg": "weight",
    "requires_shipping": "requires_shipping",
    "shipping_type": "shipping_type",
    "images": "images",
    "attributes": "attributes",
    "category": "category",
    "keywords": "keywords",
    "source_offer_id": "source_offer_id",
}

ENDPOINTS = {
    "create": "/api/products",
    "update": "/api/products/{sku}",
    "lookup": "/api/products/{sku}",
}


class KdxClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.base_url + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and method == "GET":
                    return {}
                # 4xx other than rate limiting is our bug, not a blip: fail loudly.
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise RuntimeError(f"KDX {exc.code}: {exc.read().decode('utf-8', 'replace')}")
                last_error = exc
            except Exception as exc:
                last_error = exc
            time.sleep(2 ** attempt)

        raise RuntimeError(f"KDX request failed after {self.max_retries} attempts: {last_error}")

    def to_payload(self, product: dict) -> dict:
        return {FIELD_MAP[key]: value for key, value in product.items() if key in FIELD_MAP}

    def exists(self, sku: str) -> bool:
        return bool(self._request("GET", ENDPOINTS["lookup"].format(sku=sku)))

    def create(self, product: dict) -> dict:
        return self._request("POST", ENDPOINTS["create"], self.to_payload(product))

    def update(self, sku: str, product: dict) -> dict:
        """
        Update path only ever carries the mutable fields. SKU, product URL,
        ratings and sales count are deliberately not in this payload, so an
        update can never overwrite them.
        """
        mutable = {"price", "stock", "description_ar", "description_en", "images"}
        payload = self.to_payload({k: v for k, v in product.items() if k in mutable})
        return self._request("PUT", ENDPOINTS["update"].format(sku=sku), payload)

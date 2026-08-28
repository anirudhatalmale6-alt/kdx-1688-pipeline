"""
Client for the KDX Laravel endpoint that receives products.

The contract below is not guessed: it was measured against the live endpoint on
2026-08-28 by sending deliberately wrong types for every candidate field and
reading back which ones the validator complained about. Fields the validator
never mentions are silently discarded by KDX, so sending them is pointless.

Measured contract for POST /api/v1/products/import
    header  X-API-Token: <token>
    body    {"products": [ {...}, {...} ]}

    source_offer_id  REQUIRED   the 1688 offer id; also the update key
    name_en          REQUIRED   string
    name_ar          optional   string
    description_ar   optional   string
    description_en   optional   string
    price            optional   number
    images           optional   array
    category         optional   array

Anything else (sku, weight, stock, shipping, currency, source_url) is accepted
by the HTTP layer but not validated and therefore not stored. Weight and the
shipping flag still need a home on the KDX side - see README.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

IMPORT_PATH = "/api/v1/products/import"

# Our internal name -> the name the KDX validator recognises.
FIELD_MAP = {
    "source_offer_id": "source_offer_id",
    "name_ar": "name_ar",
    "name_en": "name_en",
    "description_ar": "description_ar",
    "description_en": "description_en",
    "price": "price",
    "images": "images",
    "category": "category",
}

REQUIRED = ("source_offer_id", "name_en")

# Sent on an update of a product KDX already has. Deliberately narrow: the SKU,
# the product URL, the ratings and the sales count are not in this set, so an
# update cannot overwrite them.
MUTABLE = ("source_offer_id", "name_en", "price", "images",
           "description_ar", "description_en")


class KdxError(RuntimeError):
    pass


class KdxClient:
    def __init__(self, base_url: str, token: str, timeout: int = 45,
                 max_retries: int = 3, pace_seconds: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        # kdx-sa.com answers bursts with a 403 firewall page, so calls are paced.
        self.pace_seconds = pace_seconds
        self._last_call = 0.0

    def _wait_turn(self) -> None:
        gap = time.time() - self._last_call
        if gap < self.pace_seconds:
            time.sleep(self.pace_seconds - gap)
        self._last_call = time.time()

    def _post(self, path: str, payload: dict) -> dict:
        url = self.base_url + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "X-API-Token": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._wait_turn()
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                # 422 is our payload being wrong and 401 is the token being wrong.
                # Neither improves by trying again, so fail loudly instead.
                if exc.code in (401, 422) or (400 <= exc.code < 500 and exc.code != 429):
                    raise KdxError(f"KDX {exc.code}: {detail}")
                last_error = exc
            except Exception as exc:
                last_error = exc
            time.sleep(2 ** attempt)

        raise KdxError(f"KDX request failed after {self.max_retries} attempts: {last_error}")

    def to_payload(self, product: dict, fields: tuple | None = None) -> dict:
        allowed = fields or tuple(FIELD_MAP)
        payload = {FIELD_MAP[key]: value for key, value in product.items()
                   if key in FIELD_MAP and key in allowed}
        missing = [key for key in REQUIRED if not payload.get(FIELD_MAP[key])]
        if missing:
            raise KdxError(f"product is missing required field(s): {missing}")
        return payload

    def push(self, products: list, batch_size: int = 20) -> list:
        """Send new products. Returns one response dict per batch."""
        responses = []
        for start in range(0, len(products), batch_size):
            batch = [self.to_payload(p) for p in products[start:start + batch_size]]
            responses.append(self._post(IMPORT_PATH, {"products": batch}))
        return responses

    def update(self, products: list, batch_size: int = 20) -> list:
        """
        Same endpoint, narrowed payload. KDX keys on source_offer_id, so an
        existing product is updated rather than duplicated.
        """
        responses = []
        for start in range(0, len(products), batch_size):
            batch = [self.to_payload(p, MUTABLE) for p in products[start:start + batch_size]]
            responses.append(self._post(IMPORT_PATH, {"products": batch}))
        return responses

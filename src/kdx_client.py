"""
Client for the KDX Laravel endpoint that receives products.

The contract below is not guessed: it was measured against the live endpoint on
2026-08-28 by sending deliberately wrong types for every candidate field and
reading back which ones the validator complained about. Fields the validator
never mentions are silently discarded by KDX, so sending them is pointless.

Measured contract for POST /api/v1/products/import
    header  X-API-Token: <token>
    body    {"products": [ {...}, {...} ]}

    source_offer_id         REQUIRED   the 1688 offer id; also the update key
    name_en                 REQUIRED   string
    name_ar                 optional   string
    description_ar          optional   string
    description_en          optional   string
    price                   optional   number
    images                  optional   array
    sizes                   optional   array
    needs_shipment          optional   boolean  <- sets fast vs free delivery
    category.main_category  optional   array
    category.sub_category   optional   array

Anything else (source, product_url, name, name_original, price_currency, weight,
sku, stock) passes the HTTP layer untouched and is then discarded. It is still
sent because the client asked for that shape, but nothing may depend on it.

The product JSON itself is built in src/mapping.py.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

IMPORT_PATH = "/api/v1/products/import"

# HOW LONG HIS IMPORT TAKES, measured 2 September against the live endpoint.
#
# On 1 September one product was lost to "The read operation timed out": 146
# colour options, each with its own photograph. The first guess was payload
# size, and it was wrong - a slice with five photographs in the gallery still
# timed out, because the 146 variant photographs travelled with it untouched.
# What costs his server is the NUMBER OF PHOTOGRAPHS it has to download:
#
#     10 photographs   11.5 s
#     34 photographs   32.2 s
#     34 again         34.3 s     <- the same payload, no faster the second time
#
# roughly one second each, and the third line is the one that decides the
# design: he re-downloads on every update, so there is no cheap "top-up" call.
# His import also DELETES the photograph set before writing the new one -
# `delete from product_images where id = ...` came back in a failure - so
# chunks cannot accumulate either. A second chunk would erase the first.
#
# Both facts together mean one product must arrive in ONE request, and the only
# safe lever left is giving that request enough time.
SECONDS_PER_PHOTO = float(os.environ.get("KDX_SECONDS_PER_PHOTO", "1.5"))
BASE_TIMEOUT = int(os.environ.get("KDX_TIMEOUT", "45"))
MAX_TIMEOUT = int(os.environ.get("KDX_MAX_TIMEOUT", "600"))

# Photographs per request when several products travel together. A batch is
# split on this, which is the part of "send it in smaller batches" that can
# honestly be done from this side.
PHOTOS_PER_REQUEST = int(os.environ.get("KDX_PHOTOS_PER_REQUEST", "40"))


def photo_count(payload: dict) -> int:
    """Distinct photographs his server would have to download for this product."""
    urls = set(url for url in (payload.get("images") or []) if url)
    for variant in payload.get("variants") or []:
        urls.update(url for url in (variant.get("images") or []) if url)
        if variant.get("image"):
            urls.add(variant["image"])
    return len(urls)

REQUIRED = ("source_offer_id", "name_en")

# Fields KDX validates. Anything outside this set is accepted by the HTTP layer
# and then discarded, so it may be sent but never relied on.
VALIDATED = ("source_offer_id", "name_en", "name_ar", "description_ar",
             "description_en", "price", "images", "sizes", "needs_shipment",
             "category")

# Sent when KDX already has the product. Deliberately narrow: SKU, ratings and
# sales count are not in this set, so an update cannot overwrite them.
MUTABLE = ("source_offer_id", "name_en", "name_ar", "price", "images",
           "sizes", "needs_shipment", "description_ar", "description_en")


class KdxError(RuntimeError):
    pass


class KdxClient:
    def __init__(self, base_url: str, token: str, timeout: int = BASE_TIMEOUT,
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

    def timeout_for(self, products: list) -> int:
        """
        How long to wait for a request carrying these products.

        A fixed timeout cannot fit both a one-photograph product and a
        146-photograph one, and the second is not rare in the pool. So the wait
        is bought at the measured rate, with a ceiling so a runaway payload
        cannot hold the night open indefinitely.
        """
        photos = sum(photo_count(product) for product in products)
        return int(min(MAX_TIMEOUT, max(self.timeout,
                                        self.timeout + photos * SECONDS_PER_PHOTO)))

    def _post(self, path: str, payload: dict, timeout: int | None = None) -> dict:
        url = self.base_url + path
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "X-API-Token": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        wait = timeout or self.timeout

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._wait_turn()
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=wait) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                # 422 is our payload being wrong and 401 is the token being wrong.
                # Neither improves by trying again, so fail loudly instead.
                if exc.code in (401, 422) or (400 <= exc.code < 500 and exc.code != 429):
                    raise KdxError(f"KDX {exc.code}: {detail}")
                last_error = exc
            except TimeoutError as exc:
                # Never ask again after a timeout. His server downloads every
                # photograph on every call, so a retry sets the same work going
                # a second time while the first is still running - on
                # 1 September that turned one slow product into 142 seconds of
                # waiting and three copies of the same download queue. The
                # honest report is that it did not answer in time.
                raise KdxError(
                    f"KDX did not answer within {wait}s. That is about "
                    f"{wait / max(SECONDS_PER_PHOTO, 0.1):.0f} photographs' worth of "
                    f"download at his server's measured rate: {exc}") from exc
            except Exception as exc:
                # urllib wraps a connect timeout inside URLError, so the same
                # rule has to be applied to the reason as well as the exception.
                reason = getattr(exc, "reason", None)
                if isinstance(reason, (socket.timeout, TimeoutError)):
                    raise KdxError(f"KDX did not answer within {wait}s: {reason}") from exc
                last_error = exc
            time.sleep(2 ** attempt)

        raise KdxError(f"KDX request failed after {self.max_retries} attempts: {last_error}")

    def to_payload(self, product: dict, fields: tuple | None = None) -> dict:
        """
        Products arrive already in the KDX schema (see src/mapping.py). On the
        update path `fields` narrows them; on create everything is sent.
        """
        payload = dict(product) if fields is None else {
            key: value for key, value in product.items() if key in fields
        }
        missing = [key for key in REQUIRED if not payload.get(key)]
        if missing:
            raise KdxError(f"product is missing required field(s): {missing}")
        return payload

    def batches(self, payloads: list, batch_size: int,
                photos_per_request: int = PHOTOS_PER_REQUEST) -> list:
        """
        Group products into requests his server can finish.

        Two limits, not one. The count keeps a request from carrying too many
        products; the photograph budget keeps it from carrying too much WORK,
        which is the limit that actually bit. A single product over the whole
        budget still travels alone rather than being dropped or cut in half -
        splitting one product across requests is impossible, because his import
        deletes the photograph set before writing the new one, so the second
        half would erase the first.
        """
        grouped, current, cost = [], [], 0
        for payload in payloads:
            weight = photo_count(payload)
            if current and (len(current) >= batch_size
                            or cost + weight > photos_per_request):
                grouped.append(current)
                current, cost = [], 0
            current.append(payload)
            cost += weight
        if current:
            grouped.append(current)
        return grouped

    def push(self, products: list, batch_size: int = 20) -> list:
        """Send new products. Returns one response dict per request."""
        payloads = [self.to_payload(product) for product in products]
        return [self._post(IMPORT_PATH, {"products": batch},
                           timeout=self.timeout_for(batch))
                for batch in self.batches(payloads, batch_size)]

    def update(self, products: list, batch_size: int = 20) -> list:
        """
        Same endpoint, narrowed payload. KDX keys on source_offer_id, so an
        existing product is updated rather than duplicated.
        """
        payloads = [self.to_payload(product, MUTABLE) for product in products]
        return [self._post(IMPORT_PATH, {"products": batch},
                           timeout=self.timeout_for(batch))
                for batch in self.batches(payloads, batch_size)]

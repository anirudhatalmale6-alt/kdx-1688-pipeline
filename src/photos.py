"""
Check the photograph before the product goes out.

A listing with an empty picture frame is worse than no listing: the client's
shop shows it, a customer clicks it, and there is nothing to look at. His
importer downloads each URL we send and stores its own copy, so a URL that
does not answer costs the product its photograph permanently - his import
endpoint inserts and never updates, which means there is no second chance to
put the picture back.

So the URL is fetched here first, before the push, and a product whose photos
are all dead is held rather than published half-made.

Two things this deliberately does NOT do:

  - it does not decide the picture is good. A 200 with image bytes is all that
    can be checked from here; whether the photograph shows the product is the
    supplier's business.
  - it does not send a Referer. His shop's pages carry
    `strict-origin-when-cross-origin`, so a browser asking alicdn directly for
    the image sends `https://kdx-sa.com/` and alicdn answers 403 - measured on
    2026-08-30. That is exactly why the images must be copied onto his server
    rather than hot-linked, and his importer already does the copying. The
    check here therefore mimics his server, which sends no Referer, not a
    browser.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

# His importer is a server, not a browser: no Referer, plain user agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KDX-import/1.0)"}

TIMEOUT = int(os.environ.get("KDX_IMAGE_TIMEOUT", "20"))

# The check can be turned off, because a network that cannot reach alicdn at
# all would otherwise hold every product in the catalogue and look like a bug
# in the rules. Off means "publish what we have and trust the URL".
ENABLED = os.environ.get("KDX_CHECK_IMAGES", "1").strip().lower() not in (
    "0", "false", "no", "off")


class PhotoChecker:
    """Answers 'can this URL be fetched' once per URL per run."""

    def __init__(self, opener=None, timeout: int = TIMEOUT, attempts: int = 2):
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.attempts = attempts
        self.seen: dict = {}
        self.checked = 0
        self.dead = 0

    def reachable(self, url: str) -> bool:
        if not url:
            return False
        if url in self.seen:
            return self.seen[url]

        ok = False
        for attempt in range(self.attempts):
            request = urllib.request.Request(url, headers=HEADERS, method="GET")
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    kind = (response.headers.get("Content-Type") or "").lower()
                    # A 200 that hands back an HTML error page is not a photo.
                    ok = 200 <= getattr(response, "status", 200) < 300 \
                        and kind.startswith("image/")
                break
            except urllib.error.HTTPError:
                # 403 and 404 do not improve by asking again.
                break
            except Exception:  # noqa: BLE001
                if attempt + 1 >= self.attempts:
                    break

        self.seen[url] = ok
        self.checked += 1
        if not ok:
            self.dead += 1
        return ok

    def keep(self, urls) -> list:
        return [url for url in (urls or []) if self.reachable(url)]

    def summary(self) -> dict:
        return {"urls_checked": len(self.seen), "urls_dead": self.dead}


def prune(payload: dict, checker: PhotoChecker) -> dict:
    """
    Drop unfetchable photographs from a built KDX product, in place.

    Every place a photograph appears has to be pruned, not just the gallery:
    the variant blocks carry their own `image` and `images`, and a variant left
    pointing at a dead URL would render an empty frame on the colour swatch
    even though the product card looked fine.

    Returns a small report: how many photographs the product had and how many
    survived. An empty `kept` is the caller's signal to hold the product.
    """
    before = list(payload.get("images") or [])
    kept = checker.keep(before)
    payload["images"] = kept

    for variant in payload.get("variants") or []:
        variant["images"] = checker.keep(variant.get("images"))
        if variant.get("image") and not checker.reachable(variant["image"]):
            variant["image"] = variant["images"][0] if variant["images"] else ""

    return {"had": len(before), "kept": len(kept),
            "dropped": [url for url in before if url not in kept]}

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

import hashlib
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# His importer is a server, not a browser: no Referer, plain user agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KDX-import/1.0)"}

TIMEOUT = int(os.environ.get("KDX_IMAGE_TIMEOUT", "20"))

# The check can be turned off, because a network that cannot reach alicdn at
# all would otherwise hold every product in the catalogue and look like a bug
# in the rules. Off means "publish what we have and trust the URL".
ENABLED = os.environ.get("KDX_CHECK_IMAGES", "1").strip().lower() not in (
    "0", "false", "no", "off")


# The check already downloads the whole photograph, so the bytes are kept for
# whoever else wants to look at them - today that is the Chinese-text scorer in
# src/imagetext.py. Bounded, because a night handles hundreds of products and
# nothing here should be able to grow without a limit: once the budget is used
# the bytes are simply not kept and the next reader fetches its own copy.
KEEP_BYTES = int(os.environ.get("KDX_IMAGE_CACHE_BYTES", str(48 * 1024 * 1024)))

# How many photographs to fetch at once. A pool product carries 100+ of them and
# the run on 1 September spent 19.5 minutes on eight products almost entirely
# here, one URL at a time, each one a round trip to China. Eight is deliberately
# modest: this is someone else's CDN and the point is to stop wasting our own
# waiting, not to hammer theirs.
WORKERS = int(os.environ.get("KDX_IMAGE_WORKERS", "8"))


class PhotoChecker:
    """Answers 'can this URL be fetched' once per URL per run."""

    def __init__(self, opener=None, timeout: int = TIMEOUT, attempts: int = 2,
                 keep_bytes: int = KEEP_BYTES, workers: int = WORKERS):
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.attempts = attempts
        self.seen: dict = {}
        self.bodies: dict = {}
        self.keep_bytes = keep_bytes
        self.held_bytes = 0
        self.checked = 0
        self.dead = 0
        self.workers = max(1, workers)
        # The cache and the byte budget are touched from several threads once
        # warm() runs. Without this two threads can both see room for the last
        # megabyte and the budget quietly becomes a suggestion.
        self._lock = threading.Lock()

    def warm(self, urls) -> None:
        """
        Fetch these URLs concurrently so the checks after it are cache hits.

        Purely an optimisation: every answer still goes through reachable(), so
        the result of a run is identical whether this is called or not - which
        is what makes it safe to skip when workers is 1.
        """
        pending = []
        with self._lock:
            for url in urls or []:
                if url and url not in self.seen and url not in pending:
                    pending.append(url)
        if not pending or self.workers == 1:
            for url in pending:
                self.reachable(url)
            return
        with ThreadPoolExecutor(max_workers=min(self.workers, len(pending))) as pool:
            list(pool.map(self.reachable, pending))

    def reachable(self, url: str) -> bool:
        if not url:
            return False
        with self._lock:
            if url in self.seen:
                return self.seen[url]

        ok = False
        for attempt in range(self.attempts):
            request = urllib.request.Request(url, headers=HEADERS, method="GET")
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    kind = (response.headers.get("Content-Type") or "").lower()
                    body = response.read()
                    # A 200 that hands back an HTML error page is not a photo.
                    ok = 200 <= getattr(response, "status", 200) < 300 \
                        and kind.startswith("image/")
                    if ok and body:
                        with self._lock:
                            if self.held_bytes + len(body) <= self.keep_bytes:
                                self.bodies[url] = body
                                self.held_bytes += len(body)
                break
            except urllib.error.HTTPError:
                # 403 and 404 do not improve by asking again.
                break
            except Exception:  # noqa: BLE001
                if attempt + 1 >= self.attempts:
                    break

        with self._lock:
            self.seen[url] = ok
            self.checked += 1
            if not ok:
                self.dead += 1
        return ok

    def body(self, url: str) -> bytes:
        """The bytes already downloaded for this URL, or b'' if they were not kept."""
        return self.bodies.get(url, b"")

    def fingerprint(self, url: str):
        """
        What this photograph *is*, rather than where it lives, or None.

        1688 serves the same photograph under more than one URL, so a gallery
        deduplicated by URL still shows the shopper the same picture twice - the
        client reported it on 2 September. Measured on 12 published products
        that day: 3 of 91 photographs were byte-identical to another photograph
        in the same product under a different URL.

        None where the bytes were not kept - the checker holds only as many as
        its budget allows - and None is not a match, because not having looked
        is not evidence of a duplicate.
        """
        body = self.bodies.get(url)
        return hashlib.sha256(body).hexdigest() if body else None

    def deduplicate(self, urls) -> list:
        """The same list with any repeat of an earlier photograph removed."""
        kept: list = []
        seen: set = set()
        for url in urls or []:
            mark = self.fingerprint(url)
            if mark is not None and mark in seen:
                continue
            if mark is not None:
                seen.add(mark)
            kept.append(url)
        return kept

    def keep(self, urls) -> list:
        return [url for url in (urls or []) if self.reachable(url)]

    def summary(self) -> dict:
        return {"urls_checked": len(self.seen), "urls_dead": self.dead}


# The CDN will resize a picture for us if the URL asks. Measured on 25 first
# photographs of the 2 September catalogue: every one answered, none was
# enlarged (a 688x688 came back 688x688, a 1433x1920 came back 597x800 - the
# shape is kept), and the 25 together fell from 7.4 MB to 3.6 MB.
#
# 0 turns it off and the original URLs ship, which is what happened before this
# existed.
DISPLAY_PX = int(os.environ.get("KDX_IMAGE_DISPLAY_PX", "800"))


def display_url(url: str) -> str:
    """The same photograph, asked for at display size."""
    if not url or DISPLAY_PX <= 0 or ".alicdn.com/" not in url:
        return url
    if f"_{DISPLAY_PX}x{DISPLAY_PX}" in url:
        return url
    return f"{url}_{DISPLAY_PX}x{DISPLAY_PX}.jpg"


def _resize(urls, checker) -> list:
    """
    Swap in the display-size URL, but only where it actually answers.

    A URL nobody fetched is a URL nobody can vouch for, and his importer copies
    the picture once with no second chance, so the smaller one has to prove
    itself the same way the original did. Where it does not, the original ships
    and the product keeps its photograph.
    """
    return [display_url(url) if checker.reachable(display_url(url)) else url
            for url in urls or []]


def resize_for_display(payload: dict, checker: PhotoChecker) -> int:
    """
    Ask the CDN for display-size copies of everything this product publishes.

    Deliberately the LAST step, after the poster filter has run. Measured on 2
    September on twelve photographs that scored above the 5% line: read at
    800x800 instead of full size, four of the twelve fell below it - 7.86% to
    0.31% in the worst case. Scoring the small copy would have published four
    posters out of twelve. So the decisions are all taken on the full-size
    picture and only the URL that ships is changed.

    A first sample of thirty ordinary photographs showed no verdict changing at
    all; it contained no posters, so it could not have. The posters had to be
    looked for on purpose.
    """
    if DISPLAY_PX <= 0:
        return 0
    wanted = [display_url(url) for url in payload.get("images") or []]
    for variant in payload.get("variants") or []:
        wanted.extend(display_url(url) for url in variant.get("images") or [])
    checker.warm(wanted)

    before = list(payload.get("images") or [])
    payload["images"] = _resize(before, checker)
    changed = sum(1 for old, new in zip(before, payload["images"]) if old != new)

    for variant in payload.get("variants") or []:
        old_images = list(variant.get("images") or [])
        variant["images"] = _resize(old_images, checker)
        changed += sum(1 for old, new in zip(old_images, variant["images"])
                       if old != new)
        # The swatch has to follow its own gallery, not be resized on its own:
        # if the small copy of this colour's photograph was refused above, the
        # swatch must point at the original that was kept.
        if variant.get("image") in old_images:
            variant["image"] = variant["images"][old_images.index(variant["image"])]
    return changed


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

    # Every URL this product will ask about, fetched together. A pool product
    # carries the gallery plus one photograph per colour, and asking for them
    # one at a time is what made 1 September's run take 19.5 minutes for eight
    # products. The pruning below is unchanged and still authoritative; this
    # only means it finds the answers already in hand.
    everything = list(before)
    for variant in payload.get("variants") or []:
        everything.extend(variant.get("images") or [])
        if variant.get("image"):
            everything.append(variant["image"])
    checker.warm(everything)

    # Reachable first, then one copy of each distinct photograph. The order is
    # not interchangeable: fingerprints only exist for URLs that answered.
    kept = checker.deduplicate(checker.keep(before))
    payload["images"] = kept

    for variant in payload.get("variants") or []:
        # Within one colour only. Two colours sharing a photograph is the
        # supplier reusing a shot, and dropping the second one would leave that
        # colour with a blank swatch.
        variant["images"] = checker.deduplicate(checker.keep(variant.get("images")))
        if variant.get("image") not in variant["images"]:
            variant["image"] = variant["images"][0] if variant["images"] else ""

    return {"had": len(before), "kept": len(kept),
            "dropped": [url for url in before if url not in kept]}

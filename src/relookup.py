"""
Finding one offer again, when the channel has no lookup.

The permission we hold searches by photograph and cannot be asked for offer 123
by id, which is why the nightly run is built around discovery. But a price
refresh needs exactly that: today's 1688 price for a product published weeks
ago. Without it, the upsert route his developer added on 2026-08-30 has nothing
to send.

The way through is that every offer carries its own photograph, and searching a
photograph returns the offer it came from. Measured on 2026-08-30 against the
live gateway, on the first six products of the 30 August run:

    offer                 found at
    1000354769581         page 1, rank 10
    1004582496795         page 1, rank 6
    1004882577861         page 1, rank 4
    1008787630854         page 1, rank 11
    1008796506947         page 2          <- not on page 1
    1011749763823         page 1, rank 1

Six of six within two pages, five of six on the first. So this is a lookup, at
the cost of one or two gateway searches per product - and it is not free, which
is why the caller passes a page ceiling and why a miss is reported rather than
guessed at.

What must NOT happen is a miss quietly becoming a price. An offer that cannot be
found again is left exactly as it is in the shop, and counted, so a refresh that
stops working is visible instead of silently freezing every price.
"""

from __future__ import annotations

import os

MAX_PAGES = int(os.environ.get("KDX_RELOOKUP_PAGES", "2"))


class NotFound(LookupError):
    """The offer did not come back from its own photograph."""


def find(source, offer_id: str, image_url: str, max_pages: int = 0) -> dict:
    """
    Return the normalised product for `offer_id`, found through `image_url`.

    Raises NotFound rather than returning something approximate. The near
    matches on the page are other sellers' listings of a similar thing; using
    one of those would put another shop's price on his product and nothing in
    the report would say so.
    """
    if not image_url:
        raise NotFound(f"offer {offer_id} has no photograph to search with")
    offer_id = str(offer_id)
    pages = max_pages or MAX_PAGES
    searched = 0
    for page in range(1, pages + 1):
        rows = source.search_by_image(image_url, page=page)
        searched += 1
        for row in rows:
            if str(row.get("offer_id")) == offer_id:
                row["_relookup_page"] = page
                row["_relookup_searches"] = searched
                return row
        if not rows:
            break                     # past the last page; more will not help
    raise NotFound(f"offer {offer_id} was not among the first {pages} page(s) "
                   f"of its own photograph")


def refresh_targets(directories: list, limit: int = 0) -> list:
    """
    [{offer_id, image, price, day}] for everything already published, newest
    payload per offer.

    The payloads written by daily_run are the record of what his shop was
    actually sent, which is why they are the source here rather than the
    discovery ledger: the ledger knows an offer was seen, not what price went
    out with it.
    """
    import json

    latest: dict = {}
    for directory in directories:
        if not os.path.isdir(directory):
            continue
        for day in sorted(os.listdir(directory)):
            day_dir = os.path.join(directory, day)
            if not os.path.isdir(day_dir):
                continue
            for name in sorted(os.listdir(day_dir)):
                if not name.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(day_dir, name), encoding="utf-8") as handle:
                        payload = json.load(handle)
                except (OSError, ValueError):
                    continue
                offer_id = str(payload.get("source_offer_id") or "")
                images = payload.get("images") or []
                if not offer_id or not images:
                    continue
                # Later days overwrite earlier ones: the newest payload is the
                # one that describes what the shop currently holds.
                latest[offer_id] = {"offer_id": offer_id, "image": images[0],
                                    "price": payload.get("price"), "day": day}
    targets = [latest[key] for key in sorted(latest)]
    return targets[:limit] if limit else targets

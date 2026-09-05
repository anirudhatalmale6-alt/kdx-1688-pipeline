#!/usr/bin/env python3
"""
Put back what the shop already had, from disk, without touching 1688.

5 September 2026. He deleted every product from kdx-sa.com to start clean, and
the nightly pull publishes ten products every twenty minutes - the right rate
for discovery, and far too slow to refill a shop that is now empty.

It does not have to be re-pulled. Every product ever sent was written to
out/<day>/<offer>.json before the push, so the file IS the request body that
his import received. Pushing those files back costs no 1688 points, no
searches, no translation and no photo work, and it reproduces exactly the
catalogue he had.

ONLY EVER RUN THIS AGAINST AN EMPTY SHOP.

His import appends options rather than replacing them: on 2 September offer
717716012309 was created with 146 Chinese labels, updated with the same 146 in
Arabic, and the page afterwards showed 291 - both sets, side by side. Nothing
on this side can undo that. So --push is not the default, every offer that
goes out is written to the state file as it goes, and a run that is
interrupted resumes from there instead of starting again. Any reply that says
it UPDATED rather than inserted is printed loudly, because it means that offer
already existed.

  python3 restore_from_disk.py                    what would be sent, nothing sent
  python3 restore_from_disk.py --push             send it
  python3 restore_from_disk.py --push --mirror    send it with the options
                                                  mirrored into sizes[]

--mirror is the compatibility shape from 5 September: a front end that reads
only sizes[] never sees variants[], which is where every option's own price and
photo live, and shows one price for a product that has fifty. It is a copy -
variants[] is left whole - and it is applied only where there is no real size
axis and more than one thing to buy. See MIRROR_OPTIONS_AS_SIZES in mapping.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import kdx_client                                            # noqa: E402
import mapping                                               # noqa: E402
import paths                                                 # noqa: E402
from pipeline import is_acknowledgement, was_update          # noqa: E402

# The list he was given on 4 September: twenty purely industrial products and
# seventeen whose cheapest option is nothing like their dearest. He was asked to
# delete them, and his API has no delete verb, so the deletion was his to do by
# hand. He has now deleted everything - which means the cheapest possible way to
# honour that list is simply not to send them back.
DEFAULT_EXCLUDE = "products-to-review-2026-09-04.txt"

# Reported the same day and for the same reason, outside that file.
ALWAYS_EXCLUDE = ("1080706692044",)

OFFER_IN_URL = re.compile(r"offer/(\d+)")
BARE_ID = re.compile(r"^\s*(\d{6,})\s*$")


def excluded_ids(path: str) -> set:
    """
    Offer ids from a list meant for a person: the review file is Arabic prose
    with a 1688 URL under each product, and a plain list of ids must work too.
    """
    ids = set(ALWAYS_EXCLUDE)
    if not path:
        return ids
    if not os.path.exists(path):
        raise SystemExit(f"exclude list not found: {path}")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            ids.update(OFFER_IN_URL.findall(line))
            bare = BARE_ID.match(line)
            if bare:
                ids.add(bare.group(1))
    return ids


def payload_files(out_dir: str) -> dict:
    """
    One file per offer, newest wins.

    An offer priced again on a later day was written again, and the later file
    is the one his shop last received. Day directories sort as dates, so plain
    ordering is chronological.
    """
    newest: dict = {}
    for day in sorted(os.listdir(out_dir)):
        for path in sorted(glob.glob(os.path.join(out_dir, day, "*.json"))):
            newest[os.path.basename(path)[:-len(".json")]] = path
    return newest


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        product = json.load(handle)
    # A guard, not a formality: pushing a list of products where one is the
    # envelope, or is missing its key, is how a whole batch is rejected.
    if not isinstance(product, dict) or not product.get("source_offer_id"):
        raise ValueError(f"{path}: not a product payload")
    return product


def mirror(product: dict) -> bool:
    """
    Copy the purchase options into sizes[], where his front end looks.

    Narrow on purpose, and identical to the rule inside to_kdx_product: only
    when sizes[] is empty (no real size axis) and there is more than one option
    to choose between. Returns whether anything was changed.
    """
    block = product.get("variants") or []
    if product.get("sizes") or len(block) < 2:
        return False
    product["sizes"] = mapping.options_as_sizes(block)
    return bool(product["sizes"])


def done_already(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def record(path: str, offer_ids: list) -> None:
    """
    Written and flushed as each batch returns, never at the end. A run killed
    half way through must not offer to send those products a second time.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for offer_id in offer_ids:
            handle.write(f"{offer_id}\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true",
                        help="actually send. Without it nothing leaves this machine")
    parser.add_argument("--mirror", action="store_true",
                        help="copy each option's price and photo into sizes[]")
    parser.add_argument("--limit", type=int, default=0, help="stop after N products")
    parser.add_argument("--batch-size", type=int, default=5,
                        help="products per request (his import is also split on "
                             "photo count, see kdx_client.batches)")
    parser.add_argument("--out-dir", default="",
                        help="where the payloads are (default: the run's out/)")
    parser.add_argument("--exclude", default="",
                        help=f"list of offers not to send (default: {DEFAULT_EXCLUDE})")
    parser.add_argument("--no-exclude", action="store_true",
                        help="send everything, including the review list")
    parser.add_argument("--state", default="",
                        help="file of offers already restored, for resuming")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = args.out_dir or paths.state_path("out", "KDX_OUT_DIR")
    if not os.path.isdir(out_dir):
        raise SystemExit(f"no payloads to restore from: {out_dir}")

    state = args.state or paths.state_path("restored.txt", "KDX_RESTORE_STATE")

    if args.no_exclude:
        skip = set()
    else:
        listed = args.exclude or os.path.join(here, DEFAULT_EXCLUDE)
        skip = excluded_ids(listed if os.path.exists(listed) else "")

    files = payload_files(out_dir)
    already = done_already(state)

    chosen = [(offer_id, path) for offer_id, path in sorted(files.items())
              if offer_id not in skip and offer_id not in already]
    if args.limit:
        chosen = chosen[:args.limit]

    print(f"payloads on disk      {len(files)}")
    print(f"on the review list    {len(files.keys() & skip)}  (not sent)")
    print(f"already restored      {len(files.keys() & already)}")
    print(f"to send now           {len(chosen)}")

    products = []
    mirrored = 0
    for offer_id, path in chosen:
        product = load(path)
        if args.mirror and mirror(product):
            mirrored += 1
        products.append(product)

    photos = sum(kdx_client.photo_count(product) for product in products)
    print(f"photographs           {photos}")
    if args.mirror:
        print(f"options mirrored into sizes[]  {mirrored} products")

    if not args.push:
        print("\nDRY RUN - nothing was sent. Add --push.")
        return 0
    if not products:
        print("\nnothing left to send.")
        return 0

    client = kdx_client.KdxClient(
        os.environ.get("KDX_BASE_URL", "https://kdx-sa.com"),
        os.environ["KDX_API_TOKEN"])

    sent = acknowledged = updates = failures = 0
    for start in range(0, len(products), args.batch_size):
        chunk = products[start:start + args.batch_size]
        ids = [product["source_offer_id"] for product in chunk]
        try:
            responses = client.push(chunk, batch_size=args.batch_size)
        except Exception as error:                          # noqa: BLE001
            # One bad batch must not cost the other seven hundred. It is
            # reported by offer id and the run carries on; nothing is recorded
            # as sent, because we do not know that it was.
            failures += len(chunk)
            print(f"  FAILED  {', '.join(ids)}  {error}")
            continue
        # Recorded before anything is printed or counted. If his server accepted
        # it, this side must never offer to send it again.
        record(state, ids)
        sent += len(chunk)
        for response in responses:
            if is_acknowledgement(response):
                acknowledged += 1
            if was_update(response):
                updates += 1
                print(f"  UPDATED rather than inserted: {', '.join(ids)} - his "
                      f"import appends options, check these for duplicates")
        print(f"  {sent}/{len(products)} sent")

    print(f"\nsent        {sent} products")
    print(f"accepted    {acknowledged} batches answered 'received, processing' "
          f"(accepted, not confirmed)")
    print(f"updates     {updates}")
    print(f"failed      {failures}")
    print(f"state       {state}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""
Run the pipeline over a list of offers.

    # the whole system on recorded data, no keys, nothing published
    KDX_SOURCE=fixture KDX_COMPARE=fixture python3 run_pipeline.py --dry-run

    # the real thing, once alibaba.product.get is permitted
    python3 run_pipeline.py 104843239419 611229900011

--dry-run stops before KDX: everything is read, filtered, priced and assembled,
and the products are written to out/ instead of being published.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import pipeline as pipeline_module  # noqa: E402
import rules  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull, price and publish 1688 offers")
    parser.add_argument("offers", nargs="*", help="offer ids; omit to use every recorded offer")
    parser.add_argument("--dry-run", action="store_true", help="assemble but do not publish")
    parser.add_argument("--rate", help="CNY to SAR, instead of today's fetched rate")
    parser.add_argument("--out", default=os.path.join(HERE, "out"))
    args = parser.parse_args()

    runner = pipeline_module.build(
        dry_run=args.dry_run,
        cny_to_sar=Decimal(args.rate) if args.rate else None,
    )

    offers = args.offers
    if not offers:
        if not hasattr(runner.source, "offer_ids"):
            parser.error("no offer ids given, and this source cannot list them")
        offers = runner.source.offer_ids()
    if not offers:
        print("nothing to do: no offers given and none recorded")
        return 1

    os.makedirs(args.out, exist_ok=True)
    published = held = 0

    for outcome in runner.run(offers):
        if outcome.error:
            print(f"{outcome.offer_id}  SKIPPED  {outcome.error}")
            continue

        for result in outcome.results:
            if result.decision != rules.Decision.PUBLISH:
                held += 1
                print(f"{outcome.offer_id}  held     {result.variant.sku_id}"
                      f"  {result.audit.reason_ar}")

        if outcome.product is None:
            continue

        published += outcome.published
        path = os.path.join(args.out, f"{outcome.offer_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(outcome.product, handle, ensure_ascii=False, indent=2, default=str)

        note = "" if outcome.compared else "  (no comparison: translation was skipped)"
        print(f"{outcome.offer_id}  {'assembled' if args.dry_run else 'published'}"
              f"  {outcome.published} variants"
              f"  {len(outcome.product['variants'])} photos"
              f"  from {outcome.product['price']} SAR{note}")

    print(f"\n{published} variants {'assembled' if args.dry_run else 'published'}, "
          f"{held} held back")
    if runner.budget is not None:
        summary = runner.budget.summary()
        print(f"points: {summary['spent']}/{summary['daily']} used on {summary['day']}")
    if args.dry_run:
        print(f"products written to {os.path.relpath(args.out, HERE)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

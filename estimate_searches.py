"""
What a month of SerpApi costs, at the settings actually in force.

    python3 estimate_searches.py                 # 300 products a day
    python3 estimate_searches.py --per-day 500
    KDX_LENS_SCOPE=variant python3 estimate_searches.py --colours 4

The number is computed from the code's own settings and from the recorded live
responses, not typed in, so changing a setting changes the estimate. Where a
rate had to be measured it is measured here, in front of you, and the sample
size is printed with it - two recorded products is a thin sample and the output
says so rather than hiding it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import compare  # noqa: E402

TITLES = {
    "boiler": "Commercial Stainless Steel Electric Water Boiler 30L 3000W",
    "uniqlo": "Cotton Short Sleeve T-Shirt Loose Summer Top",
}

# SerpApi's published plans, read from serpapi.com/pricing on 29 August 2026.
PLANS = [("Free", 0, 250), ("Starter", 25, 1_000), ("Developer", 75, 5_000),
         ("Production", 150, 15_000), ("Big Data", 275, 30_000),
         ("Searcher", 725, 100_000)]


def measured_second_search_rate() -> tuple[int, int, list]:
    """
    On the recorded live responses, how often is the second (price) search
    needed at all? Returns (needed, total, detail).
    """
    directory = os.path.join(HERE, "samples", "lens")
    needed = total = 0
    detail = []
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            rows = (json.load(handle).get("visual_matches") or [])
        key = "uniqlo" if "uniqlo" in name else "boiler"
        matches = compare.identity_matches(rows, TITLES[key])
        second = compare.needs_price_search(matches)
        total += 1
        needed += 1 if second else 0
        detail.append({"sample": key, "visual_matches": len(rows),
                       "identified_on_our_platforms": len(matches),
                       "already_priced": sum(1 for m in matches if m["price"] is not None),
                       "needs_second_search": second})
    return needed, total, detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-day", type=int, default=300,
                        help="products processed a day (the 1688 point quota caps this)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--colours", type=int, default=3,
                        help="average colours per product, only used in variant scope")
    args = parser.parse_args()

    scope = compare.LENS_SCOPE
    image_searches = 1 if scope == "product" else max(1, args.colours)
    needed, total, detail = measured_second_search_rate()

    monthly_floor = args.per_day * args.days * image_searches
    monthly_ceiling = monthly_floor + args.per_day * args.days  # every product needing a price
    measured_rate = needed / total if total else 0
    monthly_measured = int(monthly_floor + args.per_day * args.days * measured_rate)

    print(f"settings:  scope={scope}  price-search={compare.SHOPPING_WHEN}  "
          f"picture>={compare.MATCH_THRESHOLD}  words>={compare.TEXT_THRESHOLD}")
    print(f"           cache TTL: a compared product is not compared again for "
          f"{int(os.environ.get('KDX_COMPARE_TTL_DAYS', '7'))} days\n")

    print("measured on the recorded live responses:")
    for row in detail:
        print(f"  {row['sample']:8} {row['visual_matches']:3} visual matches, "
              f"{row['identified_on_our_platforms']} on our five platforms, "
              f"{row['already_priced']} already priced -> "
              f"{'second search' if row['needs_second_search'] else 'no second search'}")
    print(f"  second search needed in {needed} of {total} samples "
          f"- a thin sample, treat the ceiling as the number to buy against\n")

    print(f"{args.per_day} products a day, {args.days} days:")
    print(f"  floor    {monthly_floor:>7,}  searches a month  (no product needs a price lookup)")
    print(f"  measured {monthly_measured:>7,}  at the rate measured above")
    print(f"  ceiling  {monthly_ceiling:>7,}  (every product needs one)\n")

    print("against SerpApi's plans:")
    for name, price, allowance in PLANS:
        verdict = ("enough, with room" if allowance >= monthly_ceiling * 1.25 else
                   "enough" if allowance >= monthly_ceiling else
                   "enough only if the measured rate holds" if allowance >= monthly_measured else
                   "not enough")
        print(f"  {name:11} ${price:<5} {allowance:>7,} /month   {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

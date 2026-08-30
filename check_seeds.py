"""
Are these seed photographs any good, before a whole night is spent on them?

    python3 check_seeds.py seeds.txt
    python3 check_seeds.py https://cbu01.alicdn.com/img/ibank/....jpg

One gateway call per seed - the same call the night's first page makes - and it
answers the three questions that decide whether a seed list is worth keeping:

  1. is it alive?      a photograph Alibaba cannot fetch from China fails loudly
                       (SYSTEM_ERROR "handle image error with url ..."), and a
                       seed that fails at 00:05 costs the whole night.
  2. what does it open? the categories of what comes back, in Arabic. A seed is
                       a door into one neighbourhood of the market, not a filter.
  3. is it redundant?  two seeds that return the same offers are one seed. The
                       overlap is printed as offers shared, not as a guess.

Nothing is published and nothing is written to the ledger, so this can be run as
often as the seed list changes.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import aop_client  # noqa: E402
import catalog  # noqa: E402
import discover  # noqa: E402
import source as source_module  # noqa: E402


def load_catalogue() -> catalog.CategoryIndex:
    return catalog.CategoryIndex.load(
        os.environ.get("KDX_CATEGORIES", os.path.join(HERE, "data", "categories.json")))


def read_arguments(argv: list) -> list:
    """A file of URLs, or the URLs themselves, or KDX_SEEDS."""
    if not argv:
        return discover.read_seeds()
    if len(argv) == 1 and os.path.exists(argv[0]):
        return discover.read_seeds(argv[0])
    return [value.strip() for value in argv if value.strip()]


def inspect(seed: str, source, categories: catalog.CategoryIndex) -> dict:
    try:
        found = source.search_by_image(seed, page=1)
    except Exception as exc:                                  # noqa: BLE001
        return {"seed": seed, "alive": False, "error": str(exc)}

    report = {"seed": seed, "alive": True, "offers": [], "categories": {},
              "blocked": 0, "unknown": 0}
    for product in found:
        report["offers"].append(product["offer_id"])
        state = categories.state_of(product.get("category_id"))
        if state in (catalog.BLOCKED, catalog.REVIEW):
            report["blocked"] += 1
            continue
        if state == "unknown":
            report["unknown"] += 1
        main, sub = categories.resolve(product.get("category_id"))
        name = (sub or main or {}).get("name_ar") if (main or sub) else None
        name = name or f"category {product.get('category_id') or '?'}"
        report["categories"][name] = report["categories"].get(name, 0) + 1
    return report


def main(argv: list) -> int:
    seeds = read_arguments(argv)
    if not seeds:
        print("no seeds: pass a file, some URLs, or set KDX_SEEDS")
        return 2

    client = aop_client.build_from_env()
    source = source_module.LinkPlusSource(client)
    categories = load_catalogue()

    print(f"{len(seeds)} seed photograph(s), one gateway call each\n")
    reports = []
    for index, seed in enumerate(seeds, start=1):
        report = inspect(seed, source, categories)
        reports.append(report)
        head = f"{index}. {seed[:78]}"
        if not report["alive"]:
            print(f"{head}\n   DEAD - {report['error'][:160]}\n")
            continue
        mix = ", ".join(f"{name} ({count})" for name, count
                        in sorted(report["categories"].items(),
                                  key=lambda item: -item[1])[:4])
        print(f"{head}\n   {len(report['offers'])} offers on page 1"
              f" | {mix or 'no usable rows'}")
        if report["blocked"]:
            print(f"   {report['blocked']} would be dropped on category before pricing")
        if report["unknown"]:
            print(f"   {report['unknown']} in categories the tree has not walked yet")
        print()

    alive = [report for report in reports if report["alive"] and report["offers"]]
    if len(alive) > 1:
        print("overlap - two seeds that return the same offers are one seed:")
        for first in range(len(alive)):
            for second in range(first + 1, len(alive)):
                shared = set(alive[first]["offers"]) & set(alive[second]["offers"])
                if shared:
                    print(f"  seeds {first + 1} and {second + 1} share "
                          f"{len(shared)} of 20 offers")
        print()

    dead = [report for report in reports if not report["alive"]]
    empty = [report for report in reports if report["alive"] and not report["offers"]]
    print(f"{len(alive)} usable, {len(empty)} returned nothing, {len(dead)} dead")
    if dead or empty:
        print("replace those before the night runs - a seed that fails at 00:05 "
              "is a night with fewer products, and nobody is awake to notice")
    return 1 if dead else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

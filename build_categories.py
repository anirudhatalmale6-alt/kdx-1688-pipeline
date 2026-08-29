"""
Build the 1688 category tree, translated to Arabic and English and filtered.

    KDX_1688_APP_KEY=... KDX_1688_APP_SECRET=... python3 build_categories.py

    # what it would do, without touching 1688 or the translator
    python3 build_categories.py --offline

Options that exist for a reason, not for symmetry:

  --max-calls   hard cap on API calls. The tree shares the client's 300 points a
                day with the product pull, so a walk is never allowed to eat the
                whole allowance by accident. Cached nodes do not count.
  --depth       how far down to walk. Depth 2 is enough to decide what is banned;
                the leaves are only needed once products are actually flowing.
  --no-translate  skip the model entirely. The tree still builds and still
                filters, in Chinese.

Both caches (nodes and translations) live under data/ and are reused, so running
this a second time costs nothing at all.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import catalog  # noqa: E402

DATA = os.path.join(HERE, "data")
NODE_CACHE = os.path.join(DATA, "categories_raw.json")
NAME_CACHE = os.path.join(DATA, "category_names.json")


def live_fetch():
    from aop_client import AopClient, Credentials
    import categories as categories_module

    key = os.environ.get("KDX_1688_APP_KEY", "")
    secret = os.environ.get("KDX_1688_APP_SECRET", "")
    if not key or not secret:
        raise SystemExit("set KDX_1688_APP_KEY and KDX_1688_APP_SECRET, "
                         "or pass --offline to work from the cache")

    client = AopClient(Credentials(app_key=key, app_secret=secret))
    return lambda category_id: categories_module.fetch_category(client, category_id)


def offline_fetch(category_id):
    raise catalog.BudgetExhausted(
        f"category {category_id} is not in the cache and --offline forbids fetching it")


def write_outputs(rows: list, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {}

    paths["json"] = os.path.join(out_dir, "categories.json")
    with open(paths["json"], "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)

    columns = ["id", "parent_id", "depth", "name_zh", "name_en", "name_ar",
               "path_zh", "is_leaf", "state", "reason", "matched"]

    def dump(name: str, subset: list) -> str:
        path = os.path.join(out_dir, name)
        # utf-8-sig so Arabic and Chinese open correctly in Excel, which is
        # where the client will actually look at this.
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in subset:
                writer.writerow(row)
        return path

    paths["all"] = dump("categories.csv", rows)
    paths["blocked"] = dump("categories_blocked.csv",
                            [r for r in rows if r["state"] == catalog.BLOCKED])
    paths["review"] = dump("categories_review.csv",
                           [r for r in rows if r["state"] == catalog.REVIEW])
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the translated, filtered category tree")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--max-calls", type=int, default=60)
    parser.add_argument("--offline", action="store_true",
                        help="use only the cache; never call 1688")
    parser.add_argument("--no-translate", action="store_true")
    parser.add_argument("--out", default=DATA)
    args = parser.parse_args()

    cache = catalog.NodeCache(NODE_CACHE)
    fetch = offline_fetch if args.offline else live_fetch()

    try:
        rows = catalog.build_tree(fetch, cache, max_depth=args.depth,
                                  max_calls=None if args.offline else args.max_calls)
    finally:
        cache.save()

    print(f"{len(rows)} categories  "
          f"({cache.misses} calls made, {cache.hits} served from cache)")

    # translate_rows always runs, so name_en and name_ar always exist. With no
    # translator it fills them from the cache and falls back to the Chinese,
    # which is what "not translated yet" should look like - not a missing key.
    translator = None
    if args.no_translate:
        reason = "--no-translate"
    elif not os.environ.get("OPENAI_API_KEY"):
        reason = "no OPENAI_API_KEY"
    else:
        import enrich
        translator, reason = enrich.translate_categories, ""

    if translator is None:
        print(f"names left in Chinese ({reason})", file=sys.stderr)
    known = catalog.translate_rows(rows, translator or (lambda chunk: {}), NAME_CACHE)
    print(f"{len(known)} names in the translation cache")

    paths = write_outputs(rows, args.out)
    summary = catalog.summarise(rows)
    print(json.dumps(summary, ensure_ascii=False))

    blocked = [r for r in rows if r["state"] == catalog.BLOCKED]
    flagged = [r for r in rows if r["state"] == catalog.REVIEW]
    for row in blocked:
        print(f"  BLOCKED  {row['id']:>10}  {row['path_zh']}  ({row['reason']}: {row['matched']})")
    for row in flagged:
        print(f"  REVIEW   {row['id']:>10}  {row['path_zh']}  ({row['reason']}: {row['matched']})")

    print(f"\nwritten: {os.path.relpath(paths['all'], HERE)}, "
          f"{os.path.relpath(paths['blocked'], HERE)}, "
          f"{os.path.relpath(paths['review'], HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

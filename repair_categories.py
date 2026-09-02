"""
Give a Saudi name to every category that is still stuck in Chinese.

    python3 repair_categories.py --dry-run     # count them, spend nothing
    python3 repair_categories.py               # translate and write back

Why this exists. `src/category_live.py` learns a category from 1688 and caches
it, and until 2 September it cached the Chinese name whenever the model call
did not come back - as though Chinese were the answer. `_known` then served
that row forever, so one failed call became permanent. On the live server that
left 649 of 902 learned categories in Chinese, and 24 of 102 published products
carrying a Chinese department name: the client's own report was

    "a category named Accessories & Jewelry, inside it a subcategory in
     Chinese, and inside that subcategory hat products"    -- 成人帽

The permanence is fixed in category_live (`_retranslate` records nothing on
failure, so the next run asks again). This script is the one-off repair of the
rows that were already written, done in batches of forty rather than one call
each, because the pipeline's own path pays for one name at a time.

The cache is only ever improved: a row is written back only when both names
come back non-empty and free of Chinese. Nothing is deleted, so a bad run
cannot cost us the ids we have already paid the gateway to learn.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import category_live  # noqa: E402
import enrich  # noqa: E402

BATCH = int(os.environ.get("KDX_LABELS_PER_CALL", "40"))
TREE = os.path.join(HERE, "data", "categories.json")


def stuck_rows(cache: dict) -> list:
    return [row for row in cache.values()
            if row.get("name_zh") and not category_live.is_translated(row)]


def chinese_names(cache: dict) -> dict:
    """
    id -> (Chinese name, parent id) for everything we can name.

    The built tree is loaded too, because the roots live there and a leaf's
    path stops dead without them.
    """
    names = {}
    try:
        with open(TREE, encoding="utf-8") as handle:
            for row in json.load(handle):
                names[str(row["id"])] = (row.get("name_zh") or "",
                                         None if row.get("parent_id") is None
                                         else str(row["parent_id"]))
    except (OSError, ValueError):
        pass
    for row in cache.values():
        parent = row.get("parent_id")
        names[str(row["id"])] = (row.get("name_zh") or "",
                                 None if parent in (None, "", "0", "None") else str(parent))
    return names


def path_of(category_id: str, names: dict) -> str:
    """
    "服饰配件、饰品 > 饰品配件 > 水钻".

    Sent instead of the bare name because a category name on its own is often
    ambiguous: 水钻 alone came back from the model as "water drills", where
    under jewellery parts it is a rhinestone.
    """
    parts, seen = [], set()
    current = str(category_id)
    for _ in range(category_live.MAX_CLIMB):
        if current in seen or current not in names:
            break
        seen.add(current)
        name, parent = names[current]
        if name:
            parts.insert(0, name)
        if not parent:
            break
        current = parent
    return " > ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=category_live.cache_path())
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is stuck without calling the model")
    parser.add_argument("--limit", type=int, default=0,
                        help="repair at most this many, for a cheap first look")
    parser.add_argument("--all", action="store_true",
                        help="re-name every learned category, not only the "
                             "Chinese ones. Worth doing once: the names done "
                             "before 2 September were translated without their "
                             "parents, and 水钻 came back as 'water drills'")
    args = parser.parse_args()

    with open(args.cache, encoding="utf-8") as handle:
        cache = json.load(handle)

    stuck = stuck_rows(cache)
    print(f"{len(cache)} learned categories, {len(stuck)} still in Chinese")
    targets = [row for row in cache.values() if row.get("name_zh")] if args.all else stuck
    if args.limit:
        targets = targets[:args.limit]
    if not targets or args.dry_run:
        for row in targets[:20]:
            print(f"  {row['id']:>12}  {row['name_zh']}")
        return 0

    lookup = chinese_names(cache)
    # One entry per DISTINCT path. Sibling ids share a name often enough that
    # this is worth doing: 成人帽 appeared under two different parents.
    paths = {str(row["id"]): path_of(row["id"], lookup) for row in targets}
    names = sorted({path for path in paths.values() if path})
    print(f"{len(names)} distinct paths, {-(-len(names) // BATCH)} model call(s)")

    answers: dict = {}
    for start in range(0, len(names), BATCH):
        window = names[start:start + BATCH]
        try:
            answers.update(enrich.translate_categories(window))
        except Exception as exc:  # noqa: BLE001
            print(f"  batch at {start}: {type(exc).__name__}: {exc}")
        print(f"  {min(start + BATCH, len(names))}/{len(names)}")

    fixed = 0
    for row in targets:
        entry = answers.get(paths.get(str(row["id"])) or "") or {}
        english, arabic = str(entry.get("en") or "").strip(), str(entry.get("ar") or "").strip()
        if not english or not arabic:
            continue
        if category_live._CJK.search(english) or category_live._CJK.search(arabic):
            continue
        row["name_en"], row["name_ar"] = english, arabic
        cache[str(row["id"])] = row
        fixed += 1

    with open(args.cache, "w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=1)

    left = len(stuck_rows(cache))
    print(f"\n{fixed} repaired, {left} still in Chinese")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

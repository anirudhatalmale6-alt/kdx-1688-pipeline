"""
The night's run: discover, price, publish. This is what the cron job calls.

    # what the server runs at 00:00 Riyadh
    python3 daily_run.py

    # everything except the publish, so a change can be checked first
    python3 daily_run.py --dry-run

    # a small run against the live gateway, to watch it work
    python3 daily_run.py --dry-run --quota 20

The order matters and is the client's rule, not a preference: filter first,
spend second. Discovery drops a product whose category is banned before it is
counted against the day, so the quota is spent on products that could actually
be published.

One run at a time. A night that overruns must not meet its own successor at
midnight - two copies would both discover, both publish, and the ledger written
by the slower one would forget what the faster one had done.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sys
import time
from datetime import datetime
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import budget as budget_module  # noqa: E402
import catalog  # noqa: E402
import discover  # noqa: E402
import paths  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import rules  # noqa: E402
import selected  # noqa: E402

LOCK_PATH = paths.state_path("daily.lock", "KDX_LOCK")
# A machine that loses power mid-run leaves the lock behind. Twelve hours is
# longer than any plausible run and shorter than the gap to the next midnight.
STALE_LOCK_SECONDS = int(os.environ.get("KDX_LOCK_STALE_SECONDS", str(12 * 3600)))


class Locked(RuntimeError):
    pass


def pool_keywords(runner, day: str, count: int) -> list:
    """
    The Chinese words tonight's pool search will use.

    The pool listing serves 2,000 offers per keyword, so which words are asked
    decides what the shop can reach. They come from the category tree that is
    already built and already vetted - only leaves marked `allowed`, so a word
    from a banned branch is never even asked - and the starting point moves with
    the date. Without that rotation every night would ask the same first words
    and walk the same offers it published yesterday.
    """
    rows = getattr(runner.categories, "rows", None) or []
    words = []
    for row in rows:
        if not (row.get("is_leaf") and row.get("state") == catalog.ALLOWED):
            continue
        name = str(row.get("name_zh") or "")
        # 停用 means 1688 retired the category. The name is still in the tree
        # and still marked allowed, but searching for it is a wasted walk: the
        # first live keyword run spent four of its eight words on retired
        # categories and got nothing from any of them.
        if not name or "停用" in name:
            continue
        # A trailing "（...）" is a qualifier on the category, not part of what a
        # supplier calls a product, and it narrows the search to nothing.
        name = re.sub(r"[（(][^）)]*[）)]?$", "", name).strip("、/ ")
        if name:
            words.append(name)
    if not words:
        return []
    offset = int("".join(ch for ch in day if ch.isdigit()) or "0") % len(words)
    rotated = words[offset:] + words[:offset]
    return rotated[:count]


def harvest_selected(runner, client, quota: int, ledger: "discover.Ledger",
                     keywords: list | None = None) -> tuple:
    """
    A night's products from the 精选货源 pool instead of the image search.

    Same two filters the image walk applies before spending anything - a banned
    category and a banned term are settled from the row we already hold - and
    the same ledger, so a product the shop already carries is not published a
    second time. Returns (products, notes).

    With keywords the pool is searched word by word; without them it serves its
    default window, which is 2,000 offers and repeats itself once the shop has
    them. That is why keywords are the normal path: the window is a shelf, the
    keywords are the catalogue.
    """
    pool = selected.SelectedPool(client)
    # More ids than the quota, because the two filters below reject some and a
    # night that harvested exactly `quota` ids would publish fewer than asked.
    want = max(quota * 4, 50)
    if keywords:
        ids = pool.offer_ids_for(keywords, limit=want, known=ledger.knows_offer)
        contributing = [f"{word}:{n}" for word, n in pool.keyword_counts.items() if n]
        notes = [f"{len(keywords)} keyword(s) searched, "
                 f"{len(contributing)} of them had something new",
                 "new offers per word: " + (", ".join(contributing[:12]) or "none")]
    else:
        ids = [offer for offer in pool.offer_ids() if not ledger.knows_offer(offer)][:want]
        notes = [f"pool window holds {pool.pages_walked} page(s); "
                 f"{len(ids)} offers the shop does not already carry"]
    if not ids:
        notes.append("nothing new in the pool tonight - every offer these words "
                     "return is already published")
        return [], notes

    harvested, dropped = [], 0
    for product in pool.products(offer_ids=ids):
        if len(harvested) >= quota:
            break
        state = (runner.categories.state_of(product.get("category_id"))
                 if runner.categories is not None else "unknown")
        if state in (catalog.BLOCKED, catalog.REVIEW):
            dropped += 1
            continue
        try:
            if rules.find_banned_term(pipeline_module.to_rules_product(product)):
                dropped += 1
                continue
        except Exception:                                 # noqa: BLE001
            pass
        harvested.append(product)
        ledger.add_offer(product["offer_id"])
    ledger.save()
    notes.append(f"{dropped} dropped on category or banned term before any spend")
    if pool.skipped_outside_pool:
        notes.append(f"{len(pool.skipped_outside_pool)} refused as outside the pool")
    return harvested, notes


def take_lock(path: str = LOCK_PATH) -> int:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        age = time.time() - os.path.getmtime(path)
        if age < STALE_LOCK_SECONDS:
            raise Locked(
                f"another run holds {path} (started {age / 3600:.1f} h ago). "
                f"If that run is really dead, delete the file.") from exc
        # Old enough that the process cannot still be running. Say so in the
        # log rather than clearing it silently: a stale lock means a run died.
        print(f"clearing a stale lock, {age / 3600:.1f} h old: {path}")
        os.unlink(path)
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.write(handle, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}\n"
             .encode("utf-8"))
    return handle


def release_lock(handle: int, path: str = LOCK_PATH) -> None:
    try:
        os.close(handle)
        os.unlink(path)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="One night of the KDX run")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except publish to KDX")
    parser.add_argument("--quota", type=int,
                        help="products to attempt; default is the day's remaining points")
    parser.add_argument("--seeds", help="seed photograph list; default $KDX_SEEDS")
    parser.add_argument("--report", help="where to write the run report")
    parser.add_argument("--rate", help="CNY to SAR, instead of today's fetched rate")
    parser.add_argument("--keywords",
                        help="comma-separated Chinese words for the pool search; "
                             "default is tonight's slice of the allowed category names")
    parser.add_argument("--keyword-count", type=int, default=12,
                        help="how many category names to search when --keywords is absent")
    parser.add_argument("--channel", choices=("image", "selected"), default="image",
                        help="where products come from: the LinkPlus image search "
                             "(one photograph per product, all of 1688) or the "
                             "精选货源 pool (four to five photographs, 1,950 offers)")
    args = parser.parse_args()

    started = time.time()
    day = budget_module.business_day()
    print(f"=== KDX run for {day} (Riyadh) "
          f"{'DRY RUN, nothing will be published' if args.dry_run else ''}")

    # Seeds are the image search's starting photographs. The pool channel has
    # nothing to seed - it walks a list - so demanding them there would refuse
    # to start a run that needs none.
    seeds = []
    if args.channel == "image":
        seeds = discover.read_seeds(args.seeds or "")
        print(f"{len(seeds)} seed photograph(s)")

    try:
        lock = take_lock()
    except Locked as exc:
        print(f"not starting: {exc}")
        return 2

    try:
        runner = pipeline_module.build(
            dry_run=args.dry_run,
            cny_to_sar=Decimal(args.rate) if args.rate else None,
        )
        quota = args.quota if args.quota is not None else runner.budget.remaining()
        print(f"quota for tonight: {quota} "
              f"({runner.budget.spent}/{runner.budget.daily} already spent today)")
        if quota <= 0:
            print("the day's points are gone; nothing to do until midnight")
            return 0

        walker = None
        words: list = []
        if args.channel == "selected":
            book = discover.Ledger()
            client = runner.sku_client or getattr(runner.source, "client", None)
            if client is None:
                print("the selected pool needs 1688 credentials in the environment")
                return 2
            words = ([w.strip() for w in args.keywords.split(",") if w.strip()]
                     if args.keywords else pool_keywords(runner, day, args.keyword_count))
            print(f"pool search words ({len(words)}): {', '.join(words[:12])}")
            if not words:
                # Said loudly because the fallback is silent and plausible: the
                # run walks the unfiltered window instead, publishes real
                # products, and reports success while the keyword channel it
                # was asked for searched nothing. That is exactly what happened
                # on the first live run - the category index in use had no
                # `rows` attribute.
                print("  WARNING: no search words - the category table gave none, "
                      "so this run only sees the default 2,000-offer window. "
                      "Pass --keywords to search on purpose.")
            harvested, notes = harvest_selected(runner, client, quota, book, words)
            ledger = book.summary()
            print(f"selected pool: {len(harvested)} products worth pricing")
            for note in notes:
                print(f"           note: {note}")
        else:
            walker = discover.Discovery(runner.source, discover.Ledger(),
                                        categories=runner.categories, day=day)
            harvested = walker.run(seeds, quota)
            ledger = walker.ledger.summary()
            print(f"discovery: {len(harvested)} products worth pricing "
                  f"({walker.from_surplus} from last night's surplus, "
                  f"{walker.searches} gateway searches), "
                  f"{walker.rejected_early} dropped on category or banned term")
            print(f"           ledger knows {ledger['offers_known']} offers, "
                  f"{ledger['waiting']} waiting for a future night, "
                  f"{ledger['new_per_search']} new per search over its lifetime")
            for note in walker.notes:
                print(f"           note: {note}")

        # A run that prints only counts cannot be checked. The assembled
        # products are written out so the client can read what would have been
        # published before any of it reaches his shop - and on a live run so
        # that "what exactly did we send for this product" has an answer
        # afterwards. On 2026-08-30 twenty-one products reached his shop
        # without their photographs and nothing on disk could say what was in
        # the payload, because only dry runs were ever written.
        products_dir = os.path.join(paths.state_path("out", "KDX_OUT_DIR"), day)
        os.makedirs(products_dir, exist_ok=True)

        published = held = skipped = 0
        photos_dropped = 0
        updated: list = []
        reasons: dict = {}
        for outcome in runner.run_products(harvested):
            # Written before the error check, not after: a product his shop
            # refused is exactly the one whose payload has to be readable.
            if products_dir and outcome.product is not None:
                with open(os.path.join(products_dir, f"{outcome.offer_id}.json"),
                          "w", encoding="utf-8") as handle:
                    json.dump(outcome.product, handle, ensure_ascii=False,
                              indent=2, default=str)
            if outcome.photos and outcome.photos.get("dropped"):
                photos_dropped += len(outcome.photos["dropped"])
                for url in outcome.photos["dropped"]:
                    print(f"  {outcome.offer_id}  photo unreachable, dropped: {url[:100]}")
            # An update is not a second copy of the product, but it IS a second
            # copy of its options: his import appends them. Measured 2 September
            # - a product pushed twice showed 291 colour options where it has
            # 146. Nothing here can undo it, so it is reported by offer id and
            # a person decides.
            if pipeline_module.was_update(outcome.kdx_response or {}):
                updated.append(outcome.offer_id)
                print(f"  {outcome.offer_id}  UPDATED an existing product - his import "
                      f"appends options, so check this one for duplicates")
            if outcome.error:
                skipped += 1
                print(f"  {outcome.offer_id}  SKIPPED  {outcome.error}")
                continue
            for result in outcome.results:
                if result.decision != rules.Decision.PUBLISH:
                    held += 1
                    code = result.audit.reason_code
                    reasons[code] = reasons.get(code, 0) + 1
            published += outcome.published

        elapsed = time.time() - started
        report = {
            "day": day,
            "dry_run": bool(args.dry_run),
            "seconds": round(elapsed, 1),
            "quota": quota,
            "channel": args.channel,
            "keywords": words,
            "discovered": len(harvested),
            "from_surplus": walker.from_surplus if walker else 0,
            "dropped_before_pricing": walker.rejected_early if walker else None,
            "gateway_searches": walker.searches if walker else None,
            "published": published,
            "held": held,
            "skipped": skipped,
            "held_reasons": reasons,
            "photos_dropped": photos_dropped,
            "updated_existing": updated,
            "photos": runner.photos.summary() if runner.photos is not None else None,
            "ledger": ledger,
            "points": runner.budget.summary(),
            "searches": runner.meter.summary() if runner.meter is not None else None,
        }
        print(f"\n{published} {'assembled' if args.dry_run else 'published'}, "
              f"{held} held, {skipped} skipped, in {elapsed / 60:.1f} min")
        for code, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  held {count:4}  {code}")

        path = args.report or paths.state_path("reports", "KDX_REPORT_DIR")
        if not path.endswith(".json"):
            os.makedirs(path, exist_ok=True)
            path = os.path.join(path, f"run-{day}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
        print(f"report: {path}")
        if products_dir:
            print(f"products: {products_dir}/")
        return 0
    finally:
        release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(main())

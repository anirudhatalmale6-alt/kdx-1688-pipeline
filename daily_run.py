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
import glob
import json
import os
import sys
import time
from datetime import datetime
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import budget as budget_module  # noqa: E402
import catalog  # noqa: E402
import completeness  # noqa: E402
import discover  # noqa: E402
import paths  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import mapping  # noqa: E402
import risk  # noqa: E402
import rules  # noqa: E402
import selected  # noqa: E402
import wordlist  # noqa: E402

LOCK_PATH = paths.state_path("daily.lock", "KDX_LOCK")
# A machine that loses power mid-run leaves the lock behind. Twelve hours is
# longer than any plausible run and shorter than the gap to the next midnight.
#
# Twelve hours turned out to be twelve hours of silence. On 4 September the
# 01:21 batch hit the unit's 15-minute TimeoutStartSec, systemd killed it, and
# the lock it was holding outlived it. Every batch from 01:41 to 07:22 - twenty
# of them - started, read the lock, and exited in one second. The pull was dead
# for six hours and nothing said so, because from systemd's side each of those
# runs "Finished" successfully.
#
# So age is the wrong question to ask first. The lock records the pid that took
# it; ask the operating system whether that pid is still there.
STALE_LOCK_SECONDS = int(os.environ.get("KDX_LOCK_STALE_SECONDS", str(12 * 3600)))


class Locked(RuntimeError):
    pass


def process_start_time(pid: int) -> str:
    """
    The boot-clock tick at which this pid started, or "" if it cannot be read.

    Field 22 of /proc/<pid>/stat. It is what makes a pid an identity rather than
    a number: pids are recycled, start times are not, so a pid that matches AND
    started when we said it did is the same process we wrote down.

    Field 2 is the executable name in brackets and may itself contain spaces or
    brackets, so the split is anchored on the LAST ")" rather than on the first
    space - the usual way this parse goes wrong.
    """
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError:
        return ""
    try:
        fields = raw[raw.rindex(")") + 2:].split()
        return fields[19]           # field 22 overall; 20th after the two skipped
    except (ValueError, IndexError):
        return ""


def lock_holder_alive(path: str) -> bool:
    """
    Is the process that wrote this lock still running?

    Deliberately one-sided: every answer it cannot make confidently is True,
    "assume held". Clearing a lock that IS held would put two runs on one ledger
    - the failure this whole mechanism exists to prevent - while refusing to
    clear one that is not held costs only the twelve-hour rule already in place.

    Identity is pid + start time, never the process's name. The first version of
    this function matched "daily_run" in /proc/<pid>/cmdline and the suite caught
    it immediately: verify_discover.py takes a real lock to prove a second run is
    refused, and by that rule its own living process read as dead. A lock is held
    by whoever took it, whatever that program happens to be called.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parts = handle.read().split()
        pid = int(parts[0])
        recorded = parts[2]
    except (OSError, ValueError, IndexError):
        return True  # unreadable, or written by a version that stored no start
    if pid <= 0:
        return True
    started = process_start_time(pid)
    if not started:
        return False  # no such pid (or no /proc entry for it): the run is gone
    return started == recorded


def pool_keywords(runner, day: str, count: int) -> list:
    """
    The Chinese words this run's pool search will use.

    The pool listing serves about 2,000 offers per keyword, so which words are
    asked decides what the shop can reach. `wordlist` builds them from the
    departments the client says he sells - `data/departments.json` - and this
    function only picks the day's slice. The rules, and the two ways the first
    version got them wrong, are written down in that module.
    """
    rows = getattr(runner.categories, "rows", None) or []
    return wordlist.slice_for_day(wordlist.build(rows), day, count)


def harvest_selected(runner, client, quota: int, ledger: "discover.Ledger",
                     keywords: list | None = None, shipping: str = "any") -> tuple:
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

    `shipping` is the client's request of 2 September - "please pull two
    batches, fast shipping and free shipping". It reads the weight the pool has
    just worked out and keeps only the side asked for. It cannot be done any
    earlier: the pool listing carries no weight at all, so the detail record has
    to arrive before the product can be sorted.
    """
    pool = selected.SelectedPool(client, categories=runner.categories)
    # More ids than the quota, because the two filters below reject some and a
    # night that harvested exactly `quota` ids would publish fewer than asked.
    want = max(quota * 4, 50)
    if shipping == "free":
        # Measured 3 September over 848 credible declared weights: 61 of them,
        # 7.2%, sit over the 2 kg line. A free-shipping batch therefore has to
        # look at roughly fourteen offers for every one it keeps, and asking for
        # quota*4 would quietly return two products and look like the pool had
        # run dry. This is the multiplier that measurement implies, not a guess.
        want = max(quota * 20, 200)
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

    harvested, dropped, wrong_side, unweighed, off_department = [], 0, 0, 0, 0
    departments_off = catalog.departments_off()
    for product in pool.products(offer_ids=ids):
        if len(harvested) >= quota:
            break
        state = (runner.categories.state_of(product.get("category_id"))
                 if runner.categories is not None else "unknown")
        if state in (catalog.BLOCKED, catalog.REVIEW):
            dropped += 1
            continue
        # His thirteen switched-off departments, enforced. Until 3 September the
        # list only chose which words to SEARCH for, so an offer the pool
        # returned from a department he had turned off would have published.
        # Counted apart from `dropped` so the two reasons never merge into one
        # number - and today that count is zero, which is the point: it is a
        # guard, not the thing that stops the food and the chemicals.
        if runner.categories is not None:
            why = runner.categories.department_is_off(product.get("category_id"),
                                                      departments_off)
            if why:
                off_department += 1
                continue
        try:
            if rules.find_banned_term(pipeline_module.to_rules_product(product)):
                dropped += 1
                continue
        except Exception:                                 # noqa: BLE001
            pass
        # A weight nobody stated cannot sort a product into either batch, and
        # since 3 September it cannot publish one either - his rule, "if the
        # information is unclear, exclude it". Counted separately from the
        # wrong-side tally because they are a different fact about the
        # catalogue: those are products for the other batch, these are products
        # for neither.
        if not completeness.has_usable_weight(product):
            unweighed += 1
            continue
        if shipping in ("fast", "free"):
            # The same function the payload is built with, so the batch cannot
            # be sorted by one rule and published under another.
            fast = mapping.needs_shipment(product.get("weight_kg", 0))
            if (shipping == "fast") != bool(fast):
                wrong_side += 1
                # Deliberately NOT added to the ledger. A heavy product passed
                # over by the fast batch has not been published, and marking it
                # known here would hide it from the free batch that is coming
                # for exactly this product.
                continue
        harvested.append(product)
        ledger.add_offer(product["offer_id"])
    ledger.save()
    notes.append(f"{dropped} dropped on category or banned term before any spend")
    notes.append(f"{off_department} dropped as belonging to a department he "
                 f"switched off - enforced since 3 September, previously the "
                 f"list only chose search words")
    notes.append(f"{unweighed} passed over with no weight from anywhere - neither "
                 f"the supplier nor their category could answer, so they cannot "
                 f"be filed as fast or free without guessing")
    if shipping in ("fast", "free"):
        notes.append(f"{wrong_side} passed over as {'heavy' if shipping == 'fast' else 'light'}"
                     f" - this batch is {shipping} shipping only, and they stay "
                     f"available to the other batch")
    if pool.skipped_outside_pool:
        notes.append(f"{len(pool.skipped_outside_pool)} refused as outside the pool")

    # Saved here rather than inside the walk: `products` is a generator and the
    # loop above breaks out of it as soon as the quota is full, so a save at the
    # end of the generator would run on exactly the batches that learned least.
    #
    # Every offer the walk LOOKED at teaches the table, including the ones
    # dropped on a banned category and the ones past the quota - their declared
    # weight is just as true, and throwing it away would mean learning only from
    # what happened to be published.
    pool.weights.save()
    weighed = sum(1 for product in harvested if not product.get("weight_assumed"))
    learned = sum(1 for product in harvested if product.get("weight_samples"))
    notes.append(f"weight: {weighed} of {len(harvested)} declared by the supplier, "
                 f"{learned} from the category's own measurements, "
                 f"{len(harvested) - weighed - learned} on the light default")
    table = pool.weights.summary()
    notes.append(f"weight table now holds {table['samples']} measurements over "
                 f"{table['categories']} categories; {table['with_an_opinion']} of "
                 f"them can answer ({table['of_those_heavy']} heavy), "
                 f"{table['straddling_the_line']} straddle the 2 kg line and are "
                 f"never asked")
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
        if not lock_holder_alive(path):
            # The holder is gone - killed, crashed, or the machine restarted.
            # Nothing is running, so nothing can be raced, whatever the age.
            print(f"clearing the lock of a run that is no longer there "
                  f"({age / 3600:.1f} h old): {path}")
        elif age < STALE_LOCK_SECONDS:
            raise Locked(
                f"another run holds {path} (started {age / 3600:.1f} h ago). "
                f"If that run is really dead, delete the file.") from exc
        else:
            # Old enough that the process cannot still be running. Say so in the
            # log rather than clearing it silently: a stale lock means a run died.
            print(f"clearing a stale lock, {age / 3600:.1f} h old: {path}")
        os.unlink(path)
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.write(handle, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')} "
                     f"{process_start_time(os.getpid())}\n".encode("utf-8"))
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
    parser.add_argument("--shipping", choices=("any", "fast", "free"), default="any",
                        help="publish only products that land on one side of the "
                             "2 kg line: 'fast' is the light ones the customer "
                             "pays carriage on, 'free' the heavy ones that ship "
                             "free. The client's two batches. Only 7%% of the "
                             "pool is heavy, so a free batch walks much further")
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
                print("  WARNING: no search words - the category table and "
                      "data/departments.json between them gave none, so this run "
                      "only sees the default 2,000-offer window. Pass --keywords "
                      "to search on purpose.")
            harvested, notes = harvest_selected(runner, client, quota, book, words,
                                                shipping=args.shipping)
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
        # His import went background on 4 September and now answers "received,
        # processing" with no counters. Those products are accepted, not
        # confirmed, and they are counted apart so the run never claims to have
        # seen something land that nobody has looked at.
        acknowledged = 0
        # `published` counts variants, because that is what a shop row is. The
        # count of PRODUCTS is what a person means by "how many went up", and
        # printing only the first read as the second: a run of five products
        # reported "40 published". Both are counted, and both are printed.
        products_published = 0
        photos_dropped = 0
        updated: list = []
        reasons: dict = {}

        # What 1688 thinks of the rate this account is listing at. One
        # read-only call, ahead of the publishing rather than after it, because
        # a warning that arrives once the products are already up has cost the
        # thing it was meant to protect. The two counts it wants are ours: the
        # product files written today, and every one we have ever written. They
        # are our record of what we sent, not his shop's record of what it kept,
        # so the reading is reported with the figures it was taken from.
        risk_reading = None
        risk_client = runner.sku_client or getattr(runner.source, "client", None)
        if risk_client is not None and not args.dry_run:
            out_root = paths.state_path("out", "KDX_OUT_DIR")
            risk_reading = risk.check(
                risk_client,
                published_today=len(glob.glob(os.path.join(products_dir, "*.json"))),
                on_sale=len(glob.glob(os.path.join(out_root, "*", "*.json"))))
            if risk_reading.get("error"):
                print(f"  1688 risk level unread: {risk_reading['error']}")
        if risk.should_stop(risk_reading or {}):
            print(f"  1688 rates this account's listing volume "
                  f"{risk_reading['raw']} ({risk_reading['level']}). Nothing is "
                  f"published this run - the account is worth more than the "
                  f"afternoon. Clear it with 1688, or set KDX_IGNORE_RISK=1.")
            harvested = []

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
            if pipeline_module.is_acknowledgement(outcome.kdx_response or {}):
                acknowledged += 1
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
            if outcome.published:
                products_published += 1

        elapsed = time.time() - started
        report = {
            "day": day,
            "risk": risk_reading,
            "dry_run": bool(args.dry_run),
            "seconds": round(elapsed, 1),
            "quota": quota,
            "channel": args.channel,
            "shipping": args.shipping,
            "keywords": words,
            "discovered": len(harvested),
            "from_surplus": walker.from_surplus if walker else 0,
            "dropped_before_pricing": walker.rejected_early if walker else None,
            "gateway_searches": walker.searches if walker else None,
            "published": published,
            "products_published": products_published,
            "held": held,
            "skipped": skipped,
            "held_reasons": reasons,
            "photos_dropped": photos_dropped,
            "updated_existing": updated,
            "accepted_not_confirmed": acknowledged,
            "photos": runner.photos.summary() if runner.photos is not None else None,
            "ledger": ledger,
            "points": runner.budget.summary(),
            "searches": runner.meter.summary() if runner.meter is not None else None,
        }
        verb = "assembled" if args.dry_run else "published"
        print(f"\n{products_published} product(s) {verb} "
              f"({published} sellable option(s)), "
              f"{held} held, {skipped} skipped, in {elapsed / 60:.1f} min")
        for code, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  held {count:4}  {code}")
        if acknowledged:
            print(f"  note: {acknowledged} of them were ACCEPTED but not "
                  f"confirmed - his import answers 'received, processing in the "
                  f"background' and reports no counts, so nothing here has seen "
                  f"them land. They are not pushed again, because a second push "
                  f"is how one product becomes two.")

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

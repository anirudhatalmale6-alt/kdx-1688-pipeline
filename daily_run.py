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
import sys
import time
from datetime import datetime
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import budget as budget_module  # noqa: E402
import discover  # noqa: E402
import paths  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import rules  # noqa: E402

LOCK_PATH = paths.state_path("daily.lock", "KDX_LOCK")
# A machine that loses power mid-run leaves the lock behind. Twelve hours is
# longer than any plausible run and shorter than the gap to the next midnight.
STALE_LOCK_SECONDS = int(os.environ.get("KDX_LOCK_STALE_SECONDS", str(12 * 3600)))


class Locked(RuntimeError):
    pass


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
    args = parser.parse_args()

    started = time.time()
    day = budget_module.business_day()
    print(f"=== KDX run for {day} (Riyadh) "
          f"{'DRY RUN, nothing will be published' if args.dry_run else ''}")

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
            "discovered": len(harvested),
            "from_surplus": walker.from_surplus,
            "dropped_before_pricing": walker.rejected_early,
            "gateway_searches": walker.searches,
            "published": published,
            "held": held,
            "skipped": skipped,
            "held_reasons": reasons,
            "photos_dropped": photos_dropped,
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

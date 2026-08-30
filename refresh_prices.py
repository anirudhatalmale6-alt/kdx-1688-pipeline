"""
Re-price the products his shop already holds, and send the new prices.

    python3 refresh_prices.py --dry-run --limit 10     # nothing is sent
    python3 refresh_prices.py --limit 50               # updates 50 products

Until 2026-08-30 this could not exist. The import route inserted and never
updated - the same offer id came back `skipped_count: 1` with `success: true` -
so a daily price refresh would have changed nothing while reporting success.
His developer made the route upsert on 30 August, and a control pair against the
live endpoint proved it: a fresh id answered `imported_count: 1`, the same id at
a different price answered `updated_count: 1`, and the public product page then
showed the new price, the new name and both photographs.

The other half is finding the offer again. This channel has no lookup, so each
product is found through its own photograph - see src/relookup.py, six of six
within two pages when measured. A product that cannot be found again keeps the
price it has and is counted as not-found, because a refresh that silently
freezes prices looks exactly like one that works.

Every refreshed product goes through the same pricing path as a new one: the
same competitor comparison, the same undercut bands, the same floors. There is
no second pricing implementation to drift out of step.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import budget as budget_module  # noqa: E402
import daily_run  # noqa: E402
import paths  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import relookup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the prices of published products")
    parser.add_argument("--dry-run", action="store_true",
                        help="re-price but send nothing to KDX")
    parser.add_argument("--limit", type=int, default=0,
                        help="how many products to refresh; default is all of them")
    parser.add_argument("--pages", type=int, default=0,
                        help="pages of its own photograph to search per product")
    parser.add_argument("--report", help="where to write the run report")
    parser.add_argument("--rate", help="CNY to SAR, instead of today's fetched rate")
    args = parser.parse_args()

    started = time.time()
    day = budget_module.business_day()
    out_root = paths.state_path("out", "KDX_OUT_DIR")
    targets = relookup.refresh_targets([out_root], args.limit)
    print(f"=== KDX price refresh for {day} (Riyadh)"
          f"{'  DRY RUN, nothing will be sent' if args.dry_run else ''}")
    print(f"{len(targets)} product(s) already published, from {out_root}")
    if not targets:
        print("nothing published yet; nothing to refresh")
        return 0

    try:
        lock = daily_run.take_lock()
    except daily_run.Locked as exc:
        print(f"not starting: {exc}")
        return 2

    try:
        runner = pipeline_module.build(
            dry_run=args.dry_run,
            cny_to_sar=Decimal(args.rate) if args.rate else None,
        )

        found, missing, searches = [], [], 0
        was_price = {t["offer_id"]: t.get("price") for t in targets}
        for target in targets:
            try:
                row = relookup.find(runner.source, target["offer_id"],
                                    target["image"], args.pages)
            except relookup.NotFound as exc:
                missing.append(target["offer_id"])
                print(f"  {target['offer_id']}  NOT FOUND today, price left as it is")
                continue
            except Exception as exc:  # noqa: BLE001
                missing.append(target["offer_id"])
                print(f"  {target['offer_id']}  lookup failed: {type(exc).__name__}: "
                      f"{str(exc)[:100]}")
                continue
            searches += int(row.get("_relookup_searches") or 1)
            found.append(row)
        print(f"found {len(found)} of {len(targets)} in {searches} gateway search(es)")

        updated = inserted = held = errors = 0
        changed, unchanged = 0, 0
        for outcome in runner.run_products(found):
            if outcome.error:
                errors += 1
                print(f"  {outcome.offer_id}  {outcome.error}")
                continue
            if outcome.published < 1:
                held += 1
                continue
            if pipeline_module.was_update(outcome.kdx_response or {}):
                updated += 1
            elif outcome.kdx_response:
                # An offer his shop did not already hold: it was deleted there,
                # and the refresh has just put it back. Worth seeing, not hiding.
                inserted += 1
            was = was_price.get(str(outcome.offer_id))
            now = outcome.product.get("price") if outcome.product else None
            if was is not None and now is not None and float(was) != float(now):
                changed += 1
                print(f"  {outcome.offer_id}  {was} -> {now} SAR")
            else:
                unchanged += 1

        elapsed = time.time() - started
        report = {
            "day": day,
            "kind": "price-refresh",
            "dry_run": bool(args.dry_run),
            "seconds": round(elapsed, 1),
            "published_products": len(targets),
            "found_again": len(found),
            "not_found": missing,
            "gateway_searches": searches,
            "updated": updated,
            "re_inserted": inserted,
            "held_by_rules": held,
            "errors": errors,
            "price_changed": changed,
            "price_unchanged": unchanged,
            "points": runner.budget.summary(),
            "searches": runner.meter.summary() if runner.meter is not None else None,
        }
        print(f"\n{updated} updated, {inserted} re-inserted, {held} held by the rules, "
              f"{errors} errored, {len(missing)} not found, in {elapsed / 60:.1f} min")
        print(f"{changed} price(s) moved, {unchanged} unchanged")

        path = args.report or paths.state_path("reports", "KDX_REPORT_DIR")
        if not path.endswith(".json"):
            os.makedirs(path, exist_ok=True)
            stamp = datetime.now().strftime("%H%M%S")
            path = os.path.join(path, f"refresh-{day}-{stamp}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
        print(f"report: {path}")
        return 0
    finally:
        daily_run.release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(main())

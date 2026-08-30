"""
Checks for the price-refresh path: finding an offer again, and reading his
shop's new answer.

    python3 verify_refresh.py

No network and no credentials. Every success is paired with a control, because
"it found the offer" means nothing unless "it refused to accept a near miss" is
also true - that is the failure that would put another seller's price on his
product and report a clean run.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import pipeline as pipeline_module  # noqa: E402
import relookup  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def raises(label: str, exception, call) -> None:
    global PASS, FAIL
    try:
        call()
    except exception:
        PASS += 1
        print(f"  ok    {label}")
        return
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  FAIL  {label}: raised {type(exc).__name__}, wanted {exception.__name__}")
        return
    FAIL += 1
    print(f"  FAIL  {label}: nothing raised")


class FakeSource:
    """A LinkPlus search whose pages are scripted, counting what it was asked."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls = []

    def search_by_image(self, image_url: str, page: int = 1) -> list:
        self.calls.append((image_url, page))
        return [{"offer_id": str(i), "images": [image_url]}
                for i in self.pages.get(page, [])]


def section(title: str) -> None:
    print(f"\n{title}")


def main() -> int:
    section("finding an offer again through its own photograph")

    source = FakeSource({1: [111, 222, 333]})
    row = relookup.find(source, "222", "http://img/a.jpg")
    check("the offer on page 1 is returned", row["offer_id"], "222")
    check("one search was spent", len(source.calls), 1)
    check("the page it was found on is recorded", row["_relookup_page"], 1)

    source = FakeSource({1: [111, 222], 2: [333, 444]})
    row = relookup.find(source, "444", "http://img/a.jpg", max_pages=2)
    check("an offer on page 2 is still found", row["offer_id"], "444")
    check("both pages were searched", [p for _u, p in source.calls], [1, 2])
    check("the searches spent are reported", row["_relookup_searches"], 2)

    # CONTROL: the offer is NOT there. The page is full of near misses - other
    # sellers' listings of the same kind of thing - and taking one of those
    # would put a stranger's price on his product.
    source = FakeSource({1: [111, 222], 2: [333, 444]})
    raises("a page of near misses is not a match", relookup.NotFound,
           lambda: relookup.find(source, "999", "http://img/a.jpg", max_pages=2))
    check("it stopped at the page ceiling", len(source.calls), 2)

    source = FakeSource({1: [111], 2: []})
    raises("an empty page ends the walk", relookup.NotFound,
           lambda: relookup.find(source, "999", "http://img/a.jpg", max_pages=5))
    check("it did not keep asking past the last page", len(source.calls), 2)

    source = FakeSource({1: [111]})
    raises("no photograph is not a lookup", relookup.NotFound,
           lambda: relookup.find(source, "111", ""))
    check("and it spent no search", len(source.calls), 0)

    section("which products get refreshed")

    root = tempfile.mkdtemp(prefix="kdx-refresh-")
    try:
        for day, offer, price, image in (
                ("2026-08-28", "A1", 10.0, "http://img/a1.jpg"),
                ("2026-08-28", "A2", 20.0, "http://img/a2.jpg"),
                ("2026-08-30", "A1", 11.0, "http://img/a1b.jpg"),   # newer payload
                ("2026-08-30", "A3", 30.0, ""),                     # no photograph
        ):
            os.makedirs(os.path.join(root, day), exist_ok=True)
            with open(os.path.join(root, day, f"{offer}.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"source_offer_id": offer, "price": price,
                           "images": [image] if image else []}, handle)

        targets = relookup.refresh_targets([root])
        by_id = {t["offer_id"]: t for t in targets}
        check("every published product with a photograph is a target",
              sorted(by_id), ["A1", "A2"])
        check("the newest payload wins", by_id["A1"]["price"], 11.0)
        check("and its photograph comes from that payload",
              by_id["A1"]["image"], "http://img/a1b.jpg")
        check("a product with no photograph cannot be looked up, so it is left out",
              "A3" in by_id, False)
        check("the limit is honoured", len(relookup.refresh_targets([root], 1)), 1)
        check("a directory that does not exist is not a crash",
              relookup.refresh_targets([os.path.join(root, "nope")]), [])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    section("reading his shop's answer, now that the route upserts")

    # Measured against the live endpoint on 2026-08-30, both halves of the pair.
    inserted = {"success": True, "imported_count": 1, "updated_count": 0,
                "skipped_count": 0, "failed_count": 0}
    updated = {"success": True, "imported_count": 0, "updated_count": 1,
               "skipped_count": 0, "failed_count": 0}
    check("an insert is a landing", pipeline_module._publish_trouble(inserted), "")
    check("an update is a landing too", pipeline_module._publish_trouble(updated), "")
    check("an update is reported as an update",
          pipeline_module.was_update(updated), True)
    check("an insert is not", pipeline_module.was_update(inserted), False)

    # CONTROLS: everything that is NOT a landing must still be caught. Before
    # 30 August all of these counted as published.
    check("zero counters is not a landing",
          pipeline_module._publish_trouble(
              {"success": True, "imported_count": 0, "updated_count": 0}) != "", True)
    check("a failure is caught",
          pipeline_module._publish_trouble(
              {"success": True, "failed_count": 1, "failed_items": ["bad"],
               "updated_count": 0}) != "", True)
    check("failed wins over a counter that looks fine",
          pipeline_module._publish_trouble(
              {"success": True, "updated_count": 1, "failed_count": 1}) != "", True)
    check("success:false is caught even with a counter set",
          pipeline_module._publish_trouble(
              {"success": False, "message": "no", "updated_count": 1}) != "", True)
    check("a skip now reports his shop's own reason",
          "KDX skipped" in pipeline_module._publish_trouble(
              {"success": True, "skipped_count": 1, "message": "duplicate sku"}), True)
    check("no answer at all is not treated as trouble",
          pipeline_module._publish_trouble({}), "")
    check("was_update on an empty answer is False",
          pipeline_module.was_update({}), False)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

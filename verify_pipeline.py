"""
Proof for the whole run, end to end.

    python3 verify_pipeline.py

No network, no keys. Recorded 1688 offers, a recorded image search, and a
temporary budget and audit file, so this is the complete path a real product
takes - read, filter, price, group, publish - with only the two outside calls
replaced by what they returned when they were recorded.

The point of this suite is the joins between stages, which is where the earlier
per-stage suites cannot see: that the price the engine calculated is the price
that lands under the right photograph, that a rejected size does not leave a
photo priced from a different one, and that the audit file adds up.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

WORK = tempfile.mkdtemp(prefix="kdx-verify-")
os.environ["KDX_BUDGET_STATE"] = os.path.join(WORK, "points.json")
os.environ["KDX_AUDIT_LOG"] = os.path.join(WORK, "audit.csv")

import audit as audit_module  # noqa: E402
import budget as budget_module  # noqa: E402
import catalog  # noqa: E402
import compare  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import rules  # noqa: E402
import source as source_module  # noqa: E402

passed = failed = 0

TSHIRT = "104843239419"
BOILER = "611229900011"


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


# Stands in for the model, so the joins between the stages can be tested without
# an API key in the way. It returns what the real translator returns for these
# two fixtures, which is what the recorded image search was recorded against.
NAMES = {
    "2024新款夏季女士短袖T恤 纯棉宽松百搭上衣":
        ("Cotton Short Sleeve T-Shirt Loose Summer Top", "تيشيرت قطن نسائي بأكمام قصيرة"),
    "商用不锈钢电热开水器 30L 大容量":
        ("Commercial Stainless Steel Electric Water Boiler 30L 3000W",
         "غلاية ماء كهربائية تجارية ستانلس ستيل 30 لتر"),
}
TERMS = {"白色": {"en": "White", "ar": "أبيض"}, "黑色": {"en": "Black", "ar": "أسود"},
         "S": {"en": "S", "ar": "S"}, "M": {"en": "M", "ar": "M"}}


def fake_enrich(title_zh, description_zh, **_kwargs):
    name_en, name_ar = NAMES.get(title_zh, (title_zh, title_zh))
    return {"name_en": name_en, "name_ar": name_ar,
            "description_ar": "وصف", "description_en": "description"}


def fake_terms(terms, **_kwargs):
    return {term: TERMS.get(term, {"en": term, "ar": term}) for term in terms}


# A miniature category tree covering the two fixtures, so the department that
# reaches KDX can be checked without depending on the 1497-row real one.
CATEGORY_ROWS = [
    {"id": 10166, "parent_id": None, "depth": 1, "name_zh": "女装",
     "name_en": "Women's Clothing", "name_ar": "ملابس نسائية",
     "path_zh": "女装", "is_leaf": False, "state": "allowed", "reason": "", "matched": ""},
    {"id": 1031910, "parent_id": 10166, "depth": 2, "name_zh": "连衣裙",
     "name_en": "Dresses", "name_ar": "فساتين",
     "path_zh": "女装 > 连衣裙", "is_leaf": True, "state": "allowed", "reason": "", "matched": ""},
    {"id": 130823000, "parent_id": None, "depth": 1, "name_zh": "成人用品",
     "name_en": "Adult Products", "name_ar": "منتجات للبالغين",
     "path_zh": "成人用品", "is_leaf": False, "state": "blocked",
     "reason": "sexual", "matched": "成人用品"},
]


def build(daily_points: int = 300, cny_to_sar: str = "0.52", translate: bool = True,
          state: str = "points.json", offers: str | None = None,
          categories=catalog.CategoryIndex(CATEGORY_ROWS)):
    return pipeline_module.Pipeline(
        categories=categories,
        source=source_module.FixtureSource(offers),
        provider=compare.FixtureProvider(),
        engine=rules.Engine(cny_to_sar=Decimal(cny_to_sar)),
        budget=budget_module.PointBudget(daily=daily_points,
                                         state_path=os.path.join(WORK, state)),
        audit_log=audit_module.AuditLog(os.path.join(WORK, "audit.csv")),
        translate=translate,
        enricher=fake_enrich,
        term_translator=fake_terms,
        dry_run=True,
    )


def main() -> int:
    print("1. a four-sku shirt becomes two priced photos")
    outcome = build().run_offer(TSHIRT)
    check("the offer is read without error", not outcome.error, outcome.error)
    check("all four skus are priced", len(outcome.results) == 4, str(len(outcome.results)))
    check("the sold-out size is not published", outcome.published == 3, str(outcome.published))
    product = outcome.product
    check("a product is produced", product is not None)
    check("with two photos", len(product["variants"]) == 2, str(len(product["variants"])))

    print("2. the join that matters: each price under its own photograph")
    white, black = product["variants"]
    check("white keeps both sizes", len(white["sizes"]) == 2, str(len(white["sizes"])))
    check("black keeps only the size that was in stock", len(black["sizes"]) == 1,
          str([s["original"] for s in black["sizes"]]))
    check("the white photo is on the white variant", white["image"].endswith("white.jpg"))
    check("and the black photo on the black one", black["image"].endswith("black.jpg"))

    # 28.50 CNY x 0.52 = 14.82 SAR landed, no competitor match for a shirt in the
    # recorded searches, so the client's margin band for the cheapest tier applies.
    engine = rules.Engine(cny_to_sar=Decimal("0.52"))
    expected, _ = rules.marked_up_price(engine.landed_cost_sar(
        rules.Variant(sku_id="x", attributes={}, price_cny=Decimal("28.50"),
                      stock=1, weight_kg=Decimal("0.35"))))
    check("the cheapest size carries the price the rules engine calculated",
          Decimal(str(white["sizes"][0]["price"])) == expected,
          f'{white["sizes"][0]["price"]} vs {expected}')
    check("the card price is the cheapest published price",
          Decimal(str(product["price"])) == expected, str(product["price"]))
    check("no rejected size leaked a price into the product",
          all(Decimal(str(size["price"])) > 0
              for variant in product["variants"] for size in variant["sizes"]))
    check("0.35 kg is fast shipping", product["needs_shipment"] is True)
    check("the colour names reach KDX in Arabic",
          [variant["ar"] for variant in product["variants"]] == ["أبيض", "أسود"],
          str([variant["ar"] for variant in product["variants"]]))

    print("3. a heavy product priced by undercutting a real match")
    boiler = build().run_offer(BOILER)
    check("it was read and priced", len(boiler.results) == 1)
    # The recorded image search matches this one on Noon, so it publishes by
    # undercut rather than being held back for being heavy.
    check("it published against the Noon match", boiler.published == 1,
          boiler.results[0].audit.reason_ar)
    check("priced by undercutting, not by margin",
          "Noon" in boiler.results[0].audit.pricing_basis,
          boiler.results[0].audit.pricing_basis)
    check("12.4 kg makes it free shipping", boiler.product["needs_shipment"] is False)
    check("a colour-less product still gets exactly one variant",
          len(boiler.product["variants"]) == 1)
    check("with the price on the variant itself, not on an empty size row",
          boiler.product["variants"][0]["sizes"] == []
          and boiler.product["variants"][0]["price"] > 0,
          str(boiler.product["variants"][0]))

    print("4. a banned product is stopped before it costs anything else")
    banned_dir = os.path.join(WORK, "offers")
    os.makedirs(banned_dir, exist_ok=True)
    with open(os.path.join(HERE, "samples", "offers", f"{TSHIRT}.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["result"]["productInfo"]["subject"] = "仿真枪 玩具枪 金属模型"
    payload["result"]["productInfo"]["productID"] = 999000111
    with open(os.path.join(banned_dir, "999000111.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    banned = build(offers=banned_dir).run_offer("999000111")
    check("nothing is published", banned.product is None and banned.published == 0)
    check("and every variant says why", all(
        result.audit.reason_code == "banned_category" for result in banned.results),
        str({result.audit.reason_code for result in banned.results}))

    print("5. the mains rule as the client revised it: 220V required, frequency optional")

    def boiler_with(attributes: list, offer_id: int, folder: str):
        """The boiler fixture with its electrical attributes replaced."""
        directory = os.path.join(WORK, folder)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(HERE, "samples", "offers", f"{BOILER}.json"),
                  encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["result"]["productInfo"]["productAttribute"] = attributes
        payload["result"]["productInfo"]["productID"] = offer_id
        with open(os.path.join(directory, f"{offer_id}.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return build(offers=directory, state=f"{folder}.json").run_offer(str(offer_id))

    silent = boiler_with([{"attributeName": "电压", "attributeValue": "220V"}],
                         888000222, "quiet")
    check("220V with no frequency stated is now published",
          silent.published == 1 and silent.product is not None,
          silent.results[0].audit.reason_ar if silent.results else "no results")

    # Controls. Without these, "it published" would only prove the filter was
    # switched off, not that it was narrowed.
    wrong_hz = boiler_with([{"attributeName": "电压", "attributeValue": "220V"},
                            {"attributeName": "频率", "attributeValue": "400Hz"}],
                           888000333, "wronghz")
    check("CONTROL: a frequency that IS stated and unusable is still rejected",
          wrong_hz.published == 0
          and all(r.audit.reason_code == "mains_spec" for r in wrong_hz.results),
          str({r.audit.reason_code for r in wrong_hz.results}))

    wrong_volts = boiler_with([{"attributeName": "电压", "attributeValue": "110V"},
                               {"attributeName": "频率", "attributeValue": "60Hz"}],
                              888000444, "wrongv")
    check("CONTROL: 110V is rejected however good the frequency is",
          wrong_volts.published == 0
          and all(r.audit.reason_code == "mains_spec" for r in wrong_volts.results),
          str({r.audit.reason_code for r in wrong_volts.results}))

    dual = boiler_with([{"attributeName": "电压", "attributeValue": "110V/220V"},
                        {"attributeName": "频率", "attributeValue": "50/60Hz"}],
                       888000555, "dual")
    check("a dual-voltage product counts as 220V", dual.published == 1,
          dual.results[0].audit.reason_ar if dual.results else "no results")

    # A charger almost never prints "220V"; it prints the range it accepts.
    spread = boiler_with([{"attributeName": "电压", "attributeValue": "AC100-240V"},
                          {"attributeName": "频率", "attributeValue": "50/60Hz"}],
                         888000666, "range")
    check("a 100-240V range covers Saudi mains and is accepted",
          spread.published == 1,
          spread.results[0].audit.reason_ar if spread.results else "no results")

    narrow = boiler_with([{"attributeName": "电压", "attributeValue": "100-127V"},
                          {"attributeName": "频率", "attributeValue": "60Hz"}],
                         888000777, "narrow")
    check("CONTROL: a range that stops below 220 is still rejected",
          narrow.published == 0
          and all(r.audit.reason_code == "mains_spec" for r in narrow.results),
          str({r.audit.reason_code for r in narrow.results}))

    print("6. skipping translation must not silently change the price")
    untranslated = build(translate=False, state="untranslated.json").run_offer(BOILER)
    check("the outcome says the comparison did not run",
          untranslated.compared is False,
          "a Chinese title cannot match an English one, so the search is skipped openly")
    check("and the heavy product is held back rather than published unpriced",
          untranslated.results[0].decision == rules.Decision.REJECT,
          str(untranslated.results[0].decision))
    # The reason is "nobody looked", not "nothing was found". The engine's own
    # heavy_and_unmatched reason reads, in Arabic, that the product was not
    # found on any comparison platform - true after a search, a false statement
    # before one, and the client reads this file to find out why his catalogue
    # is short.
    check("with a reason that does not claim a search happened",
          untranslated.results[0].audit.reason_code == "not_compared",
          untranslated.results[0].audit.reason_code)
    check("CONTROL: the same offer, searched, is rejected for the honest reason instead",
          all(result.audit.reason_code != "not_compared" for result in boiler.results))
    check("while the translated run priced the same offer from Noon",
          boiler.compared is True and "Noon" in boiler.results[0].audit.pricing_basis,
          "the two runs must differ, otherwise this check proves nothing")

    print("7. the budget stops a run instead of failing every remaining offer")
    outcomes = build(daily_points=1, state="tiny.json").run([TSHIRT, BOILER])
    check("the first offer runs on the one point available",
          outcomes[0].points_spent == 1 and outcomes[0].product is not None)
    check("the second is stopped rather than attempted", len(outcomes) == 2)
    check("and the reason is the budget, not an API error",
          outcomes[-1].error == "daily point budget exhausted", outcomes[-1].error)

    print("8. the department KDX files the product under comes from the tree")
    category = outcome.product["category"]
    check("the main department is the root of the branch",
          category["main_category"] and category["main_category"][0]["name_ar"] == "ملابس نسائية",
          str(category["main_category"]))
    check("the sub department is the category the offer actually names",
          category["sub_category"] and category["sub_category"][0]["id"] == 1031910,
          str(category["sub_category"]))
    no_tree = build(categories=None, state="notree.json").run_offer(TSHIRT)
    check("CONTROL: with no tree loaded the block is empty, not invented",
          no_tree.product["category"]["main_category"] == []
          and no_tree.published == 3,
          str(no_tree.product["category"]))

    print("8b. a category the client excluded is refused before anything is spent")
    banned_category_dir = os.path.join(WORK, "bannedcat")
    os.makedirs(banned_category_dir, exist_ok=True)
    with open(os.path.join(HERE, "samples", "offers", f"{TSHIRT}.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    # An innocent title in an excluded department: the title filter cannot see
    # this one, only the tree can.
    payload["result"]["productInfo"]["categoryID"] = 130823000
    payload["result"]["productInfo"]["productID"] = 777000111
    with open(os.path.join(banned_category_dir, "777000111.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    by_category = build(offers=banned_category_dir, state="bannedcat.json").run_offer("777000111")
    check("nothing is published from a blocked department",
          by_category.product is None and by_category.published == 0)
    check("and the reason names the department, not a word in the title",
          all(r.audit.reason_code == "banned_category" for r in by_category.results)
          and "منتجات للبالغين" in by_category.results[0].audit.reason_ar,
          by_category.results[0].audit.reason_ar if by_category.results else "no results")
    check("CONTROL: the same product in an allowed department publishes",
          build(offers=banned_category_dir, state="bannedcat2.json",
                categories=catalog.CategoryIndex(
                    [dict(row, state="allowed") if row["id"] == 130823000 else row
                     for row in CATEGORY_ROWS])).run_offer("777000111").published == 3,
          "otherwise this only proves the fixture is broken")

    print("9. the audit file accounts for every variant")
    with open(os.path.join(WORK, "audit.csv"), encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    check("there is a line per variant, published or not", len(rows) >= 4, str(len(rows)))
    check("rejections carry an Arabic reason",
          all(row["reason_ar"] for row in rows if row["decision"] != "publish"))
    # The bug this catches: budget.spend() answers with the points REMAINING,
    # so writing its return value here made every row claim ~299 points for a
    # one-point read. Asserting a total would need updating every time a check
    # above reads another offer; asserting the shape does not.
    charges = [int(row["points_spent"] or 0) for row in rows]
    check("no row was ever charged more than the cost of one read",
          set(charges) <= {0, 1}, str(sorted(set(charges))))
    shirt = [row for row in rows if row["offer_id"] == TSHIRT]
    check("the read cost is charged once per read, not once per variant",
          0 < sum(int(row["points_spent"] or 0) for row in shirt) < len(shirt),
          f"{sum(int(r['points_spent'] or 0) for r in shirt)} points over {len(shirt)} rows")

    print("the nightly path prices a product without asking for it back")
    # The channel we hold has no lookup, and a product taken from the discovery
    # ledger was never in this process's memory. Before run_product existed the
    # second nightly run skipped every one of its products with "was not
    # returned by a LinkPlus search" - so this is a regression check, not a
    # hypothetical.
    runner = build()
    normalised = runner.source.get_product(TSHIRT)

    class NoLookup:
        """A source that behaves the way the live channel does."""

        def get_product(self, offer_id):
            raise source.SourceError(
                f"offer {offer_id} was not returned by a LinkPlus search")

    runner.source = NoLookup()
    outcomes = runner.run_products([normalised])
    check("a product handed straight over is priced", not outcomes[0].error,
          str(outcomes[0].error))
    check("and it produced the same number of published variants",
          outcomes[0].published == 3, str(outcomes[0].published))
    check("CONTROL asking the same source for it by id still fails",
          bool(runner.run_offer(TSHIRT).error))

    print("\none bad product must not cost the night")
    # A SerpApi read timed out at product ~150 of 300 on 30 August and the
    # exception left this loop: nothing was published and three hours of gateway
    # calls were spent for nothing, at midnight, with nobody awake.
    runner2 = build(state="points-resilient.json")
    good = runner2.source.get_product(TSHIRT)
    seen = []

    def explode_on_the_second(normalised):
        seen.append(normalised["offer_id"])
        if len(seen) == 2:
            raise TimeoutError("The read operation timed out")
        return pipeline_module.OfferOutcome(offer_id=normalised["offer_id"],
                                            product={}, results=[])

    runner2.run_product = explode_on_the_second
    outcomes = runner2.run_products([good, good, good])
    check("the night carries on past the product that failed",
          len(outcomes) == 3, str(len(outcomes)))
    check("the failure is recorded against the offer it belongs to, with its type",
          outcomes[1].error.startswith("TimeoutError:"), outcomes[1].error)
    check("the products after it are still priced",
          not outcomes[2].error, outcomes[2].error)
    check("CONTROL and the failed one published nothing",
          outcomes[1].product is None and outcomes[1].results == [])

    # CONTROL: running out of the monthly allowance is not a product failing.
    # Continuing past it would price every remaining product with no comparison
    # at all, so it must still stop the night.
    import searches as searches_module

    def out_of_searches(normalised):
        raise searches_module.OutOfSearches("monthly SerpApi allowance exhausted: 30000/30000")

    runner3 = build(state="points-outofsearches.json")
    runner3.run_product = out_of_searches
    stopped = runner3.run_products([good, good, good])
    check("CONTROL an exhausted monthly allowance still stops the night",
          len(stopped) == 1, str(len(stopped)))
    check("CONTROL and it says why", "allowance exhausted" in stopped[0].error,
          stopped[0].error)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

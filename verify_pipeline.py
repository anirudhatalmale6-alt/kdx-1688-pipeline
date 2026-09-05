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
import io
import json
import os
import sys
import tempfile
import urllib.error
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
import enrich  # noqa: E402
import mapping  # noqa: E402
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
          categories=catalog.CategoryIndex(CATEGORY_ROWS), term_translator=fake_terms):
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
        term_translator=term_translator,
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
    # Landed, not goods: since 5 September the cost carries the freight from
    # his rate card, and the margin band is picked from the number the freight
    # is already inside. Passing the listing is what makes this the same
    # arithmetic the pipeline just did - without it the engine deliberately
    # answers goods-only and this check would be measuring a different rule.
    engine = rules.Engine(cny_to_sar=Decimal("0.52"))
    goods_only = engine.goods_cost_sar(
        rules.Variant(sku_id="x", attributes={}, price_cny=Decimal("28.50"),
                      stock=1, weight_kg=Decimal("0.35")))
    cheapest = min((r for r in outcome.results if r.final_price_sar),
                   key=lambda r: r.final_price_sar)
    landed = Decimal(cheapest.audit.cost_sar)
    expected, band = rules.marked_up_price(landed)
    check("the cheapest size carries the price the rules engine calculated",
          Decimal(str(white["sizes"][0]["price"])) == expected,
          f'{white["sizes"][0]["price"]} vs {expected}')
    check("and that price is the LANDED cost plus his band, not the goods cost",
          landed > goods_only and expected == rules.money(landed * (1 + band)),
          f"goods {goods_only} landed {landed} band {band}")
    check("CONTROL the freight inside it is the shirt's own, recorded on the row",
          Decimal(cheapest.audit.freight_sar) == landed - goods_only,
          f"{cheapest.audit.freight_sar} vs {landed - goods_only}")
    check("the card price is the cheapest published price",
          Decimal(str(product["price"])) == expected, str(product["price"]))
    check("no rejected size leaked a price into the product",
          all(Decimal(str(size["price"])) > 0
              for variant in product["variants"] for size in variant["sizes"]))
    check("0.35 kg is fast shipping", product["needs_shipment"] is True)
    # The product had no weight of its own until 2 September - the number lived
    # only inside variants[].sizes[], and a product with no size axis carried it
    # nowhere at all. His shop prices fast delivery from a product weight, so
    # those checked out with no delivery charge and the words "free shipping"
    # beside a fast delivery he pays for. The two must agree: a flag saying
    # "light" beside a weight over the line would charge the wrong fee.
    check("the product carries its own weight",
          product.get(mapping.WEIGHT_FIELD) == 0.35, str(product.get(mapping.WEIGHT_FIELD)))
    check("and it is the number the shipping flag was decided from",
          mapping.needs_shipment(product[mapping.WEIGHT_FIELD])
          is product["needs_shipment"])
    check("the colour names reach KDX in Arabic",
          [variant["ar"] for variant in product["variants"]] == ["أبيض", "أسود"],
          str([variant["ar"] for variant in product["variants"]]))

    print("3. a heavy product priced by undercutting a real match")
    boiler = build().run_offer(BOILER)
    check("it was read and priced", len(boiler.results) == 1)
    # The recorded image search matches this one on Noon at 689 and - since the
    # 3 September rank change - on AliExpress at 520 as well, so it publishes by
    # undercutting the cheaper of the two rather than being held back for being
    # heavy.
    check("it published against the rival match", boiler.published == 1,
          boiler.results[0].audit.reason_ar)
    check("priced by undercutting, not by margin",
          "ناقص" in boiler.results[0].audit.pricing_basis,
          boiler.results[0].audit.pricing_basis)
    check("and against the CHEAPER of the two rivals the picture found",
          "AliExpress" in boiler.results[0].audit.pricing_basis,
          boiler.results[0].audit.pricing_basis)
    check("12.4 kg makes it free shipping", boiler.product["needs_shipment"] is False)
    # CONTROL for the weight above: a product with no size axis is exactly the
    # shape that carried no weight anywhere before, so it is the one to ask.
    check("CONTROL a product with no size axis still carries a weight",
          boiler.product.get(mapping.WEIGHT_FIELD) == 12.4,
          str(boiler.product.get(mapping.WEIGHT_FIELD)))
    check("a colour-less product still gets exactly one variant",
          len(boiler.product["variants"]) == 1)
    check("with the price on the variant itself, not on an empty size row",
          boiler.product["variants"][0]["sizes"] == []
          and boiler.product["variants"][0]["price"] > 0,
          str(boiler.product["variants"][0]))

    print("3c. when every rival sells below our cost")
    # His rule of 3 September, and it splits on the shipping type:
    #
    #   "اذا تمت المقارنة في 5 التطبيقات وكلها تبيع المنتج بخسارة فيتطبق هامش
    #    الربح ... هذا معتمد في المنتجات الصغيرة التي تحتوي على الشحن السريع"
    #
    # This has to be driven with a made-up rival, and that is the whole reason
    # the section exists. Across 6,354 real audit rows the old branch fired
    # ZERO times, so "it never happens" was indistinguishable from "the code is
    # unreachable". These are the positive controls that tell them apart.
    engine = rules.Engine(cny_to_sar=Decimal("0.52"))
    light = rules.Variant(sku_id="sku-light", attributes={}, price_cny=Decimal("400"),
                          stock=9, weight_kg=Decimal("1"))
    heavy = rules.Variant(sku_id="sku-heavy", attributes={}, price_cny=Decimal("400"),
                          stock=9, weight_kg=Decimal("9"))
    cheap_rival = [compare.CompetitorHit(platform="Noon", price_sar=Decimal("120.00"),
                                         match_score=Decimal("100"), url="u",
                                         matched_variant="")]
    cost = engine.landed_cost_sar(light)
    check("CONTROL the fixture really is a loss: the rival undercuts our cost",
          rules.undercut_price(Decimal("120.00"))[0] <= cost,
          f"rival 120.00 -> {rules.undercut_price(Decimal('120.00'))[0]}, cost {cost}")

    product = rules.Product(offer_id="loss-1", title_zh="x", description_zh="",
                            images=["i"], variants=[light])
    light_out = engine.evaluate(product, {"sku-light": cheap_rival})[0]
    check("a LIGHT product falls through to the margin instead of being dropped",
          light_out.audit.reason_code == "margin_rivals_below_cost",
          light_out.audit.reason_code)
    check("and it is priced from our own cost, above it",
          light_out.final_price_sar > cost,
          f"{light_out.final_price_sar} vs cost {cost}")
    check("the rival stays on the row as the evidence for why",
          light_out.audit.competitor_price_sar == "120.00",
          light_out.audit.competitor_price_sar)

    heavy_product = rules.Product(offer_id="loss-2", title_zh="x", description_zh="",
                                  images=["i"], variants=[heavy])
    heavy_out = engine.evaluate(heavy_product, {"sku-heavy": cheap_rival})[0]
    check("a HEAVY one still stops, because that is where the shipping risk is",
          heavy_out.audit.reason_code == "would_sell_at_loss",
          heavy_out.audit.reason_code)

    # CONTROL the other way: a rival ABOVE our cost must still be undercut
    # normally, or the branch above has swallowed the ordinary case.
    rich_rival = [compare.CompetitorHit(platform="Noon", price_sar=Decimal("900.00"),
                                        match_score=Decimal("100"), url="u",
                                        matched_variant="")]
    normal = engine.evaluate(product, {"sku-light": rich_rival})[0]
    check("CONTROL a rival above our cost is still undercut, not margined",
          normal.audit.reason_code == "matched"
          and "ناقص" in normal.audit.pricing_basis,
          f"{normal.audit.reason_code} / {normal.audit.pricing_basis}")

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
    # Two layers, and the order matters. Since 3 September the completeness gate
    # refuses an untranslated listing outright, so the first pair below waives
    # that gate by name to reach the layer underneath - the one that decides
    # what an untranslated product would be priced at IF it were published. Both
    # still have to hold: the gate can be waived, and when it is, the pricing
    # underneath must still not pretend a search happened.
    os.environ["KDX_COMPLETENESS_SKIP"] = "untranslated"
    untranslated = build(translate=False, state="untranslated.json").run_offer(BOILER)
    check("the outcome says the comparison did not run",
          untranslated.compared is False,
          "a Chinese title cannot match an English one, so the search is skipped openly")
    # Until 5 September this was a refusal. The gate he opened that day means a
    # heavy unmatched product is published at cost plus freight plus margin -
    # so what has to hold now is not that it was held back, but that its price
    # is a real one and the row does not claim a search stood behind it.
    check("the heavy product is published, at a price above its landed cost",
          untranslated.results[0].decision == rules.Decision.PUBLISH
          and untranslated.results[0].final_price_sar
          > Decimal(untranslated.results[0].audit.cost_sar),
          f"{untranslated.results[0].decision} "
          f"{untranslated.results[0].audit.final_price_sar}")
    # The reason is "nobody looked", not "nothing was found" - true after a
    # search, a false statement before one, and the client reads this file to
    # find out how his prices were set.
    check("with a reason that does not claim a search happened",
          untranslated.results[0].audit.reason_code == "not_compared",
          untranslated.results[0].audit.reason_code)
    del os.environ["KDX_COMPLETENESS_SKIP"]
    gated = build(translate=False, state="untranslated-gated.json").run_offer(BOILER)
    check("CONTROL and with the gate in force it never gets that far",
          all(r.audit.reason_code == "untranslated" for r in gated.results)
          and gated.compared is False,
          str({r.audit.reason_code for r in gated.results}))
    check("CONTROL: the same offer, searched, is rejected for the honest reason instead",
          all(result.audit.reason_code != "not_compared" for result in boiler.results))
    check("while the translated run priced the same offer from a rival",
          boiler.compared is True
          and "ناقص" in boiler.results[0].audit.pricing_basis,
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
    # Since 3 September there are TWO ways this product can be refused on its
    # department: the tree state, and the client's own sell:false list. 成人用品
    # is in both. The control has to release both, or it stops proving the
    # fixture works and starts proving only that the second rule fires.
    original_off = catalog.departments_off
    try:
        catalog.departments_off = lambda path="": {}
        check("CONTROL: the same product in an allowed department publishes",
              build(offers=banned_category_dir, state="bannedcat2.json",
                    categories=catalog.CategoryIndex(
                        [dict(row, state="allowed") if row["id"] == 130823000 else row
                         for row in CATEGORY_ROWS])).run_offer("777000111").published == 3,
              "otherwise this only proves the fixture is broken")
    finally:
        catalog.departments_off = original_off

    check("CONTROL: and with the tree allowing it, his own switched-off list "
          "still refuses it - the second gate is real",
          all(r.audit.reason_code == "department_off" for r in
              build(offers=banned_category_dir, state="bannedcat3.json",
                    categories=catalog.CategoryIndex(
                        [dict(row, state="allowed") if row["id"] == 130823000 else row
                         for row in CATEGORY_ROWS])).run_offer("777000111").results))

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

    print("\na fraction of a riyal is a real price and still not a product")
    # A glass decorative stone went live at 0.08 SAR on the first real night.
    # 1688 quotes wholesale per piece, so the arithmetic was right and the
    # listing was still nonsense - the payment fee exceeds the sale.
    import importlib

    def engine_with_floor(floor: str):
        os.environ["KDX_MIN_PRICE_SAR"] = floor
        importlib.reload(rules)
        return rules.Engine(cny_to_sar=Decimal("0.52"))

    def one_variant(price_cny: str):
        return rules.Product(
            offer_id="9001", title_zh="装饰玻璃石", description_zh="",
            images=["https://cbu01.alicdn.com/img/ibank/stone.jpg"],
            variants=[rules.Variant(sku_id="9001-default", attributes={},
                                    price_cny=Decimal(price_cny), stock=9,
                                    weight_kg=Decimal("0.2"))])

    # 5 September. The stone is no longer priced at 0.08 SAR, and not because
    # of the floor: with his rate card inside the cost it now carries 6.11 SAR
    # of its own carriage, and is published at what it actually costs to bring
    # in. The floor stopped being the thing that catches this - which is worth
    # a check of its own, because a guard that can no longer fire is a guard
    # nobody should be relying on.
    engine = engine_with_floor("3")
    cheap = engine.evaluate(one_variant("0.12"), {})[0]
    check("a penny product is now published at the cost of shipping it, not "
          "refused for being cheap",
          cheap.decision == rules.Decision.PUBLISH, str(cheap.audit.reason_code))
    check("and its price covers its freight with the margin on top",
          cheap.final_price_sar > Decimal(cheap.audit.freight_sar),
          f"{cheap.final_price_sar} vs {cheap.audit.freight_sar}")
    check("CONTROL the goods were genuinely worth pennies - it is the freight "
          "that lifted it, not a repriced product",
          Decimal(cheap.audit.cost_sar) - Decimal(cheap.audit.freight_sar)
          < Decimal("0.10"),
          f"{cheap.audit.cost_sar} - {cheap.audit.freight_sar}")

    dearer = engine.evaluate(one_variant("12.00"), {})[0]
    check("CONTROL an ordinary product is unaffected",
          dearer.decision == rules.Decision.PUBLISH, str(dearer.audit.reason_code))

    # CONTROL: the floor is still his number and still in force - it simply
    # sits far below anything a real freight charge can produce.
    high = engine_with_floor("500")
    refused = high.evaluate(one_variant("12.00"), {})[0]
    check("CONTROL raising the floor rejects more, so the number is really in "
          "force", refused.audit.reason_code == "below_min_price",
          refused.audit.reason_code)
    check("and the reason names both numbers, in Arabic",
          "الحد الأدنى" in refused.audit.reason_ar, refused.audit.reason_ar)
    off = engine_with_floor("0")
    check("CONTROL KDX_MIN_PRICE_SAR=0 publishes what the arithmetic produces",
          off.evaluate(one_variant("0.12"), {})[0].decision == rules.Decision.PUBLISH)

    # He answered on 2026-08-30: "اجعلها الحد الادنى 0.01". The floor stops a
    # zero and nothing else, which is what he asked for - a 0.08 SAR stone is
    # published again, deliberately, and he was told so.
    os.environ.pop("KDX_MIN_PRICE_SAR", None)
    importlib.reload(rules)
    check("the floor with nothing configured is the client's 0.01",
          rules.MIN_PRICE_SAR == Decimal("0.01"), str(rules.MIN_PRICE_SAR))
    his = rules.Engine(cny_to_sar=Decimal("0.52"))
    check("at his floor the 0.08 SAR product publishes again",
          his.evaluate(one_variant("0.12"), {})[0].decision == rules.Decision.PUBLISH)
    # Refused for a better reason than it used to be. Before 5 September a zero
    # priced itself at zero and the floor caught it; now that freight is part
    # of the cost, a zero-priced listing has a real cost - its own carriage -
    # and would have sailed over the floor. The refusal is explicit instead of
    # incidental, and the log says which of the two it was.
    check("CONTROL and a price of exactly zero is still refused",
          his.evaluate(one_variant("0"), {})[0].audit.reason_code == "no_price",
          str(his.evaluate(one_variant("0"), {})[0].audit.reason_code))
    check("CONTROL and it is refused for having no price, not for being cheap - "
          "so freight can never turn a broken listing into a sellable one",
          "لا يوجد سعر" in his.evaluate(one_variant("0"), {})[0].audit.reason_ar)

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

    print("\na product his shop cannot show is not a published product")
    # On 30 August twenty-one products reached his shop with empty picture
    # frames and the run reported twenty-one published, because the photographs
    # were never checked and the shop's answer was thrown away.
    import photos as photos_module

    class FakeKdx:
        def __init__(self, answer):
            self.answer = answer
            self.pushed = []

        def push(self, products, batch_size=20):
            self.pushed.extend(products)
            return [self.answer]

    class Opener:
        def __init__(self, dead=(), bodies=None):
            self.dead = set(dead)
            self.asked = []
            # {url: bytes} for the tests that need two URLs to be one photograph.
            self.bodies = dict(bodies or {})

        def __call__(self, request, timeout=None):
            url = request.full_url
            self.asked.append(url)
            if url in self.dead:
                raise urllib.error.HTTPError(url, 404, "gone", {}, io.BytesIO(b""))

            body = self.bodies.get(url, b"\xff\xd8jpeg" + url.encode())

            class R:
                status = 200
                headers = {"Content-Type": "image/jpeg"}

                def read(self_inner):
                    # The checker keeps the bytes for the text scorer to read,
                    # and now also to tell one photograph from another. Distinct
                    # per URL unless a test deliberately says otherwise: a stub
                    # that answered the same six bytes for every URL made every
                    # photograph in the suite a duplicate of the first one.
                    return body

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False
            return R()

    IMPORTED = {"success": True, "imported_count": 1, "failed_count": 0,
                "skipped_count": 0, "failed_items": []}

    def live_runner(answer, dead=()):
        runner = build(state=f"points-live-{len(dead)}-{answer['skipped_count']}.json")
        runner.dry_run = False
        runner.kdx = FakeKdx(answer)
        runner.photos = photos_module.PhotoChecker(opener=Opener(dead))
        return runner

    def original(url: str) -> str:
        """
        The URL as the supplier serves it, before the display-size swap.

        The photographs that ship are now the CDN's smaller copies, so a test
        that wants to kill a photograph has to kill the one the checker asks
        about first - killing only the small copy leaves the original alive and
        the product keeps its picture, which is the whole point of that
        fallback.
        """
        tail = f"_{photos_module.DISPLAY_PX}x{photos_module.DISPLAY_PX}.jpg"
        return url[:-len(tail)] if tail and url.endswith(tail) else url

    healthy = live_runner(IMPORTED)
    outcome = healthy.run_product(healthy.source.get_product(TSHIRT))
    published = list(outcome.product["images"])
    all_photos = [original(url) for url in published]
    check("with live photographs the product is pushed",
          len(healthy.kdx.pushed) == 1 and not outcome.error, outcome.error)
    # Not a count - counts drift whenever a step asks about one more URL. What
    # has to hold is that every photograph his shop is told to copy is one this
    # run fetched and got an image back from.
    check("and every photograph that ships was fetched, not assumed",
          all(healthy.photos.seen.get(url) is True for url in published),
          str({url: healthy.photos.seen.get(url) for url in published}))
    check("CONTROL the full-size originals were fetched too - they are what "
          "the poster filter reads",
          all(healthy.photos.seen.get(url) is True for url in all_photos),
          str({url: healthy.photos.seen.get(url) for url in all_photos}))

    partial = live_runner(IMPORTED, dead=all_photos[1:])
    outcome = partial.run_product(partial.source.get_product(TSHIRT))
    check("a dead photograph is dropped and the rest still go",
          [original(url) for url in outcome.product["images"]] == all_photos[:1],
          str(outcome.product["images"]))
    check("and the run says which one it dropped",
          outcome.photos["dropped"] == all_photos[1:], str(outcome.photos))

    blind = live_runner(IMPORTED, dead=all_photos)
    outcome = blind.run_product(blind.source.get_product(TSHIRT))
    check("a product with no reachable photograph is never pushed",
          blind.kdx.pushed == [], str(blind.kdx.pushed))
    check("it is reported as an error, not as a publication",
          "no reachable photograph" in outcome.error and outcome.product is None,
          outcome.error)

    SKIPPED = dict(IMPORTED, imported_count=0, skipped_count=1,
                   message="declined by the shop")
    refused = live_runner(SKIPPED)
    outcome = refused.run_product(refused.source.get_product(TSHIRT))
    check("an offer his shop silently skipped is not counted as published",
          "declined by the shop" in outcome.error, outcome.error)
    check("and the answer it gave is kept for the report",
          outcome.kdx_response == SKIPPED, str(outcome.kdx_response))

    # Since 30 August the same route upserts, so a product his shop already
    # holds comes back as an update. That is a landing, and it has to be counted
    # as one - otherwise every price refresh would report itself as a failure.
    UPDATED = dict(IMPORTED, imported_count=0, updated_count=1)
    again = live_runner(UPDATED)
    outcome = again.run_product(again.source.get_product(TSHIRT))
    check("an update counts as published", outcome.published >= 1 and not outcome.error,
          outcome.error)
    check("and it is distinguishable from an insert",
          pipeline_module.was_update(outcome.kdx_response), str(outcome.kdx_response))

    print("\na product whose only photograph is an advertising poster")
    # The client's complaint of 30-31 August, and the only lever left while the
    # detail permission is refused: with one photograph per product there is no
    # gallery to re-order, so the choice is publish the poster or hold the
    # product. These go through run_product rather than calling the scorer
    # directly - a check that calls the step itself would pass even if the
    # pipeline never reached it.
    import imagetext as imagetext_module

    def with_score(score, limit):
        original_percent = imagetext_module.text_percent
        original_limit = imagetext_module.MAX_TEXT_PERCENT
        imagetext_module.text_percent = lambda data: score
        imagetext_module.MAX_TEXT_PERCENT = limit
        runner = live_runner(IMPORTED)
        try:
            return runner, runner.run_product(runner.source.get_product(TSHIRT))
        finally:
            imagetext_module.text_percent = original_percent
            imagetext_module.MAX_TEXT_PERCENT = original_limit

    poster, outcome = with_score(6.11, 2.0)
    check("a poster-only product is never pushed to his shop",
          poster.kdx.pushed == [], str(poster.kdx.pushed))
    check("it is held with the measurement in the reason",
          outcome.product is None and "6.11%" in (outcome.error or ""), outcome.error)

    clean, outcome = with_score(0.4, 2.0)
    check("CONTROL a clean photograph under the same threshold still publishes",
          len(clean.kdx.pushed) == 1 and not outcome.error, outcome.error)

    unmeasurable, outcome = with_score(None, 2.0)
    check("CONTROL a photograph tesseract could not read is published, not held",
          len(unmeasurable.kdx.pushed) == 1 and not outcome.error, outcome.error)

    off, outcome = with_score(40.0, 0.0)
    check("CONTROL with no threshold set the poster is published as before",
          len(off.kdx.pushed) == 1 and not outcome.error, outcome.error)

    print("\nthe poster filter reaches the colour swatches, not just the gallery")
    # The defect the client photographed in his own shop on 2 September. The
    # gallery was filtered and the variant blocks were not, so a photograph
    # judged an advertising poster was dropped from `images` and published
    # anyway one level down, inside the colour it belonged to. Measured on the
    # live catalogue before this ran: 170 such photographs across 48 of 230
    # products, scoring 8.9%-20.2% against a 5% limit, while the photographs
    # that survived into the same galleries scored 0.1%-0.4%.
    def with_scores(by_url, limit):
        original_percent = imagetext_module.text_percent
        original_limit = imagetext_module.MAX_TEXT_PERCENT
        # The opener's body carries its own URL, which is how a per-photograph
        # score is expressed without reaching into the checker.
        def scorer(data):
            url = bytes(data).decode("utf-8", "ignore")
            return next((v for k, v in by_url.items() if k in url), 0.1)
        imagetext_module.text_percent = scorer
        imagetext_module.MAX_TEXT_PERCENT = limit
        runner = live_runner(IMPORTED)
        try:
            return runner, runner.run_product(runner.source.get_product(TSHIRT))
        finally:
            imagetext_module.text_percent = original_percent
            imagetext_module.MAX_TEXT_PERCENT = original_limit

    # Through run_product, because a check that calls the step itself would pass
    # even if the pipeline never reached it.
    mixed, outcome = with_scores({"main-2.jpg": 20.21}, 5.0)
    pushed = mixed.kdx.pushed[0] if mixed.kdx.pushed else {}
    everywhere = list(pushed.get("images") or [])
    for variant in pushed.get("variants") or []:
        everywhere.extend(variant.get("images") or [])
        if variant.get("image"):
            everywhere.append(variant["image"])
    check("a poster reaches neither the gallery nor a swatch",
          pushed and not any("main-2.jpg" in url for url in everywhere),
          str(everywhere))
    # CONTROL. If the clean photographs went too this would pass for the wrong
    # reason: a product with no pictures also has no posters in it.
    check("CONTROL the clean photographs are still there",
          any("main-1.jpg" in url for url in everywhere), str(everywhere))
    check("CONTROL no colour is left with a blank swatch",
          all(variant["image"] or not variant["images"]
              for variant in pushed.get("variants") or []),
          str(pushed.get("variants")))

    # The rule at the level where it bites, with a colour that owns more than
    # one photograph - which the fixture above does not have. A colour keeps its
    # clean shot and loses its poster.
    block = {"variants": [
        {"original": "red", "image": "a-poster.jpg",
         "images": ["a-poster.jpg", "b-clean.jpg"]},
        {"original": "blue", "image": "c-poster.jpg", "images": ["c-poster.jpg"]},
    ]}
    original_limit = imagetext_module.MAX_TEXT_PERCENT
    imagetext_module.MAX_TEXT_PERCENT = 5.0
    try:
        gone = pipeline_module._drop_posters_from_variants(
            block, {"a-poster.jpg": 20.21, "b-clean.jpg": 0.4, "c-poster.jpg": 30.0})
    finally:
        imagetext_module.MAX_TEXT_PERCENT = original_limit
    check("a colour loses the poster and keeps the photograph",
          block["variants"][0]["images"] == ["b-clean.jpg"],
          str(block["variants"][0]))
    check("and its swatch moves to the one that survived",
          block["variants"][0]["image"] == "b-clean.jpg",
          str(block["variants"][0]))
    check("the run can say which ones went", gone == ["a-poster.jpg"], str(gone))
    # CONTROL for the deliberate exception. A colour whose every photograph is a
    # poster keeps the cleanest one: a blank frame next to a live price is worse
    # for the shopper than a caption inside the picture, and it is the same
    # trade order_gallery already makes for the gallery.
    check("CONTROL a colour with nothing else keeps its poster",
          block["variants"][1]["images"] == ["c-poster.jpg"],
          str(block["variants"][1]))

    # The second, higher line. Measured on the live channel, that exception
    # swallows the rule: all 50 colours in a six-product batch carried exactly
    # one photograph, so 17 kept one over the limit - 14 of them at 5-6%, then
    # 7%, 8% and one at 12%. The client complained about pictures that are
    # advertising and nothing else, which is the 12%, not the 5.4%.
    def with_drop_line(value, variants, scores_map):
        payload = {"variants": json.loads(json.dumps(variants))}
        before = os.environ.get("KDX_POSTER_DROP_COLOUR_PCT")
        imagetext_module.MAX_TEXT_PERCENT = 5.0
        if value is None:
            os.environ.pop("KDX_POSTER_DROP_COLOUR_PCT", None)
        else:
            os.environ["KDX_POSTER_DROP_COLOUR_PCT"] = str(value)
        try:
            pipeline_module._drop_posters_from_variants(payload, scores_map)
        finally:
            imagetext_module.MAX_TEXT_PERCENT = original_limit
            if before is None:
                os.environ.pop("KDX_POSTER_DROP_COLOUR_PCT", None)
            else:
                os.environ["KDX_POSTER_DROP_COLOUR_PCT"] = before
        return payload

    THREE = [{"original": "mild", "image": "m.jpg", "images": ["m.jpg"]},
             {"original": "loud", "image": "l.jpg", "images": ["l.jpg"]},
             {"original": "clean", "image": "k.jpg", "images": ["k.jpg"]}]
    MARKS = {"m.jpg": 5.4, "l.jpg": 12.0, "k.jpg": 0.3}

    # CONTROL FIRST: unset must reproduce exactly what shipped before it existed.
    unset = with_drop_line(None, THREE, MARKS)
    check("CONTROL with no second line set, every colour survives as before",
          [v["original"] for v in unset["variants"]] == ["mild", "loud", "clean"],
          str([v["original"] for v in unset["variants"]]))

    at_ten = with_drop_line(10, THREE, MARKS)
    check("above the second line the colour is withdrawn, not published",
          [v["original"] for v in at_ten["variants"]] == ["mild", "clean"],
          str([v["original"] for v in at_ten["variants"]]))
    check("CONTROL the borderline colour is left alone",
          at_ten["variants"][0]["images"] == ["m.jpg"], str(at_ten["variants"][0]))

    # CONTROL: the last colour is never withdrawn. A product with no variants
    # has no price and no photograph; whether to hold it over its pictures is
    # poster_only's decision, one step further on.
    only = with_drop_line(10, [{"original": "loud", "image": "l.jpg",
                                "images": ["l.jpg"]}], MARKS)
    check("CONTROL the last colour standing is never withdrawn",
          [v["original"] for v in only["variants"]] == ["loud"],
          str(only["variants"]))

    # CONTROL: with no threshold set, nothing is touched at all.
    untouched = {"variants": [{"original": "red", "image": "a-poster.jpg",
                               "images": ["a-poster.jpg", "b-clean.jpg"]}]}
    imagetext_module.MAX_TEXT_PERCENT = 0.0
    try:
        pipeline_module._drop_posters_from_variants(untouched, {"a-poster.jpg": 90.0})
    finally:
        imagetext_module.MAX_TEXT_PERCENT = original_limit
    check("CONTROL with no threshold set the swatches are left alone",
          untouched["variants"][0]["images"] == ["a-poster.jpg", "b-clean.jpg"],
          str(untouched["variants"][0]))

    # CONTROL: an unmeasured photograph is not a poster. Tesseract fails on
    # plenty of real photographs, and treating silence as guilt would strip
    # galleries wholesale the first time OCR had a bad night.
    unread = {"variants": [{"original": "red", "image": "x.jpg",
                            "images": ["x.jpg", "y.jpg"]}]}
    imagetext_module.MAX_TEXT_PERCENT = 5.0
    try:
        pipeline_module._drop_posters_from_variants(unread, {"x.jpg": None, "y.jpg": 0.2})
    finally:
        imagetext_module.MAX_TEXT_PERCENT = original_limit
    check("CONTROL a photograph that could not be read is kept",
          unread["variants"][0]["images"] == ["x.jpg", "y.jpg"],
          str(unread["variants"][0]))

    # CONTROL: the guard has to be switchable off without editing code, or a
    # network that cannot reach alicdn at all would hold the entire catalogue
    # and look exactly like a pricing bug.
    unguarded = build(state="points-unguarded.json")
    unguarded.dry_run = False
    unguarded.kdx = FakeKdx(IMPORTED)
    unguarded.photos = None
    outcome = unguarded.run_product(unguarded.source.get_product(TSHIRT))
    check("CONTROL with the check off the same product still publishes",
          len(unguarded.kdx.pushed) == 1 and not outcome.error, outcome.error)

    print("\nan option name that will not translate never reaches the cart")
    # 2 September. The client opened his own shopping cart and sent a photograph
    # of it: a pushchair whose single option read
    # "M005-单向推车-黑色-标配款-单手折叠（可坐可趟）". Measured across everything
    # published: 238 of 1415 options carried Chinese in the Arabic field.
    #
    # The cause was the fallback in to_kdx_variants - keep the original when the
    # translator gave nothing back - which is the right failure and the wrong
    # thing to publish. Reproduced by handing the pipeline a translator that
    # returns one label unchanged, which is exactly what the model did.
    def stubborn(terms, **_kwargs):
        return {term: ({"en": term, "ar": term} if term == "黑色"
                       else TERMS.get(term, {"en": term, "ar": term}))
                for term in terms}

    outcome = build(state="points-untranslated.json",
                    term_translator=stubborn).run_offer(TSHIRT)
    published = {result.variant.attributes.get("color")
                 for result in outcome.results
                 if result.decision is rules.Decision.PUBLISH}
    check("the option whose name stayed Chinese is not published",
          "黑色" not in published, str(published))
    check("and the reason names it rather than the price",
          any(result.audit.reason_code == "untranslated_option"
              and "黑色" in result.audit.reason_ar for result in outcome.results),
          str(sorted({result.audit.reason_code for result in outcome.results})))
    check("no Chinese option name reaches the payload",
          not any(enrich.has_cjk(variant["ar"])
                  for variant in (outcome.product or {}).get("variants", [])),
          str([variant["ar"] for variant in (outcome.product or {}).get("variants", [])]))
    # CONTROL. Holding the whole product would cost the client the other colour
    # for a fault that belongs to one of them, so the refusal has to be per
    # option - and the white shirt is what proves the guard did not simply
    # reject everything it touched.
    check("CONTROL the colour that did translate still publishes",
          "白色" in published, str(published))
    check("CONTROL with both of its sizes",
          len((outcome.product or {}).get("variants", [{}])[0].get("sizes", [])) == 2,
          str((outcome.product or {}).get("variants")))

    # CONTROL. Running with no translator at all used to publish the Chinese
    # title. Since 3 September the completeness gate refuses it - his rule, "if
    # the product information is unclear, exclude it", and a shopper meeting
    # Chinese characters is the clearest case of unclear there is. The old
    # behaviour is still reachable by name, and this pair proves the gate is
    # what changed it rather than something else in the run.
    os.environ["KDX_COMPLETENESS_SKIP"] = "untranslated"
    untranslated = build(state="points-notranslate.json", translate=False,
                         term_translator=stubborn).run_offer(TSHIRT)
    check("CONTROL with the gate waived, translation off behaves as it always did",
          len((untranslated.product or {}).get("variants", [])) == 2,
          str((untranslated.product or {}).get("variants")))
    del os.environ["KDX_COMPLETENESS_SKIP"]
    refused = build(state="points-notranslate2.json", translate=False,
                    term_translator=stubborn).run_offer(TSHIRT)
    check("and with the gate in force the same offer is not published at all",
          refused.product is None
          and all(r.audit.reason_code == "untranslated" for r in refused.results),
          str({r.audit.reason_code for r in refused.results}))

    print("\nthe label the model hands straight back is cut into its pieces")
    # The second half of the same fix. Asked for the whole SKU string the model
    # returns it unchanged - twice, because the retry asks the same way - so the
    # third pass asks for the segments instead. This stub behaves exactly as the
    # real model did on 2 September: mute on the compound label, fluent on its
    # parts.
    SEGMENTS = {"单向推车": "عربة دفع باتجاه واحد", "黑色": "أسود",
                "标配款": "النسخة القياسية", "单手折叠（可坐可趟）": "طي بيد واحدة"}
    WHOLE = "M005-单向推车-黑色-标配款-单手折叠（可坐可趟）"
    asked = []

    def fake_chat(_system, user, _api_key, _timeout):
        wanted = json.loads(user)
        asked.append(list(wanted))
        answers = {}
        for term in wanted:
            arabic = SEGMENTS.get(term)
            # The whole label comes back exactly as it went in. That is the
            # defect, reproduced rather than described.
            answers[term] = ({"en": term, "ar": arabic or term}
                             if arabic else {"en": term, "ar": term})
        return {"terms": answers}

    original_chat = enrich._chat
    enrich._chat = fake_chat
    try:
        result = enrich.translate_terms([WHOLE], api_key="test")
    finally:
        enrich._chat = original_chat

    check("the label comes back with no Chinese left in it",
          not enrich.has_cjk(result[WHOLE]["ar"]), result[WHOLE]["ar"])
    check("and keeps its shape, model code and dashes included",
          result[WHOLE]["ar"] == "M005-عربة دفع باتجاه واحد-أسود-"
                                 "النسخة القياسية-طي بيد واحدة",
          result[WHOLE]["ar"])
    # CONTROL on the mechanism, not the output: a passing string could also be
    # produced by asking for the whole label a third time and getting lucky.
    check("CONTROL the third call asks for the segments, not the label",
          len(asked) == 3 and WHOLE not in asked[2] and "黑色" in asked[2],
          str(asked))
    check("CONTROL and only for the Chinese ones - M005 is not sent to be named",
          "M005" not in asked[2], str(asked[2]))

    # The separator can be a Chinese character itself. 【 and 】 live in the CJK
    # punctuation block, so a label rebuilt with them still in it is judged to be
    # Chinese and thrown away - which is exactly what happened: adding the
    # brackets to the split took the survivors of the real 234 from 59 back up to
    # 132 before this was understood.
    VALVE = "4V310-10~优质款【AC220V】"
    valve_asked = []

    def valve_chat(_system, user, _api_key, _timeout):
        wanted = json.loads(user)
        valve_asked.append(list(wanted))
        return {"terms": {term: {"en": term,
                                 "ar": "الفئة الممتازة" if term == "优质款" else term}
                          for term in wanted}}

    enrich._chat = valve_chat
    try:
        valve = enrich.translate_terms([VALVE], api_key="test")
    finally:
        enrich._chat = original_chat

    check("a bracketed label is not defeated by its own punctuation",
          not enrich.has_cjk(valve[VALVE]["ar"]), valve[VALVE]["ar"])
    check("the brackets come through as brackets, and the code untouched",
          valve[VALVE]["ar"] == "4V310-10~الفئة الممتازة[AC220V]",
          valve[VALVE]["ar"])
    check("CONTROL a bracket is never sent to be translated as a word",
          all("【" not in batch and "】" not in batch for batch in valve_asked),
          str(valve_asked))

    # A part code can run straight into the Chinese with nothing between them.
    # The first live run after the separator work held all thirty options of one
    # goalkeeper glove on this shape - "E2守门员手套蓝色" - because a piece glued
    # together like that comes back unchanged exactly as a whole label does.
    GLOVE = "E2守门员手套蓝色【不带护指】"
    glove_asked = []

    def glove_chat(_system, user, _api_key, _timeout):
        wanted = json.loads(user)
        glove_asked.append(list(wanted))
        known = {"守门员手套蓝色": "قفازات حارس مرمى زرقاء", "不带护指": "بدون واقي أصابع"}
        return {"terms": {term: {"en": term, "ar": known.get(term, term)}
                          for term in wanted}}

    enrich._chat = glove_chat
    try:
        glove = enrich.translate_terms([GLOVE], api_key="test")
    finally:
        enrich._chat = original_chat

    check("a code glued to the Chinese does not defeat the split",
          not enrich.has_cjk(glove[GLOVE]["ar"]), glove[GLOVE]["ar"])
    check("the code stays, and the seam becomes a space",
          glove[GLOVE]["ar"] == "E2 قفازات حارس مرمى زرقاء[بدون واقي أصابع]",
          glove[GLOVE]["ar"])
    check("CONTROL the code itself is never sent to be named",
          all("E2" not in batch for batch in glove_asked), str(glove_asked))

    print("\nthe seller's decoration is taken off before the label is read")
    # Measured against the live model on 2 September. The same label, with and
    # without its ornament: "✷☽☽\t5号守门员手套（身高建议130CM左右）✷☽☽" came back
    # untouched, and "5号守门员手套（身高建议130CM左右）" came back as
    # "قفازات حارس المرمى مقاس 5 (الطول الموصى به حوالي 130 سم)" on the first ask.
    # So the ornament is not cosmetic - it is the difference between a good
    # translation and no translation, and the segment fallback it forced had
    # rendered 左右 ("approximately") as "left and right".
    DIRTY = "✷☽☽\t5号守门员手套（身高建议130CM左右）✷☽☽"
    dirty_asked = []

    def dirty_chat(_system, user, _api_key, _timeout):
        wanted = json.loads(user)
        dirty_asked.append(list(wanted))
        clean = "5号守门员手套（身高建议130CM左右）"
        # Fluent on the clean label, mute on the decorated one - the model's own
        # behaviour, recorded.
        return {"terms": {term: ({"en": "Goalkeeper Gloves Size 5",
                                  "ar": "قفازات حارس المرمى مقاس 5"}
                                 if term == clean else {"en": term, "ar": term})
                          for term in wanted}}

    enrich._chat = dirty_chat
    try:
        dirty = enrich.translate_terms([DIRTY], api_key="test")
    finally:
        enrich._chat = original_chat

    check("the decorated label still translates on the first ask",
          dirty[DIRTY]["ar"] == "قفازات حارس المرمى مقاس 5", dirty[DIRTY]["ar"])
    check("CONTROL the ornament never reaches the model",
          "✷" not in "".join(sum(dirty_asked, [])), str(dirty_asked))
    check("CONTROL and one ask was enough - no fallback was needed",
          len(dirty_asked) == 1, str(dirty_asked))
    check("CONTROL the answer comes back under the key the caller handed in",
          list(dirty) == [DIRTY], str(list(dirty)))
    # A tab held two words apart; dropping it would have joined them.
    check("CONTROL a control character becomes a space, not nothing",
          enrich._declutter("红色\t大号") == "红色 大号",
          enrich._declutter("红色\t大号"))
    # And a symbol that means something in a size label is not decoration.
    check("CONTROL a temperature or degree sign survives",
          enrich._declutter("25℃±2°") == "25℃±2°", enrich._declutter("25℃±2°"))

    print("the department's weight reaches a leaf filed underneath it")
    # 1031910 (Dresses) sits under 10166 (Women's Clothing). His table is
    # written against departments, and 1688 files offers against leaves, so
    # this is the join the whole table depends on.
    tree = catalog.CategoryIndex(CATEGORY_ROWS)
    leaf_offer = {"offer_id": "1", "category_id": "1031910",
                  "weight_kg": 1.0, "weight_assumed": True}

    def weighed(table, offer=None, categories=tree):
        before = os.environ.get("KDX_CATEGORY_WEIGHTS")
        if table is None:
            os.environ.pop("KDX_CATEGORY_WEIGHTS", None)
        else:
            os.environ["KDX_CATEGORY_WEIGHTS"] = json.dumps(table)
        try:
            return pipeline_module._weigh_by_category(
                dict(offer or leaf_offer), categories)
        finally:
            if before is None:
                os.environ.pop("KDX_CATEGORY_WEIGHTS", None)
            else:
                os.environ["KDX_CATEGORY_WEIGHTS"] = before

    got = weighed({"10166": 0.4})
    check("a department number reaches the leaf below it",
          got["weight_kg"] == 0.4, str(got["weight_kg"]))
    check("and the audit names the category the number came from",
          got.get("weight_category_id") == "10166", str(got.get("weight_category_id")))
    check("a category average is still an assumption, not a measurement",
          got["weight_assumed"] is True, str(got["weight_assumed"]))

    both = weighed({"10166": 0.4, "1031910": 0.9})
    check("the leaf wins over the department above it when he gave both",
          both["weight_kg"] == 0.9, str(both["weight_kg"]))
    check("and it is the leaf that is named",
          both.get("weight_category_id") == "1031910",
          str(both.get("weight_category_id")))

    # The controls. Each one is a way this could pass for the wrong reason.
    check("CONTROL an empty table changes nothing",
          weighed(None)["weight_kg"] == 1.0)
    check("CONTROL a table that names neither the leaf nor its parents "
          "changes nothing",
          weighed({"130823000": 7.0})["weight_kg"] == 1.0)
    measured = weighed({"10166": 0.4},
                       offer={"offer_id": "2", "category_id": "1031910",
                              "weight_kg": 3.3, "weight_assumed": False})
    check("CONTROL a weight the source actually measured is never overwritten",
          measured["weight_kg"] == 3.3, str(measured["weight_kg"]))
    check("CONTROL and no category is credited for it",
          "weight_category_id" not in measured)
    check("CONTROL with no category tree at all the weight stands",
          weighed({"10166": 0.4}, categories=None)["weight_kg"] == 1.0)
    unknown = weighed({"10166": 0.4},
                      offer={"offer_id": "3", "category_id": "999999",
                             "weight_kg": 1.0, "weight_assumed": True})
    check("CONTROL a leaf whose ancestry is unknown keeps the assumed weight",
          unknown["weight_kg"] == 1.0, str(unknown["weight_kg"]))

    # The ancestry itself, since everything above rests on it.
    chain = tree.chain("1031910")
    check("the chain is root first, leaf last",
          [str(row["id"]) for row in chain] == ["10166", "1031910"],
          str([row["id"] for row in chain]))
    check("CONTROL an id the tree has never seen has no chain",
          tree.chain("999999") == [])
    check("CONTROL and neither does a blank one", tree.chain("") == [])

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

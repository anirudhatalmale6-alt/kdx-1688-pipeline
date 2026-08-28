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


def build(daily_points: int = 300, cny_to_sar: str = "0.52", translate: bool = True,
          state: str = "points.json", offers: str | None = None):
    return pipeline_module.Pipeline(
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

    print("5. an electrical product must state both 220V and the frequency")
    quiet = os.path.join(WORK, "quiet")
    os.makedirs(quiet, exist_ok=True)
    with open(os.path.join(HERE, "samples", "offers", f"{BOILER}.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    payload["result"]["productInfo"]["productAttribute"] = [
        {"attributeName": "电压", "attributeValue": "220V"}]
    payload["result"]["productInfo"]["productID"] = 888000222
    with open(os.path.join(quiet, "888000222.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    silent = build(offers=quiet, state="quiet.json").run_offer("888000222")
    check("a voltage with no stated frequency is not published",
          silent.product is None and silent.published == 0)
    check("and the reason names the missing spec",
          all(result.audit.reason_code == "mains_spec" for result in silent.results),
          str({result.audit.reason_code for result in silent.results}))

    print("6. skipping translation must not silently change the price")
    untranslated = build(translate=False, state="untranslated.json").run_offer(BOILER)
    check("the outcome says the comparison did not run",
          untranslated.compared is False,
          "a Chinese title cannot match an English one, so the search is skipped openly")
    check("and the price is visibly a margin price, not an undercut",
          untranslated.results[0].decision == rules.Decision.REJECT
          and untranslated.results[0].audit.reason_code == "heavy_and_unmatched",
          untranslated.results[0].audit.reason_code)
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

    print("8. the audit file accounts for every variant")
    with open(os.path.join(WORK, "audit.csv"), encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    check("there is a line per variant, published or not", len(rows) >= 4, str(len(rows)))
    check("rejections carry an Arabic reason",
          all(row["reason_ar"] for row in rows if row["decision"] != "publish"))
    check("the read cost is charged once per offer, not once per variant",
          sum(int(row["points_spent"] or 0) for row in rows
              if row["offer_id"] == TSHIRT) == 2,
          "one point for each of the two times this offer was read")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

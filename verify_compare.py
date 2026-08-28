"""
Proof for the image comparison stage.

    python3 verify_compare.py

No network, no key. The recorded search response in samples/lens/ carries the
five cases that decide whether a price is safe to publish:

    a real match on Noon, in SAR, at the top of the visual results
    the same photograph on a 20 litre boiler instead of a 30 litre one
    a Temu listing quoting USD
    an eBay listing, which is not one of the client's five platforms
    a genuine match that the image search ranked ninth

Only the first may survive. Everything here exists because the failure mode is
silent and expensive: a bad match does not crash, it just prices our product
against something we are not selling.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import compare  # noqa: E402
import rules  # noqa: E402

passed = failed = 0

BOILER_IMAGE = "https://cbu01.alicdn.com/img/ibank/boiler-1.jpg"
OUR_TITLE = "Commercial Stainless Steel Electric Water Boiler 30L 3000W"


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


def main() -> int:
    provider = compare.FixtureProvider()
    results = provider.search_by_image(BOILER_IMAGE)
    check("the recorded search loads", len(results) == 5, str(len(results)))

    print("1. only one of five results is safe to price against")
    hits = compare.hits_from_results(results, OUR_TITLE)
    check("exactly one hit survives", len(hits) == 1,
          str([(h.platform, str(h.price_sar)) for h in hits]))
    if hits:
        check("it is the Noon listing", hits[0].platform == "Noon", hits[0].platform)
        check("at its SAR price", hits[0].price_sar == Decimal("689.00"),
              str(hits[0].price_sar))
        check("scoring at or above the client's 95 threshold",
              hits[0].match_score >= rules.MATCH_THRESHOLD, str(hits[0].match_score))

    print("2. why each of the other four was thrown out")
    check("the 20 litre boiler is rejected despite the identical photo",
          compare.text_score(OUR_TITLE,
                             "Commercial Stainless Steel Electric Water Boiler 20L 2000W") == 0,
          "a capacity disagreement must veto the match outright")
    check("a rival quoting USD is unusable", compare.sar_price(
        {"value": "$118.99", "extracted_value": 118.99, "currency": "USD"}) is None)
    check("eBay is not one of the five platforms",
          compare.platform_of("https://www.ebay.com/itm/225533114400", "eBay") is None)
    check("a ninth-ranked visual match cannot reach 95 on the picture alone",
          compare.visual_score(9) < rules.MATCH_THRESHOLD, str(compare.visual_score(9)))

    print("3. the two signals must agree, not average out")
    check("a perfect title at rank 1 scores 100",
          compare.match_score(OUR_TITLE, OUR_TITLE, 1) == 100)
    check("a perfect title ranked ninth is still refused",
          compare.match_score(OUR_TITLE, OUR_TITLE, 9) < rules.MATCH_THRESHOLD)
    check("a rank-1 photo with an unrelated title is refused",
          compare.match_score(OUR_TITLE, "Kids Plastic Lunch Box Cartoon", 1)
          < rules.MATCH_THRESHOLD)
    check("the weaker signal decides, never the stronger",
          compare.match_score(OUR_TITLE, OUR_TITLE, 5)
          == min(compare.visual_score(5), Decimal("100")))

    print("4. reading a price")
    check("SAR written as a symbol is read", compare.sar_price("﷼ 689.00") == Decimal("689.00"))
    check("thousands separators survive", compare.sar_price("SAR 1,299.50") == Decimal("1299.50"))
    check("a bare number with no currency is refused", compare.sar_price("689.00") is None)
    check("an empty price is refused", compare.sar_price("") is None)
    check("a zero price is refused", compare.sar_price("SAR 0.00") is None)

    print("5. platform recognition")
    for link, expected in (("https://www.noon.com/saudi-en/x/p/", "Noon"),
                           ("https://www.amazon.sa/dp/B0C", "Amazon"),
                           ("https://ar.shein.com/item-p-123.html", "SHEIN"),
                           ("https://www.temu.com/sa/g-601.html", "Temu"),
                           ("https://www.aliexpress.com/item/1.html", "AliExpress"),
                           ("https://www.alibaba.com/product/1.html", None),
                           ("https://random-shop.sa/item/1", None)):
        check(f"{link.split('/')[2]} -> {expected}",
              compare.platform_of(link) == expected, str(compare.platform_of(link)))

    print("6. marketing words must not manufacture a match")
    check("filler words are ignored",
          compare.text_score("Hot Sale New Fashion Free Shipping Boiler 30L",
                             "Boiler 30L") == 100)
    check("but the product words still have to be there",
          compare.text_score("Hot Sale New Fashion Free Shipping",
                             "Kids Lunch Box") == 0)

    print("7. a hit is tied to the variant whose photo found it")
    tagged = compare.hits_from_results(results, OUR_TITLE, variant_sku="sku-black")
    check("the sku travels with the hit", tagged and tagged[0].matched_variant == "sku-black")
    other = rules.Variant(sku_id="sku-white", attributes={}, price_cny=Decimal("10"),
                          stock=5, weight_kg=Decimal("1"))
    check("and rules.best_match refuses to apply it to another variant",
          rules.best_match(tagged, other) is None,
          "the black photo's price must never price the white variant")
    same = rules.Variant(sku_id="sku-black", attributes={}, price_cny=Decimal("10"),
                         stock=5, weight_kg=Decimal("1"))
    check("while the variant it belongs to accepts it",
          rules.best_match(tagged, same) is not None)

    print("8. the whole stage, end to end, against the rules engine")
    variant = rules.Variant(sku_id="sku-boiler",
                            attributes={"images": [BOILER_IMAGE]},
                            price_cny=Decimal("460.00"), stock=85, weight_kg=Decimal("12.4"))
    product = rules.Product(offer_id="611229900011", title_zh="商用不锈钢电热开水器 30L",
                            description_zh="220V 3000W", images=[BOILER_IMAGE],
                            variants=[variant], specifications={"电压": "220V",
                                                               "频率": "50/60Hz"})
    by_variant = compare.hits_for_product(provider, product, OUR_TITLE)
    check("the search is driven by the variant's own photo",
          list(by_variant) == ["sku-boiler"], str(list(by_variant)))

    engine = rules.Engine(cny_to_sar=Decimal("0.52"))
    result = engine.evaluate(product, by_variant)[0]
    check("the product is accepted", result.decision == rules.Decision.PUBLISH,
          f"{result.decision} / {result.audit.reason_ar}")
    check("priced by undercutting Noon, not by margin",
          "Noon" in result.audit.pricing_basis, result.audit.pricing_basis)
    # 689.00 is over 500 SAR, so the client's band is 1% off.
    check("at Noon's price minus the 1% band for this price range",
          result.final_price_sar == Decimal("682.11"), str(result.final_price_sar))
    check("and it stays above our landed cost",
          result.final_price_sar > engine.landed_cost_sar(variant),
          f"{result.final_price_sar} vs {engine.landed_cost_sar(variant)}")
    check("12.4 kg is flagged as free shipping, not fast",
          (result.audit.requires_shipping, result.audit.shipping_type) == ("no", "free"),
          f"{result.audit.requires_shipping} / {result.audit.shipping_type}")

    print("9. with no match at all the engine falls back to margin")
    empty = compare.hits_for_product(compare.FixtureProvider(), product, "Something Else Entirely")
    fallback = engine.evaluate(product, empty)[0]
    check("a heavy unmatched product is held back rather than guessed at",
          fallback.decision == rules.Decision.REJECT
          and fallback.audit.reason_code == "heavy_and_unmatched",
          f"{fallback.decision} / {fallback.audit.reason_code}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

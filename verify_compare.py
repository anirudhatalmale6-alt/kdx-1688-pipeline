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

import json
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
# The image behind the recorded live google_lens response.
SKIRT_IMAGE = ("https://image.uniqlo.com/UQ/ST3/us/imagesgoods/470922"
               "/feature/usgoods_470922_feature1.jpg")


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

    print("10. the recorded LIVE responses, and what they actually contain")
    # Both files are real SerpApi responses on the client's own key, kept so
    # these numbers can be re-checked rather than taken on trust.
    lens_file = os.path.join(HERE, "samples", "lens", compare.slugify(SKIRT_IMAGE) + ".json")
    with open(lens_file, encoding="utf-8") as handle:
        live_lens = json.load(handle)["visual_matches"]
    priced = [m for m in live_lens if m.get("price")]
    check("google_lens returned 60 visual matches for a real product",
          len(live_lens) == 60, str(len(live_lens)))
    check("but only 3 of them carried a price at all",
          len(priced) == 3, f"{len(priced)} of {len(live_lens)}")
    check("and the prices it did give are already in SAR, so nothing is converted",
          all(compare.sar_price(m["price"]) is not None for m in priced),
          str([m["price"] for m in priced]))

    with open(os.path.join(HERE, "samples", "shopping",
                           compare.slugify(OUR_TITLE) + ".json"), encoding="utf-8") as handle:
        live_shopping = json.load(handle)["shopping_results"]
    check("google_shopping returned 40 results for the same kind of product",
          len(live_shopping) == 40, str(len(live_shopping)))
    check("and priced every single one of them",
          all(row.get("price") for row in live_shopping),
          str(sum(1 for row in live_shopping if row.get("price"))))
    on_platforms = [row for row in live_shopping
                    if compare.platform_of(str(row.get("product_link") or ""),
                                           str(row.get("source") or ""))]
    check("seven of them are on platforms the client named",
          len(on_platforms) == 7, str(len(on_platforms)))

    print("11. the join: image finds who sells it, shopping finds for how much")
    identified = [{"platform": "Amazon", "score": Decimal("100"), "link": "x",
                   "title": "t", "price": None},
                  {"platform": "Noon", "score": Decimal("100"), "link": "y",
                   "title": "t", "price": None}]

    # Why the default moved to 60 on 29 August. Set back to 95 explicitly here,
    # because the finding is about that number and it must not depend on which
    # value happens to be the default today.
    original = compare.TEXT_THRESHOLD
    try:
        compare.TEXT_THRESHOLD = Decimal("95")
        strict = compare.prices_from_shopping(identified, live_shopping, OUR_TITLE, "sku-1")
    finally:
        compare.TEXT_THRESHOLD = original
    check("at the client's original 95% wording rule, real listings price NOTHING",
          strict == [], f"{len(strict)} hits")
    check("because the best a real rival title scores against ours is 75",
          max(compare.text_score(OUR_TITLE, row.get("title", "")) for row in on_platforms)
          == Decimal("75"),
          str(max(compare.text_score(OUR_TITLE, row.get("title", "")) for row in on_platforms)))

    original = compare.TEXT_THRESHOLD
    try:
        compare.TEXT_THRESHOLD = Decimal("60")
        relaxed = compare.prices_from_shopping(identified, live_shopping, OUR_TITLE, "sku-1")
        check("lowering only the wording bar makes real matches appear",
              len(relaxed) == 3, str(len(relaxed)))
        check("every one of them is on a platform the picture had identified",
              all(hit.platform in ("Amazon", "Noon") for hit in relaxed),
              str([hit.platform for hit in relaxed]))
        check("the cheapest is Amazon at 553.01 SAR",
              min(hit.price_sar for hit in relaxed) == Decimal("553.01"),
              str(sorted(hit.price_sar for hit in relaxed)))
        check("the score stays the picture's, so the pricing engine accepts it",
              all(hit.match_score >= rules.MATCH_THRESHOLD for hit in relaxed),
              str([str(hit.match_score) for hit in relaxed]))

        # Controls. Each removes one condition and must kill the match.
        only_noon = [dict(identified[1])]
        noon_hits = compare.prices_from_shopping(only_noon, live_shopping, OUR_TITLE, "s")
        check("CONTROL: a platform the picture did NOT identify is never priced",
              all(hit.platform == "Noon" for hit in noon_hits),
              str([hit.platform for hit in noon_hits]))

        foreign = [dict(row, price="$99.00", extracted_price=99.0) for row in on_platforms]
        check("CONTROL: a rival price in another currency is dropped, not converted",
              compare.prices_from_shopping(identified, foreign, OUR_TITLE, "s") == [])

        wrong_words = compare.prices_from_shopping(identified, live_shopping,
                                                   "Kids Plastic Lunch Box", "s")
        check("CONTROL: a title that disagrees prices nothing even on the right platform",
              wrong_words == [], str(len(wrong_words)))
    finally:
        compare.TEXT_THRESHOLD = original

    print("12. the second search only happens when it can change something")
    class CountingShopping:
        def __init__(self, rows):
            self.rows, self.calls = rows, 0

        def search_by_title(self, title):
            self.calls += 1
            return self.rows

    class PricedLens:
        """Every identified platform already quoted a price."""

        def search_by_image(self, image_url):
            return [{"link": "https://www.noon.com/x", "source": "Noon", "title": OUR_TITLE,
                     "price": {"currency": "SAR", "extracted_value": 689.0,
                               "value": "SAR 689.00"}}]

    shopping = CountingShopping(live_shopping)
    compare.hits_for_product(PricedLens(), product, OUR_TITLE, shopping=shopping)
    check("nothing left to look up means no shopping call is paid for",
          shopping.calls == 0, f"{shopping.calls} calls")

    # The recorded boiler fixture identifies two platforms: Noon quoting 689 SAR
    # and Temu quoting USD, which is dropped. Under the client's rule of 29
    # August - do not buy a second search when a price is already in hand - this
    # product costs one search and is priced against Noon.
    #
    # That rule has a price of its own, and it is measurable here rather than
    # arguable: the shopping engine, if it were asked, finds the same boiler on
    # Amazon at 553.01 SAR. Undercutting 689 instead of 553 puts us 136 SAR
    # above the cheapest rival on this one product. Both behaviours are kept,
    # one environment variable apart, so the choice stays the client's.
    shopping = CountingShopping(live_shopping)
    cheap = compare.hits_for_product(compare.FixtureProvider(), product, OUR_TITLE,
                                     shopping=shopping)
    check("a price already in hand means no second search is bought",
          shopping.calls == 0, f"{shopping.calls} calls")
    check("so the product is priced against the rival the picture priced",
          min(hit.price_sar for hit in cheap["sku-boiler"]) == Decimal("689.00"),
          str(sorted(hit.price_sar for hit in cheap["sku-boiler"])))

    original_when = compare.SHOPPING_WHEN
    try:
        compare.SHOPPING_WHEN = "any-unpriced"
        shopping = CountingShopping(live_shopping)
        thorough = compare.hits_for_product(compare.FixtureProvider(), product, OUR_TITLE,
                                            shopping=shopping)
        check("the thorough setting looks up the platform that quoted no SAR price",
              shopping.calls == 1, f"{shopping.calls} calls")
        # And on this product it buys nothing: the picture identified Noon
        # (priced) and Temu (not priced), and the recorded shopping response
        # contains no Temu row at all - 5 Amazon rows, 2 Noon, 33 elsewhere.
        # Amazon quotes 553.01 there, cheaper than Noon's 689, and we still do
        # not take it, because the picture never identified Amazon for this
        # product and a shopping row on its own is not allowed to establish
        # identity. That guard is the point, not a shortfall.
        check("but on this product the extra search changes nothing",
              min(hit.price_sar for hit in thorough["sku-boiler"]) == Decimal("689.00"),
              str(sorted(hit.price_sar for hit in thorough["sku-boiler"])))
        check("CONTROL: the cheaper Amazon row was there and was deliberately not used",
              any(compare.sar_price(row.get("price")) == Decimal("553.01")
                  for row in live_shopping))
    finally:
        compare.SHOPPING_WHEN = original_when

    class UnpricedLens:
        """Identifies two platforms, quotes neither - the measured normal case."""

        def search_by_image(self, image_url):
            return [{"link": "https://www.amazon.sa/dp/X", "source": "Amazon",
                     "title": OUR_TITLE},
                    {"link": "https://www.noon.com/x", "source": "Noon",
                     "title": OUR_TITLE}]

    shopping = CountingShopping(live_shopping)
    compare.hits_for_product(UnpricedLens(), product, OUR_TITLE, shopping=shopping)
    check("an identified but unpriced product asks the shopping engine once",
          shopping.calls == 1, f"{shopping.calls} calls")

    class NoMatchLens:
        def search_by_image(self, image_url):
            return [{"link": "https://example.com/x", "source": "Example", "title": OUR_TITLE}]

    shopping = CountingShopping(live_shopping)
    compare.hits_for_product(NoMatchLens(), product, OUR_TITLE, shopping=shopping)
    check("CONTROL: no identified platform means no second search is paid for",
          shopping.calls == 0, f"{shopping.calls} calls")

    print("13. one search per product, or one per colour - the bill lives here")

    class CountingLens:
        def __init__(self):
            self.calls, self.images = 0, []

        def search_by_image(self, image_url):
            self.calls += 1
            self.images.append(image_url)
            return [{"link": "https://www.noon.com/x", "source": "Noon", "title": OUR_TITLE,
                     "price": {"currency": "SAR", "extracted_value": 689.0,
                               "value": "SAR 689.00"}}]

    colours = [
        rules.Variant(sku_id=f"sku-{name}", attributes={"color": name,
                                                        "images": [f"https://img/{name}.jpg"]},
                      price_cny=Decimal("460.00"), stock=5, weight_kg=Decimal("12.4"))
        for name in ("black", "white", "silver", "red", "blue")
    ]
    five = rules.Product(offer_id="611229900012", title_zh="商用不锈钢电热开水器 30L",
                         description_zh="220V 3000W", images=[BOILER_IMAGE],
                         variants=colours)

    lens = CountingLens()
    per_product = compare.hits_for_product(lens, five, OUR_TITLE, scope="product")
    check("five colours cost one search, not five", lens.calls == 1, f"{lens.calls} calls")
    check("and the one searched is the product's main photo",
          lens.images == [BOILER_IMAGE], str(lens.images))
    check("every colour still gets the answer",
          sorted(per_product) == sorted(v.sku_id for v in colours), str(sorted(per_product)))
    # The hit carries no variant tag, which is what makes applying it to all
    # five legitimate rather than a shortcut - rules.best_match already refuses
    # a TAGGED hit against a different variant, and would have refused these.
    check("the hit is untagged, so the pricing engine accepts it for any colour",
          all(hit.matched_variant == "" for rows in per_product.values() for hit in rows))
    check("and rules.best_match does accept it for a colour that was not searched",
          rules.best_match(per_product["sku-red"], colours[3]) is not None)

    lens = CountingLens()
    per_colour = compare.hits_for_product(lens, five, OUR_TITLE, scope="variant")
    check("the precise setting is still there and costs five searches",
          lens.calls == 5, f"{lens.calls} calls")
    check("and there each hit is tagged to the photo that found it",
          per_colour["sku-red"][0].matched_variant == "sku-red")
    check("CONTROL: that tagged hit is refused against another colour",
          rules.best_match(per_colour["sku-red"], colours[0]) is None,
          "the red photo's price must not price the black one")

    class NeverCalled:
        def search_by_image(self, image_url):
            raise AssertionError("a product with no photo must not be searched")

    naked = rules.Product(offer_id="611229900013", title_zh="x", description_zh="",
                          images=[], variants=[
                              rules.Variant(sku_id="sku-none", attributes={},
                                            price_cny=Decimal("10"), stock=1,
                                            weight_kg=Decimal("1"))])
    check("CONTROL: a product with no photo at all buys no search",
          compare.hits_for_product(NeverCalled(), naked, OUR_TITLE, scope="product") == {})

    # "Nobody else sells this" against "your key is invalid". SerpApi puts both
    # in `error`, and reading them alike killed a live run at product 1 of 12.
    check("a product no competitor carries returns no hits, and does not raise",
          compare.rows_or_empty(
              {"error": "Google Lens hasn't returned any results for this query."},
              "visual_matches") == [])
    check("the same for the shopping engine",
          compare.rows_or_empty(
              {"error": "Google Shopping hasn't returned any results for this query."},
              "shopping_results") == [])
    for fault in ("Invalid API key.",
                  "Your account has run out of searches.",
                  "We couldn't process your request."):
        try:
            compare.rows_or_empty({"error": fault}, "visual_matches")
            check(f"CONTROL a real fault still raises: {fault[:28]}", False)
        except compare.CompareError as exc:
            check(f"CONTROL a real fault still raises: {fault[:28]}", fault in str(exc))
    check("CONTROL a good payload still comes through untouched",
          compare.rows_or_empty({"visual_matches": [{"title": "x"}]},
                                "visual_matches") == [{"title": "x"}])
    check("CONTROL an empty good payload is empty, not an error",
          compare.rows_or_empty({}, "visual_matches") == [])

    # A read timeout is the connection saying nothing, not an answer about a
    # product. One of them at product ~150 of 300 took a whole night with it.
    class Flaky:
        def __init__(self, fail_times, payload=None):
            self.fail_times = fail_times
            self.payload = payload if payload is not None else {"visual_matches": [1]}
            self.calls = 0

        def __call__(self, url, timeout=0):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise TimeoutError("The read operation timed out")
            return _FakeBody(json.dumps(self.payload))

    slept = []
    flaky = Flaky(fail_times=2)
    got = compare.fetch_json("https://serpapi/x", timeout=1, opener=flaky,
                             sleep=slept.append)
    check("a timed-out request is tried again rather than losing the product",
          got == {"visual_matches": [1]} and flaky.calls == 3, str(flaky.calls))
    check("and it waits between attempts instead of hammering",
          slept == [2, 4], str(slept))

    dead = Flaky(fail_times=99)
    try:
        compare.fetch_json("https://serpapi/x", timeout=1, opener=dead,
                           sleep=lambda _s: None)
        check("CONTROL a host that is really down still raises", False)
    except compare.CompareError as exc:
        check("CONTROL a host that is really down still raises",
              "after 3 attempts" in str(exc), str(exc))
    check("CONTROL and it gave up after three, not forever", dead.calls == 3,
          str(dead.calls))

    good = Flaky(fail_times=0)
    compare.fetch_json("https://serpapi/x", timeout=1, opener=good,
                       sleep=lambda _s: None)
    check("CONTROL a request that works is made exactly once", good.calls == 1)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


class _FakeBody:
    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    raise SystemExit(main())

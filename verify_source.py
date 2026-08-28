"""
Proof for the source layer - the part that turns a raw 1688 response into
something the rest of the pipeline can use.

    python3 verify_source.py

No network, no credentials. This is the module that cannot be tested against the
live API yet, which is exactly why it is tested hardest here: the day the
permission arrives, the first real payload becomes a fixture in samples/offers/
and this same suite runs against it unchanged.

The check I care most about is the photo axis. 1688 hangs photos off the colour
attribute and prices off the full colour x size sku, so the grouping has to be
derived from the data - from which attribute actually carries an image - rather
than from matching the Chinese word for "colour". Section 3 proves it survives
an offer whose axis is named something else entirely.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import mapping  # noqa: E402
import source  # noqa: E402

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


def load(offer_id: str) -> dict:
    with open(os.path.join(HERE, "samples", "offers", f"{offer_id}.json"),
              encoding="utf-8") as handle:
        return json.load(handle)


TSHIRT = "104843239419"
BOILER = "611229900011"


def main() -> int:
    fixtures = source.FixtureSource()

    print("1. a two-colour, two-size offer becomes two photos with their own prices")
    shirt = fixtures.get_product(TSHIRT)
    check("four skus collapse to two variants", len(shirt["variants"]) == 2,
          str(len(shirt["variants"])))
    white, black = shirt["variants"]
    check("grouped by colour, in the order 1688 listed them",
          [v["original"] for v in shirt["variants"]] == ["白色", "黑色"],
          str([v["original"] for v in shirt["variants"]]))
    check("both white sizes share the one white photo",
          white["image"].endswith("white.jpg") and len(white["images"]) == 1,
          str(white["images"]))
    check("and the black photo is a different one",
          black["image"].endswith("black.jpg"))
    check("each colour keeps both its sizes",
          [s["original"] for s in white["sizes"]] == ["S", "M"],
          str([s["original"] for s in white["sizes"]]))
    check("the two sizes of one colour carry different prices",
          [s["price"] for s in white["sizes"]] == [Decimal("28.50"), Decimal("29.80")],
          str([str(s["price"]) for s in white["sizes"]]))

    print("2. the fields the pipeline needs downstream")
    check("sku ids survive for later updates",
          [s["sku_id"] for s in white["sizes"]] == ["5501001", "5501002"])
    check("stock survives per size", white["sizes"][1]["stock"] == 620)
    check("a sold-out size keeps its zero rather than vanishing",
          black["sizes"][1]["stock"] == 0)
    check("weight is read from the shipping block", shirt["weight_kg"] == 0.35)
    check("the mains attributes reach the 220V filter",
          shirt["attributes"].get("电压") == "220V"
          and shirt["attributes"].get("频率") == "50/60Hz",
          str(shirt["attributes"]))
    check("the gallery keeps the offer-level photos", len(shirt["images"]) == 2)
    check("title and category come through",
          shirt["title_zh"].startswith("2024") and shirt["category_id"] == "1031910")

    print("3. the photo axis is derived from the data, not from a Chinese word")
    renamed = copy.deepcopy(load(TSHIRT))
    skus = renamed["result"]["productInfo"]["skuInfos"]
    for sku in skus:
        sku["attributes"][0]["attributeName"] = "款式分类"
    odd = source.normalise(renamed)
    check("an axis named something else still groups correctly",
          len(odd["variants"]) == 2 and odd["variants"][0]["image"].endswith("white.jpg"),
          str([v["original"] for v in odd["variants"]]))

    swapped = copy.deepcopy(load(TSHIRT))
    for sku in swapped["result"]["productInfo"]["skuInfos"]:
        sku["attributes"].reverse()  # size first, colour second
    check("attribute order does not decide the axis",
          len(source.normalise(swapped)["variants"]) == 2)

    print("4. the control: with no image anywhere the axis must not be invented")
    blind = copy.deepcopy(load(TSHIRT))
    for sku in blind["result"]["productInfo"]["skuInfos"]:
        sku["attributes"][0].pop("skuImageUrl")
    fallback = source.normalise(blind)
    check("it falls back to the colour attribute by name",
          len(fallback["variants"]) == 2,
          str([v["original"] for v in fallback["variants"]]))
    check("and every variant still gets a photo from the gallery",
          all(v["image"] for v in fallback["variants"]))

    print("5. an offer with no sku table still publishes")
    boiler = fixtures.get_product(BOILER)
    check("it gets exactly one variant", len(boiler["variants"]) == 1)
    check("priced from the offer's own price range",
          boiler["variants"][0]["price"] == Decimal("460.00"),
          str(boiler["variants"][0].get("price")))
    check("with no size axis", boiler["variants"][0]["sizes"] == [])
    check("and a heavy weight, which is free shipping later",
          boiler["weight_kg"] == 12.4)

    print("6. alternative key spellings, because the live shape is not confirmed yet")
    variant_keys = copy.deepcopy(load(TSHIRT))
    info = variant_keys["result"].pop("productInfo")
    info["offerId"] = info.pop("productID")
    info["productSkuInfos"] = info.pop("skuInfos")
    for sku in info["productSkuInfos"]:
        sku["skuID"] = sku.pop("skuId")
        sku["attributeList"] = sku.pop("attributes")
        for attribute in sku["attributeList"]:
            attribute["attrName"] = attribute.pop("attributeName")
            attribute["attrValue"] = attribute.pop("attributeValue")
    variant_keys["result"]["product"] = info
    renamed_out = source.normalise(variant_keys)
    check("a differently spelled response normalises the same way",
          len(renamed_out["variants"]) == 2 and renamed_out["offer_id"] == TSHIRT,
          str(renamed_out.get("offer_id")))

    print("7. what must fail loudly rather than publish something wrong")
    for label, payload in (("an empty response", {}),
                           ("a response with no offer id",
                            {"result": {"productInfo": {"subject": "x"}}})):
        try:
            source.normalise(payload)
            check(f"{label} is refused", False, "it normalised")
        except source.SourceError:
            check(f"{label} is refused", True)

    priceless = copy.deepcopy(load(BOILER))
    priceless["result"]["productInfo"]["productSaleInfo"].pop("priceRangeList")
    try:
        source.normalise(priceless)
        check("an offer with no price at all is refused", False, "it normalised")
    except source.SourceError:
        check("an offer with no price at all is refused", True)

    unweighed = copy.deepcopy(load(TSHIRT))
    unweighed["result"]["productInfo"].pop("productShippingInfo")
    check("an unknown weight is treated as heavy, never as light",
          source.normalise(unweighed)["weight_kg"] > 2.0,
          "a wrong 'light' flag would charge fast shipping on a heavy parcel")

    print("8. the output feeds the KDX shape without a translation step")
    product = mapping.to_kdx_product(
        offer_id=shirt["offer_id"], name_ar="تيشيرت", name_en="T-shirt",
        name_original=shirt["title_zh"], weight_kg=shirt["weight_kg"],
        images=shirt["images"], variants=shirt["variants"])
    check("it builds", product["source_offer_id"] == TSHIRT)
    check("two variants reach KDX", len(product["variants"]) == 2)
    check("the card price is the cheapest sku in the offer",
          product["price"] == 28.50, str(product["price"]))
    check("and the range ends at the dearest", product["price_max"] == 31.20,
          str(product["price_max"]))
    check("every photo lands in the gallery exactly once",
          len(product["images"]) == 4 and len(set(product["images"])) == 4,
          str(product["images"]))
    check("the sizes list the old front end reads is still flat",
          [s["original"] for s in product["sizes"]] == ["S", "M"],
          str(product["sizes"]))

    print("9. an ACL refusal is reported as what it is, not as a crash")

    class Refusing:
        def call(self, *_args, **_kwargs):
            raise source.AopError(
                "HTTP 400 gw.APIACLDecline: AppKey is not allowed(acl)")

    try:
        source.AopSource(Refusing()).get_product(TSHIRT)
        check("the official source explains the permission problem", False, "it returned")
    except source.SourceError as exc:
        check("the official source explains the permission problem",
              "not permitted" in str(exc) and "APIACLDecline" in str(exc), str(exc)[:120])

    print("10. choosing a source needs no code change")
    os.environ["KDX_SOURCE"] = "fixture"
    check("KDX_SOURCE=fixture selects the recorded payloads",
          isinstance(source.build_source(), source.FixtureSource))
    os.environ["KDX_SOURCE"] = "http"
    os.environ["KDX_SOURCE_URL"] = "https://example.invalid/item/{offer_id}?key={key}"
    check("KDX_SOURCE=http selects a provider that holds the permission",
          isinstance(source.build_source(), source.HttpSource))
    os.environ.pop("KDX_SOURCE")
    os.environ.pop("KDX_SOURCE_URL")
    check("both recorded offers are discoverable",
          fixtures.offer_ids() == sorted([TSHIRT, BOILER]), str(fixtures.offer_ids()))

    out = os.path.join(HERE, "samples", "normalised_offer.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(product, handle, ensure_ascii=False, indent=2, default=str)
    print(f"\nwrote {os.path.relpath(out, HERE)}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Proof for the all-photos / all-prices shape KDX asked for.

    python3 verify_variants.py

No network, no credentials. Every claim I make to the client about this shape is
checked here, including the two that are easy to get wrong: that two sizes of one
colour keep one photo and two prices, and that the old single-price shape still
builds unchanged so nothing breaks on the day KDX switches over.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import mapping  # noqa: E402

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


RED = "https://cbu01.alicdn.com/red.jpg"
RED_2 = "https://cbu01.alicdn.com/red-back.jpg"
BLUE = "https://cbu01.alicdn.com/blue.jpg"
MAIN = "https://cbu01.alicdn.com/main.jpg"

VARIANTS = [
    {"original": "红色", "en": "Red", "ar": "أحمر", "image": RED, "images": [RED, RED_2],
     "sizes": [{"original": "S", "en": "S", "ar": "صغير", "price": 45.9,
                "sku_id": "sku-1", "stock": 40, "weight": 0.42},
               {"original": "M", "en": "M", "ar": "وسط", "price": 47.5,
                "sku_id": "sku-2", "stock": 120, "weight": 0.45}]},
    {"original": "蓝色", "en": "Blue", "ar": "أزرق", "image": BLUE,
     "sizes": [{"original": "M", "en": "M", "ar": "وسط", "price": 52.0,
                "sku_id": "sku-3", "stock": 0, "weight": 3.1}]},
]


def build(**overrides):
    kwargs = dict(offer_id="104843239419", name_ar="فستان", name_en="Dress",
                  name_original="连衣裙", weight_kg=0.45, images=[MAIN], variants=VARIANTS)
    kwargs.update(overrides)
    return mapping.to_kdx_product(**kwargs)


def main() -> int:
    product = build()

    print("1. one photo, its own prices - the thing the client asked for")
    red = product["variants"][0]
    check("the red photo carries two sizes", len(red["sizes"]) == 2)
    check("both red sizes share the one red photo", red["image"] == RED)
    check("and keep two different prices",
          [size["price"] for size in red["sizes"]] == [45.90, 47.50],
          str([size["price"] for size in red["sizes"]]))
    check("the price shown under the red photo is its cheapest size",
          red["price"] == 45.90 and red["price_min"] == 45.90 and red["price_max"] == 47.50)
    check("sku id and stock survive per size",
          red["sizes"][1]["sku_id"] == "sku-2" and red["sizes"][1]["stock"] == 120)

    print("2. the product card")
    check("price is the cheapest price in the whole offer", product["price"] == 45.90,
          str(product["price"]))
    check("price_max is the dearest", product["price_max"] == 52.00, str(product["price_max"]))
    check("currency is still SAR", product["price_currency"] == "SAR")

    print("3. every photo reaches KDX, once each")
    check("all four photos are in the gallery",
          product["images"] == [RED, RED_2, BLUE, MAIN], str(product["images"]))
    check("no photo is repeated", len(product["images"]) == len(set(product["images"])))

    print("4. the current KDX front end must not break")
    check("sizes is still a flat list of names",
          product["sizes"] == [{"original": "S", "en": "S", "ar": "صغير"},
                               {"original": "M", "en": "M", "ar": "وسط"}],
          str(product["sizes"]))
    check("a size that appears under two colours is listed once",
          [size["original"] for size in product["sizes"]] == ["S", "M"])
    check("no price leaks into the old sizes list",
          all("price" not in size for size in product["sizes"]))

    print("5. weight decides shipping per size, not per product")
    check("0.42 kg is fast shipping", red["sizes"][0]["needs_shipment"] is True)
    check("3.1 kg is free shipping",
          product["variants"][1]["sizes"][0]["needs_shipment"] is False)

    print("6. a product with no colour axis still renders")
    plain = build(variants=[{"original": "", "image": MAIN, "price": 30}])
    check("it gets exactly one variant", len(plain["variants"]) == 1)
    check("with the price on the variant itself", plain["variants"][0]["price"] == 30.00)
    check("and an empty size list", plain["variants"][0]["sizes"] == [])
    check("the card price follows it", plain["price"] == 30.00)

    print("7. the mistakes I want to fail loudly")
    try:
        build(variants=[{"original": "红色", "image": RED}])
        check("a variant with no sizes and no price is refused", False, "it built")
    except ValueError:
        check("a variant with no sizes and no price is refused", True)
    try:
        mapping.to_kdx_product(offer_id="1", name_ar="a", name_en="a", name_original="a",
                               weight_kg=1, images=[MAIN])
        check("the old shape without a price is refused", False, "it built")
    except ValueError:
        check("the old shape without a price is refused", True)

    print("8. the old single-price shape is untouched")
    old = mapping.to_kdx_product(offer_id="1", name_ar="a", name_en="a", name_original="a",
                                 price_sar=99.999, weight_kg=1, images=[MAIN], sizes=["S"])
    check("it still builds", old["price"] == 100.00, str(old["price"]))
    check("and carries no variants key", "variants" not in old)
    check("money is rounded to two decimals", isinstance(old["price"], float))

    sample = os.path.join(HERE, "samples", "kdx_product_with_variants.json")
    with open(sample, "w", encoding="utf-8") as handle:
        json.dump(product, handle, ensure_ascii=False, indent=2)
    print(f"\nwrote {os.path.relpath(sample, HERE)} - the exact JSON KDX will receive")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

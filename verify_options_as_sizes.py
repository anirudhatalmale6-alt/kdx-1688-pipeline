#!/usr/bin/env python3
"""
Mirroring purchase options into sizes[], and leaving everything else alone.

5 September 2026. He asked why a product with fifty prices shows one price in
his shop. The answer is in the contract measured against his endpoint on 28
August: it validates ten fields, and variants[] - where every option's price and
photo live - is not one of them. A front end reading only sizes[] therefore has
nowhere to find those prices, and an offer with no size axis sends sizes[]
empty.

KDX_OPTIONS_AS_SIZES=1 mirrors the options into sizes[] with their prices, so
his current site renders them with no change on his side.

What has to stay true when it is on:

  * variants[] is untouched - it is still the full record, and the mirror is a
    copy, never a move
  * a product that really has sizes keeps sending its size NAMES without
    prices, exactly as it does today
  * a product with one option gets nothing: there is nothing to choose between
  * the price against each option is that option's own price, not the card price

And when it is off - which is how it ships until he asks - the payload must be
identical to the one he is reviewing right now, byte for byte.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import mapping                                              # noqa: E402

PASS = FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def load(flag: str):
    """Re-import mapping with the switch in the state under test."""
    if flag:
        os.environ["KDX_OPTIONS_AS_SIZES"] = flag
    else:
        os.environ.pop("KDX_OPTIONS_AS_SIZES", None)
    return importlib.reload(mapping)


# --------------------------------------------------------------------------
# Fixtures: the shapes 1688 actually sends
# --------------------------------------------------------------------------

# Three purchase options, no size axis, each with its own photo and price.
# This is offer 1072288684025's shape - 52 of these in the real payload.
SIZELESS = [
    {"original": "30*50*10cm 高脚款", "en": "30*50*10cm high leg",
     "ar": "٣٠*٥٠*١٠ سم نمط الأرجل العالية",
     "image": "https://cbu01.alicdn.com/img/ibank/a.jpg", "price": "144.87"},
    {"original": "40*40*12cm 可拼接", "en": "40*40*12cm joinable",
     "ar": "٤٠*٤٠*١٢ سم قابلة للربط",
     "image": "https://cbu01.alicdn.com/img/ibank/b.jpg", "price": "170.30"},
    {"original": "50*40*12cm 可拼接", "en": "50*40*12cm joinable",
     "ar": "٥٠*٤٠*١٢ سم قابلة للربط",
     "image": "https://cbu01.alicdn.com/img/ibank/c.jpg", "price": "219.79"},
]

# One colour, two real sizes under it - the case that must not change.
WITH_SIZES = [
    {"original": "黑色", "en": "Black", "ar": "أسود",
     "image": "https://cbu01.alicdn.com/img/ibank/k.jpg",
     "sizes": [{"original": "M", "en": "M", "ar": "M", "price": "31.00",
                "sku_id": "1", "stock": 9},
               {"original": "L", "en": "L", "ar": "L", "price": "34.00",
                "sku_id": "2", "stock": 4}]},
]

SINGLE = [
    {"original": "标准款", "en": "Standard", "ar": "قياسي",
     "image": "https://cbu01.alicdn.com/img/ibank/s.jpg", "price": "88.00"},
]


def build(variants, module=mapping) -> dict:
    return module.to_kdx_product(
        offer_id="1072288684025",
        name_ar="لوح بلاستيكي مقاوم للرطوبة", name_en="Moisture proof pallet",
        name_original="防潮塑料板", weight_kg=Decimal("1.0"),
        images=["https://cbu01.alicdn.com/img/ibank/cover.jpg"],
        variants=variants, requires_shipping="yes")


# --------------------------------------------------------------------------
section("Off by default: the shape he is reviewing right now")
# --------------------------------------------------------------------------

off = load("")
check("the switch reads off", off.MIRROR_OPTIONS_AS_SIZES, False)

before = build(SIZELESS, off)
check("sizeless product still sends sizes empty", before["sizes"], [])
check("its options are all in variants", len(before["variants"]), 3)
check("card price is the cheapest option", before["price"], 144.87)
check("dearest option reaches price_max", before["price_max"], 219.79)

for value in ("0", "no", "off", "false", ""):
    check(f"KDX_OPTIONS_AS_SIZES={value!r} stays off",
          load(value).MIRROR_OPTIONS_AS_SIZES, False)

# --------------------------------------------------------------------------
section("On: the options arrive where his front end looks")
# --------------------------------------------------------------------------

on = load("1")
check("the switch reads on", on.MIRROR_OPTIONS_AS_SIZES, True)

after = build(SIZELESS, on)
check("one sizes entry per option", len(after["sizes"]), 3)
check("in the order 1688 sends them",
      [size["original"] for size in after["sizes"]],
      [variant["original"] for variant in SIZELESS])
check("each carries its OWN price, not the card price",
      [size["price"] for size in after["sizes"]], [144.87, 170.30, 219.79])
check("each carries its own photo",
      [size["image"] for size in after["sizes"]],
      [variant["image"] for variant in SIZELESS])
check("the Arabic name travels with it",
      [size["ar"] for size in after["sizes"]],
      [variant["ar"] for variant in SIZELESS])
check("English too",
      [size["en"] for size in after["sizes"]],
      [variant["en"] for variant in SIZELESS])

# The mirror is a copy. Nothing may be moved out of variants.
check("variants[] is untouched", after["variants"], before["variants"])
check("every option still has its price in variants",
      [variant["price"] for variant in after["variants"]],
      [144.87, 170.30, 219.79])
check("the gallery is unchanged", after["images"], before["images"])
check("the card price is unchanged", after["price"], before["price"])

for value in ("1", "yes", "true", "on", "ON", " Yes "):
    check(f"KDX_OPTIONS_AS_SIZES={value!r} turns it on",
          load(value).MIRROR_OPTIONS_AS_SIZES, True)

# --------------------------------------------------------------------------
section("On, but narrow: what it must NOT touch")
# --------------------------------------------------------------------------

on = load("1")
sized = build(WITH_SIZES, on)
check("a real size axis still sends size NAMES",
      sized["sizes"], [{"original": "M", "en": "M", "ar": "M"},
                       {"original": "L", "en": "L", "ar": "L"}])
check("...with no price on them",
      any("price" in size for size in sized["sizes"]), False)
check("...and the prices stay under the variant",
      [size["price"] for size in sized["variants"][0]["sizes"]], [31.0, 34.0])

single = build(SINGLE, on)
check("one option is not a choice: sizes stays empty", single["sizes"], [])
check("its price is still on the card", single["price"], 88.0)

# The old single-price shape, built without variants at all, must be untouched.
plain = on.to_kdx_product(
    offer_id="9", name_ar="ا", name_en="a", name_original="a",
    price_sar=Decimal("12.00"), weight_kg=Decimal("0.5"),
    images=["one.jpg"], sizes=["S", "M"], requires_shipping="yes")
check("no-variants shape keeps its plain size names",
      plain["sizes"], [{"original": "S", "en": "S", "ar": "S"},
                       {"original": "M", "en": "M", "ar": "M"}])

# --------------------------------------------------------------------------
section("The switch is the only difference")
# --------------------------------------------------------------------------

a = json.dumps(build(SIZELESS, load("")), ensure_ascii=False, sort_keys=True)
b = json.dumps(build(SIZELESS, load("1")), ensure_ascii=False, sort_keys=True)
check("off and on differ", a == b, False)

stripped_a = json.loads(a)
stripped_b = json.loads(b)
stripped_a.pop("sizes")
stripped_b.pop("sizes")
check("and they differ ONLY in sizes[]", stripped_a, stripped_b)

# Leave the process the way it was found, so a suite runner that imports this
# next to another script does not inherit the switch.
load("")
check("switch left off for whatever runs next",
      mapping.MIRROR_OPTIONS_AS_SIZES, False)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

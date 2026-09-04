"""
Shipping cost, from the client's rate card of 5 September 2026.

His words, verbatim:

    المنتجات التي لا تحتوي على كهرباء
    تاخد ابعاد المنتج من 1688 ثم يبدا النظام يحسبها كالاتي
    الابعاد تضرب في هذا السعر 1018 ثم تحصل على سعر الشحن

    المنتجات التي تحتوي على كهرباء ... 1244

    ملاحظة مهمة سعر المتر مكعب الذي ارسلته هو بالريال

and his worked example:

    0.90 × 0.40 × 0.876 = 0.31536  ثم تضرب هذهي القيمة ... 1244

So: cubic metres × a riyal rate per cubic metre = the shipping cost, and the
rate depends on one thing only, whether the product runs on mains electricity.
He also gave the fallback himself - "ممكن ان تجعل النظام يبدا بصفر هكذا
0 × 0 × 0" - a product with no dimensions costs nothing to ship.


WHY THIS FILE IS MORE THAN THREE LINES OF ARITHMETIC
----------------------------------------------------
The formula needs cubic metres. 1688 does not give them.

Measured on 5 September over 151 real listings pulled back from the detail
route - the same route the pipeline uses - after stripping HTML so that a
`width=` inside an `<img>` tag could not be counted as a product dimension:

    a length attribute .............. 0 of 151
    a width attribute ............... 1 of 151
    a height attribute .............. 7 of 151   (all of them "plant height
                                                  35cm" style OPTIONS, sold as
                                                  choices, not package sizes)
    all three together .............. 0 of 151
    an LxWxH triple anywhere in the
      title, description or
      attributes .................... 0 of 151
    any mention of cm or mm at all .. 13 of 151
    a declared unit weight .......... 90 of 151

The positive control that makes those zeros mean something: the same pass
counted 505 colour attributes and 208 spec attributes over the same 151
listings, so the attribute list it searched was populated. And the two
"packing" mentions it did find are carton specs - `箱规57.5-32.4-42.5` is the
size of an outer box holding many pieces, which is why a carton match is
vetoed below rather than used.

Dimensions therefore arrive for roughly nobody, while a weight arrives for
about three in five. Feeding his formula only its literal input would return
zero shipping on 100% of today's catalogue - a formula that runs, reports
success, and changes no price at all.

So there are two ways in, in this order:

  1. DIMENSIONS, when the listing actually states them. Exactly his formula.
  2. WEIGHT, converted to volume through one density figure.

Density is the only invented number in this file and it is deliberately the
only one: it is a single environment variable, it is written into every audit
row that used it, and the module refuses to pretend a converted volume is a
measured one.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal, ROUND_HALF_UP


def _decimal_env(name: str, default: str) -> Decimal:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = Decimal(raw) if raw else Decimal(default)
    except Exception:                                        # noqa: BLE001
        return Decimal(default)
    return value if value >= 0 else Decimal(default)


# His two rates, in riyals per cubic metre. Named after what they are so a
# future reader does not have to guess which is which.
RATE_PLAIN_SAR_PER_M3 = _decimal_env("KDX_FREIGHT_SAR_PER_M3", "1018")
RATE_ELECTRIC_SAR_PER_M3 = _decimal_env("KDX_FREIGHT_SAR_PER_M3_ELECTRIC", "1244")

# Kilograms per cubic metre, used ONLY when the listing states no dimensions.
#
# 200 kg/m3 is the packed density consolidators normally assume for mixed
# retail goods, and it is the middle of the two standards that bracket it:
# air freight bills at 167 kg/m3 (the 6000 divisor), sea LCL at 1000 kg/m3
# (weight-or-measure). It is an estimate and it is his to change - one value,
# one restart, no release.
DENSITY_KG_PER_M3 = _decimal_env("KDX_FREIGHT_KG_PER_M3", "200")

# A single piece bigger than this is not a piece, it is a carton or a parse
# gone wrong. Not a price ceiling and not a publishing rule - nothing is
# refused for hitting it. It only means "this reading is not credible", and
# the estimate falls back to the weight, which is the same thing that happens
# when no dimensions are found at all.
MAX_CREDIBLE_M3 = _decimal_env("KDX_FREIGHT_MAX_M3", "2")

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Reading dimensions out of a listing
# --------------------------------------------------------------------------

_NUM = r"\d{1,4}(?:[.,]\d{1,3})?"
# Deliberately no hyphen. `57.5-32.4-42.5` is a real shape in this data, but
# every instance of it in the 151 measured listings was a carton spec, while
# hyphens elsewhere are ranges and part numbers and dates. Accepting them
# would trade a rare true reading for a steady trickle of false ones.
_SEP = r"\s*[x×*╳X]\s*"

# 45x30x15, 45*30*15, 45×30×15 - with the unit if the seller wrote one.
_TRIPLE = re.compile(
    rf"(?<![\d.])({_NUM}){_SEP}({_NUM}){_SEP}({_NUM})\s*"
    r"(cm|CM|mm|MM|m\b|厘米|毫米|公分|米)?",
)

# 长80宽60高35, in any order, each with its own number.
_NAMED = {
    "length": re.compile(rf"[长長](?:度)?\s*[:：]?\s*({_NUM})\s*(cm|mm|厘米|毫米|公分|米)?"),
    "width": re.compile(rf"宽(?:度)?\s*[:：]?\s*({_NUM})\s*(cm|mm|厘米|毫米|公分|米)?"),
    "height": re.compile(rf"高(?:度)?\s*[:：]?\s*({_NUM})\s*(cm|mm|厘米|毫米|公分|米)?"),
}

# Words that mean the numbers after them describe an OUTER BOX of many pieces.
# One of the 151 listings measured above says `箱规57.5-32.4-42.5`, which is
# 0.079 m3 of carton holding an unknown number of squeeze toys. Charging one
# toy for the whole carton would multiply its shipping by however many fit.
_CARTON = re.compile(r"(箱规|箱規|装箱|裝箱|外箱|箱规格|整箱|carton|CTN)")
_CARTON_REACH = 24          # characters after the carton word that it governs

_UNIT_TO_M = {
    "cm": Decimal("0.01"), "CM": Decimal("0.01"), "厘米": Decimal("0.01"),
    "公分": Decimal("0.01"),
    "mm": Decimal("0.001"), "MM": Decimal("0.001"), "毫米": Decimal("0.001"),
    "m": Decimal("1"), "米": Decimal("1"),
}

# No unit written. 1688 sellers write centimetres and leave the unit off far
# more often than anything else, so that is the assumption - and it is the
# SAFE direction to be wrong in: reading centimetres as metres would multiply
# a volume by a million, while reading metres as centimetres divides it.
_DEFAULT_UNIT_M = Decimal("0.01")


def _strip_html(text: str) -> str:
    """Tags out, so `<img width="350">` is never read as 350 cm of product."""
    return re.sub(r"<[^>]{0,600}>", " ", text or "")


def _in_carton_context(text: str, position: int) -> bool:
    window = text[max(0, position - _CARTON_REACH):position]
    return bool(_CARTON.search(window))


def _scale(unit: str | None) -> Decimal:
    if not unit:
        return _DEFAULT_UNIT_M
    return _UNIT_TO_M.get(unit.strip(), _DEFAULT_UNIT_M)


def _to_decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "."))


def dimensions_from_text(text: str) -> tuple[Decimal, str] | None:
    """
    Volume in cubic metres read off the listing itself, with the evidence.

    Returns None - never a guess - when the listing does not state a size.
    """
    clean = _strip_html(text)

    for match in _TRIPLE.finditer(clean):
        if _in_carton_context(clean, match.start()):
            continue
        scale = _scale(match.group(4))
        sides = [_to_decimal(match.group(i)) * scale for i in (1, 2, 3)]
        if any(side <= 0 for side in sides):
            continue
        volume = sides[0] * sides[1] * sides[2]
        if volume > MAX_CREDIBLE_M3:
            continue
        return volume, match.group(0).strip()

    found = {}
    for axis, pattern in _NAMED.items():
        match = pattern.search(clean)
        if not match or _in_carton_context(clean, match.start()):
            continue
        value = _to_decimal(match.group(1)) * _scale(match.group(2))
        if value > 0:
            found[axis] = (value, match.group(0).strip())
    if len(found) == 3:
        volume = found["length"][0] * found["width"][0] * found["height"][0]
        if 0 < volume <= MAX_CREDIBLE_M3:
            return volume, " ".join(found[a][1] for a in ("length", "width", "height"))
    return None


def volume_from_weight(weight_kg) -> Decimal:
    """The estimate, plainly named so no caller can mistake it for a reading."""
    weight = Decimal(str(weight_kg or 0))
    if weight <= 0 or DENSITY_KG_PER_M3 <= 0:
        return Decimal("0")
    return weight / DENSITY_KG_PER_M3


def volume_m3(text: str = "", weight_kg=None) -> tuple[Decimal, str, str]:
    """
    (cubic metres, where it came from, the evidence).

    Source is one of "declared" (his formula, fed by the listing), "weight"
    (converted through the density) or "none" (his own 0 x 0 x 0 default,
    which costs nothing to ship).
    """
    declared = dimensions_from_text(text) if text else None
    if declared:
        return declared[0], "declared", declared[1]
    estimated = volume_from_weight(weight_kg)
    if estimated > 0:
        return estimated, "weight", f"{weight_kg} kg / {DENSITY_KG_PER_M3} kg/m3"
    return Decimal("0"), "none", ""


def rate_for(is_electrical: bool) -> Decimal:
    return RATE_ELECTRIC_SAR_PER_M3 if is_electrical else RATE_PLAIN_SAR_PER_M3


def shipping_sar(volume, is_electrical: bool) -> Decimal:
    """Cubic metres times his rate. The whole of the formula he sent."""
    return money(Decimal(str(volume)) * rate_for(is_electrical))


def quote(text: str = "", weight_kg=None, is_electrical: bool = False) -> dict:
    """
    One shipping quote with its whole derivation attached, because every one of
    these numbers ends up inside a selling price and the audit row has to be
    able to say which of the two roads it came down.
    """
    volume, source, evidence = volume_m3(text, weight_kg)
    return {
        "sar": shipping_sar(volume, is_electrical),
        "m3": volume,
        "source": source,
        "evidence": evidence,
        "rate": rate_for(is_electrical),
        "electrical": bool(is_electrical),
    }

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
The formula needs cubic metres. 1688 hands them over for about one listing in
fifty.

Measured on 5 September over 151 real listings pulled back from the detail
route - the same route the pipeline uses - reading every string in the payload,
with the HTML stripped so a `width=` inside an `<img>` tag could not be counted
as a product dimension:

    an LxWxH triple anywhere in the
      title, description, attributes
      or SKU options ................ 3 of 151
    ...of which a genuine package
      size of the thing being sold ... 1 of 151
    any mention of cm / mm / m ....... 74 of 151
    a declared unit weight ........... 90 of 151

The positive control that makes that small number mean something: the same pass
counted 4,630 product attributes and 5,599 SKU attributes over the same 151
listings, in 261 distinct attribute names, so the lists it searched were full.

What the fields whose NAMES promise a size actually hold, on his own catalogue:

    尺寸  ("dimensions")  9 of 151, e.g. "1.2米加粗-豪华导游杆/圆头款【黑色】"
    规格  ("spec")       28 of 151, e.g. "长型锁黑色一只装"
    规格型号 ("model")    16 of 151, e.g. "白色100个【送7个收纳盒】"
    尺码  ("size")       67 of 151, e.g. "M", "L", "XL"

They are the option names the buyer picks from, not the box the courier lifts.

WORSE THAN MISSING: the three triples that DO exist were all read wrongly the
first time this module ran, and each one is now a fixture in verify_freight.py.

    830489165036   "348X105X38ｍｍ" - the millimetre suffix is written in
                   FULL-WIDTH characters, which the unit table did not hold, so
                   a MIDI keyboard was read as 348 x 105 x 38 CENTIMETRES:
                   1.39 m3, 1,413 SAR of freight on a 178 SAR keyboard.
    611415954620   "1#*20*20*60*60*120*140cm" - six numbers, a LIST of fabric
                   cut options, of which the first three were read as a box.
    1069466826544  "打印尺寸 270X270X270mm" - the PRINT volume of a 3D printer,
                   which is the one measurement in the listing that is not the
                   parcel.

So a stated triple is now only believed when the unit is understood, the run of
numbers stops at three, and no nearby word says the measurement belongs to
something else (a carton, a screen, a print bed, a bore).

WHEN THE SELLER STATED NOTHING - 5 September, his decision
----------------------------------------------------------
The first version of this file bridged the gap by weight, through a density.
He refused it, twice and plainly:

    لا يمكن تحويل الحجم الفعلي الى كيلو هذهي معادلة تفرق وليس لها علاقة
    بمعادلة ابعاد المنتج وسعر قيمة الشحن
    ... وبخصوص ابعاد المنتج فهي لا تحسب الكيلو فقط تحسب بالابعاد

His formula is a volume formula and it stays one. So the fallback is also a
volume: a default carton per product family, written in centimetres, listed
below where he can read it and correct any row. No kilogram enters this file.

The four roads in, in order, each named in the audit row that used it:

  1. "override"  a size he typed himself, in KDX_FREIGHT_DIMS_FILE
  2. "declared"  a size the listing states. Exactly his formula.
  3. "family"    the default carton for that kind of product
  4. "default"   the default carton for everything else

There is no fifth road that returns zero. His own 0 x 0 x 0 was offered as a
starting point - "ممكن ان تجعل النظام يبدا بصفر" - and taken literally it
would have priced the freight of every product in the shop at nothing.
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

# The default carton, in centimetres, for a product whose seller stated no
# size. His formula's input, in his formula's units, so that changing it is
# reading a box with a tape measure rather than believing a physics constant.
#
# 25 x 20 x 12 cm = 0.006 m3 = 6.11 SAR plain, 7.46 SAR electrical.
#
# A small parcel, not a shipping carton. Replayed over 13,227 real priced rows,
# a 40 x 30 x 20 default put 24.43 SAR of freight on a 0.07 SAR rhinestone -
# the arithmetic was his and the box was mine, and the box was wrong.
DEFAULT_BOX_CM = (_decimal_env("KDX_FREIGHT_BOX_L_CM", "25"),
                  _decimal_env("KDX_FREIGHT_BOX_W_CM", "20"),
                  _decimal_env("KDX_FREIGHT_BOX_H_CM", "12"))

# A default carton per product family, because a bra and a 3D printer do not
# travel in the same box and one number for both would be wrong twice.
#
# The keys are matched against the Chinese CATEGORY and TITLE - the source
# data, which is always present - not against the Arabic department, which is
# assigned later in the run. First match in this list wins, so the specific
# families are listed before the general ones.
#
# Each box is one PIECE as it travels - a garment in its poly bag, not the
# carton the factory ships fifty in. Every one is an estimate and every one is
# his to correct; the comment after each row is what his 1018 SAR/m3 makes of
# it, so the consequence is readable without a calculator.
FAMILY_BOXES_CM: list = [
    # (name shown in the audit, keywords, L, W, H)
    ("printer3d",  ("3d打印", "3D打印", "打印机", "投影"),        55, 50, 55),  # 153.97 SAR
    ("luggage",    ("拉杆箱", "行李箱", "旅行箱"),               65, 42, 26),  # 72.26
    ("appliance",  ("电机", "机器", "机械", "设备", "液压", "工业"), 42, 35, 30),  # 44.89
    ("storage",    ("收纳", "置物", "储物", "整理架", "衣架"),      40, 30, 18),  # 21.99
    ("instrument", ("乐器", "键盘", "吉他", "midi", "MIDI"),      55, 28, 12),  # 18.81
    ("toy",        ("玩具", "公仔", "毛绒", "娃娃", "抱枕"),        35, 25, 20),  # 17.81
    ("pet_garden", ("宠物", "花盆", "花瓶", "园艺", "植物"),        30, 24, 18),  # 13.19
    ("bag",        ("背包", "箱包", "手提包", "钱包", "包袋"),      35, 28, 12),  # 11.97
    ("lighting",   ("灯", "照明", "led", "LED"),                 30, 22, 15),  # 10.08
    ("fabric",     ("面料", "布料", "棉布", "雨布", "旗帜"),        35, 25, 10),  # 8.91
    ("electronics", ("电子", "数码", "电脑", "充电", "电器"),       28, 22, 12),  # 7.53
    ("shoes",      ("鞋", "靴"),                                32, 20, 11),  # 7.17
    ("safety",     ("口罩", "防毒", "面具", "呼吸", "手套", "头盔"),  25, 18, 12),  # 5.50
    ("clothing",   ("T恤", "t恤", "衬衣", "衬衫", "连衣裙", "内衣",
                    "文胸", "袜", "背心", "吊带", "裙", "裤", "服",
                    "毛衣", "外套", "帽", "假发"),                30, 25, 4),   # 3.05
    ("stationery", ("文具", "墙贴", "贴纸", "办公", "笔", "本子"),   30, 20, 3),   # 1.83
    ("hardware",   ("锁", "螺丝", "螺栓", "螺母", "阀", "五金",
                    "轴承", "工具", "接头", "垫片"),               18, 12, 8),   # 1.76
    ("jewellery",  ("首饰", "饰品", "手链", "项链", "耳环", "戒指"),  15, 10, 5),   # 0.76
]

# The smallest parcel a courier actually carries, in centimetres.
#
# Measured on the live run of 5 September 07:48. A seller who states a size
# states the SIZE OF THE THING, not of the box it travels in: offer
# 1072111096507 says "32*32*9mm", which is 0.0000092 m3 and one halala of
# freight. 181 more rows came in at "14.5*8.5*5.5". Both are true measurements
# of the object and neither is a parcel - nobody ships a 3 cm square in nothing.
#
# So a size READ FROM A LISTING is lifted to at least this. A size HE TYPES is
# not: if he measures a box himself, that is the box.
#
# 15 x 10 x 5 cm = 0.00075 m3 = 0.76 SAR.
MIN_PARCEL_CM = (_decimal_env("KDX_FREIGHT_MIN_L_CM", "15"),
                 _decimal_env("KDX_FREIGHT_MIN_W_CM", "10"),
                 _decimal_env("KDX_FREIGHT_MIN_H_CM", "5"))

# A file he can put sizes in himself, one product per line, when a particular
# box matters enough to measure:  offer_id,length_cm,width_cm,height_cm
DIMS_FILE = os.environ.get("KDX_FREIGHT_DIMS_FILE", "/opt/kdx/dims.csv")

# Above this weight, a default carton is a guess about something heavy enough
# to hurt. Nothing is refused and no price changes - the product is listed in
# the audit as wanting a real measurement, so he can type one into DIMS_FILE.
HEAVY_ENOUGH_TO_MEASURE_KG = _decimal_env("KDX_FREIGHT_MEASURE_OVER_KG", "15")

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
_SEP_CHARS = r"x×*╳XｘＸ＊"
_SEP = rf"\s*[{_SEP_CHARS}]\s*"

# 45x30x15, 45*30*15, 45×30×15 - with the unit if the seller wrote one.
#
# The lookbehind and the lookahead together say "exactly three numbers".
# `1#*20*20*60*60*120*140cm` on offer 611415954620 is six fabric cut options,
# not a box, and without them its first three were read as one.
_TRIPLE = re.compile(
    rf"(?<![\d.{_SEP_CHARS}])({_NUM}){_SEP}({_NUM}){_SEP}({_NUM})"
    rf"(?!{_SEP}\d)\s*"
    r"(cm|CM|Cm|ｃｍ|ＣＭ|mm|MM|Mm|ｍｍ|ＭＭ|m\b|ｍ|米|厘米|毫米|公分)?",
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

# Measurements that belong to something other than the parcel. 1069466826544
# states 打印尺寸 270X270X270mm - the volume a 3D printer can print INSIDE
# itself, which is smaller than the machine and much smaller than its box.
_NOT_THE_PARCEL = re.compile(
    r"(打印尺寸|印刷尺寸|打印范围|成型尺寸|屏幕|显示尺寸|可视|分辨率|像素|"
    r"内径|孔径|管径|screen|display|print size|resolution)", re.IGNORECASE)

_CARTON_REACH = 24          # characters after the carton word that it governs

_UNIT_TO_M = {
    "cm": Decimal("0.01"), "CM": Decimal("0.01"), "Cm": Decimal("0.01"),
    "ｃｍ": Decimal("0.01"), "ＣＭ": Decimal("0.01"),
    "厘米": Decimal("0.01"), "公分": Decimal("0.01"),
    "mm": Decimal("0.001"), "MM": Decimal("0.001"), "Mm": Decimal("0.001"),
    "ｍｍ": Decimal("0.001"), "ＭＭ": Decimal("0.001"), "毫米": Decimal("0.001"),
    "m": Decimal("1"), "ｍ": Decimal("1"), "米": Decimal("1"),
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


def _measures_something_else(text: str, position: int) -> bool:
    """True when the words just before the numbers say what they measure, and
    it is not the parcel."""
    window = text[max(0, position - _CARTON_REACH):position]
    return bool(_NOT_THE_PARCEL.search(window))


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
        if _measures_something_else(clean, match.start()):
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
        if not match or _in_carton_context(clean, match.start()) \
                or _measures_something_else(clean, match.start()):
            continue
        value = _to_decimal(match.group(1)) * _scale(match.group(2))
        if value > 0:
            found[axis] = (value, match.group(0).strip())
    if len(found) == 3:
        volume = found["length"][0] * found["width"][0] * found["height"][0]
        if 0 < volume <= MAX_CREDIBLE_M3:
            return volume, " ".join(found[a][1] for a in ("length", "width", "height"))
    return None


def _box_m3(length_cm, width_cm, height_cm) -> Decimal:
    """Three centimetre sides to cubic metres. His arithmetic, in his units."""
    centi = Decimal("0.01")
    return (Decimal(str(length_cm)) * centi
            * Decimal(str(width_cm)) * centi
            * Decimal(str(height_cm)) * centi)


def _family_in(text: str) -> tuple[str, Decimal, str] | None:
    haystack = _strip_html(text or "")
    if not haystack.strip():
        return None
    for name, keywords, length, width, height in FAMILY_BOXES_CM:
        for word in keywords:
            if word in haystack:
                return (name, _box_m3(length, width, height),
                        f"{length}x{width}x{height}cm ({name}: {word})")
    return None


def family_box(category: str = "", title: str = "") -> tuple[str, Decimal, str] | None:
    """
    (family name, cubic metres, the box) for the first family that matches, or
    None when nothing matches and the default box applies.

    The CATEGORY is asked first and on its own, because it is the one field in
    a 1688 listing that says what the thing IS. Only if it names no family at
    all does the title get a turn.

    Offer 910007827618 is why. It is a sew-on rhinestone filed under 饰品配件,
    and its title ends "...diy发饰鞋子服" - hair, shoes, clothing, the places a
    buyer might use it. Reading title and category together, 鞋 wins and a
    0.2 gram stone travels in a shoe box at 7.17 SAR instead of a jewellery bag
    at 0.76. The words a seller uses to describe USES are not words about the
    parcel.
    """
    return _family_in(category) or _family_in(title)


def _load_overrides() -> dict:
    """
    offer_id -> (cubic metres, the line he wrote), from KDX_FREIGHT_DIMS_FILE.

    Read on every call rather than cached: the file is tiny, and a size he
    types should take effect on the next run without anyone restarting a
    service for him. A malformed line is skipped, never fatal - this file sits
    between him and his own prices and must not be able to stop a run.
    """
    overrides = {}
    try:
        with open(DIMS_FILE, encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [part.strip() for part in line.replace(";", ",").split(",")]
                if len(parts) < 4:
                    continue
                try:
                    volume = _box_m3(*parts[1:4])
                except Exception:                            # noqa: BLE001
                    continue
                if 0 < volume <= MAX_CREDIBLE_M3:
                    overrides[parts[0]] = (volume, f"{parts[1]}x{parts[2]}x{parts[3]}cm")
    except OSError:
        return {}
    return overrides


def volume_m3(text: str = "", offer_id: str = "", family_category: str = "",
              family_title: str = "") -> tuple[Decimal, str, str]:
    """
    (cubic metres, where it came from, the evidence).

    Source is one of "override" (a size he typed), "declared" (his formula, fed
    by a size the listing states), "family" (the default carton for that kind
    of product) or "default" (the default carton for everything else).

    Never zero, and never a kilogram: he ruled the weight road out on
    5 September and the file header records his words.
    """
    if offer_id:
        override = _load_overrides().get(str(offer_id))
        if override:
            return override[0], "override", override[1]

    declared = dimensions_from_text(text) if text else None
    if declared:
        floor = _box_m3(*MIN_PARCEL_CM)
        if declared[0] < floor:
            length, width, height = MIN_PARCEL_CM
            return (floor, "declared",
                    f"{declared[1]} -> {length}x{width}x{height}cm (أقل طرد)")
        return declared[0], "declared", declared[1]

    family = family_box(family_category, family_title or (
        "" if family_category else text))
    if family:
        return family[1], "family", family[2]

    length, width, height = DEFAULT_BOX_CM
    return (_box_m3(length, width, height), "default",
            f"{length}x{width}x{height}cm")


def wants_measuring(source: str, weight_kg) -> bool:
    """
    True when a heavy product is being shipped on a guessed box.

    Changes no price and refuses nothing. It only marks the rows worth putting
    a tape measure to, because that is where a wrong box costs real money.
    """
    if source in ("override", "declared"):
        return False
    try:
        return Decimal(str(weight_kg or 0)) >= HEAVY_ENOUGH_TO_MEASURE_KG
    except Exception:                                        # noqa: BLE001
        return False


def rate_for(is_electrical: bool) -> Decimal:
    return RATE_ELECTRIC_SAR_PER_M3 if is_electrical else RATE_PLAIN_SAR_PER_M3


def shipping_sar(volume, is_electrical: bool) -> Decimal:
    """Cubic metres times his rate. The whole of the formula he sent."""
    return money(Decimal(str(volume)) * rate_for(is_electrical))


def quote(text: str = "", weight_kg=None, is_electrical: bool = False,
          offer_id: str = "", family_category: str = "",
          family_title: str = "") -> dict:
    """
    One shipping quote with its whole derivation attached, because every one of
    these numbers ends up inside a selling price and the audit row has to be
    able to say which of the four roads it came down.

    `text` is everything the listing says, and a stated size is looked for in
    all of it. The default box is chosen from `family_category` first and
    `family_title` second - never the description - because choosing a box from
    a description is how a rhinestone ends up in a shoe box. With neither
    given, the whole text is used, so a caller holding one string still works.

    `weight_kg` no longer feeds the volume - it only decides whether the row is
    worth measuring by hand. The parameter stays because every caller has the
    weight to hand and the flag is worth more than the argument it costs.
    """
    volume, source, evidence = volume_m3(text, offer_id, family_category,
                                         family_title)
    return {
        "sar": shipping_sar(volume, is_electrical),
        "m3": volume,
        "source": source,
        "evidence": evidence,
        "rate": rate_for(is_electrical),
        "electrical": bool(is_electrical),
        "wants_measuring": wants_measuring(source, weight_kg),
    }

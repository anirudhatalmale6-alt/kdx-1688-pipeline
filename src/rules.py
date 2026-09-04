"""
KDX decision engine.

Every rule in here comes straight from the client's written specification.
Each product variant goes through the same path:

    exclusions -> electrical spec -> price (compare or mark up) -> shipping flag

and whatever happens, an audit record is produced explaining the outcome.
Nothing is ever published without a recorded reason.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum

import liquids

# --------------------------------------------------------------------------
# Configuration derived from the client's rules
# --------------------------------------------------------------------------

# Undercut applied to a competitor's price when the same product is matched.
# (upper bound in SAR inclusive, discount fraction)
UNDERCUT_BANDS = [
    (Decimal("100"), Decimal("0.03")),
    (Decimal("500"), Decimal("0.02")),
    (None, Decimal("0.01")),
]

# Margin applied to our landed cost when the product is NOT found anywhere.
# (upper bound in SAR inclusive, markup fraction)
MARKUP_BANDS = [
    (Decimal("50"), Decimal("0.30")),
    (Decimal("100"), Decimal("0.27")),
    (Decimal("200"), Decimal("0.24")),
    (Decimal("350"), Decimal("0.20")),
    (Decimal("500"), Decimal("0.17")),
    (Decimal("750"), Decimal("0.15")),
    (Decimal("1000"), Decimal("0.13")),
    (None, Decimal("0.10")),
]

# Weight boundary that decides the shipping flag, in kilograms.
LIGHT_MAX_KG = Decimal("2")

# The cheapest thing worth putting in a shop.
#
# Not one of the client's rules - it comes from looking at what the first real
# night actually published. A glass decorative stone went live at 0.08 SAR, and
# 320 of the 3,830 offers waiting cost under one yuan. 1688 quotes a wholesale
# price per piece, so a fraction of a riyal is a truthful conversion of a real
# price and still nonsense in a retail shop: the payment fee alone is larger
# than the sale.
#
# Three riyals was a placeholder while the question was with him. He answered
# on 2026-08-30 - "اجعلها الحد الادنى 0.01" - so the floor is now his number,
# and it is deliberately low enough to change almost nothing: it exists only to
# stop a product priced at literally zero. KDX_MIN_PRICE_SAR=0 turns it off
# entirely.
MIN_PRICE_SAR = Decimal(os.environ.get("KDX_MIN_PRICE_SAR", "0.01"))

# A match is only trusted at or above this score.
MATCH_THRESHOLD = Decimal("95")

COMPARISON_PLATFORMS = ["Temu", "SHEIN", "AliExpress", "Amazon", "Noon"]

# Mains specification the client accepts for anything electrical.
REQUIRED_VOLTAGE = re.compile(r"\b220\s*v\b", re.IGNORECASE)
# Chargers and adapters almost never print "220V". They print a range -
# "100-240V 50/60Hz" - which runs on Saudi mains perfectly. Reading only the
# literal 220 would reject the whole accessory aisle for saying it too well.
# Not \b before the first number: listings write "AC100-240V", and a word
# boundary between "C" and "1" does not exist.
VOLTAGE_RANGE = re.compile(r"(?<!\d)(\d{2,3})\s*[-~–—到至]\s*(\d{2,3})\s*v\b", re.IGNORECASE)
# Any frequency at all, versus one we can sell. The pair is what lets "not
# stated" be told apart from "stated and unsuitable".
ANY_FREQUENCY = re.compile(r"\b\d{2,3}\s*(?:/\s*\d{2,3}\s*)?hz\b", re.IGNORECASE)
ACCEPTED_FREQUENCY = re.compile(r"\b(?:50|60)\s*(?:/\s*(?:50|60)\s*)?hz\b", re.IGNORECASE)

# Signals that a product is mains-powered at all.
ELECTRICAL_HINTS = re.compile(
    r"(220v|110v|\bhz\b|\bwatt\b|\bwatts\b|\bplug\b|电源|插头|电压|功率|额定|charger|adapter|"
    r"محول|شاحن|كهرب)",
    re.IGNORECASE,
)

# Categories the client refuses to sell. Kept as explicit word lists so the
# reason recorded in the audit log names the exact term that triggered it.
BANNED_TERMS = {
    "sexual": ["sex toy", "vibrator", "adult toy", "erotic", "情趣", "成人用品", "دلدو", "جنسي"],
    "religious": ["quran", "bible", "crucifix", "rosary", "prayer mat", "islamic calligraphy",
                  "佛像", "十字架", "مصحف", "قرآن", "سجادة صلاة", "تعويذة"],
    "weapons": ["gun", "rifle", "pistol", "airsoft", "ammunition", "bullet", "taser", "knuckle",
                "弹药", "枪", "سلاح", "ذخيرة", "مسدس"],
    "drugs": ["bong", "grinder weed", "cannabis", "narcotic", "大麻", "مخدرات"],
    "tobacco": ["vape", "e-cigarette", "e-liquid", "shisha", "hookah", "nicotine", "电子烟",
                "سجائر", "شيشة", "نيكوتين"],
    "counterfeit": ["replica", "1:1 copy", "aaa quality copy", "fake brand", "高仿", "تقليد"],
    # 3 September, his words: "استبعاد المنتجات التي تحتوي على وجبات سواء كانت
    # الى الأنسان او كانت الى الحيوان" and "استبعاد المنتجات التي تحتوي على طحين
    # او مواد كيماوية او كيميائية".
    #
    # The department gate in catalog.py already stops 食品酒水 and 化工, which
    # he switched off on 2 September. These terms exist for what the department
    # gate CANNOT see: edible goods sitting inside a department he sells. The
    # freeze-dried chicken cat treats published on 3 September are filed under
    # 宠物及园艺 - pets and gardening, a department he keeps - so only the
    # product's own words can catch them.
    #
    # The Arabic entries here are deliberately PHRASES, never bare nouns, and
    # that is a measurement not a preference. Scored over the 183 products
    # assembled so far, "دقيق" caught a German precision pressure valve - the
    # word is flour AND "precise" - and the existing "تقليد" caught four
    # traditional costumes, because تقليدي "traditional" contains it. A bare
    # noun in Arabic is a different word with the prefix on.
    "food": ["零食", "食品", "干货", "罐头", "夹心饼", "糖果", "巧克力",
             "speciality food", "snack food", "مواد غذائية", "وجبات جاهزة"],
    # 4 September: goat milk powder for cats and dogs reached the shop at 22:30
    # on the 3rd, after this list already existed. Nothing here described it -
    # it is not 粮, not 零食, not 罐头 - so the powders and supplements are named
    # now. The category gate in catalog.FOOD_TOKENS is the wider net and blocks
    # 狗狗保健品 and 猫猫保健品 outright; these words are what catches the same
    # thing filed somewhere else.
    "animal_food": ["猫粮", "狗粮", "宠物零食", "宠物食品", "冻干鸡", "冻干猫",
                    "猫条", "猫罐头", "狗罐头", "饲料", "营养膏", "pet treat",
                    "pet food", "cat treat", "dog treat", "cat food", "dog food",
                    "羊奶粉", "宠物奶粉", "猫奶粉", "狗奶粉", "幼犬奶粉",
                    "宠物益生菌", "猫咪益生菌", "狗狗益生菌", "宠物营养品",
                    "goat milk powder", "puppy milk", "kitten milk",
                    "طعام قطط", "طعام كلاب", "أعلاف", "حليب الماعز"],
    "flour": ["面粉", "小麦粉", "玉米粉", "淀粉", "flour", "طحين قمح", "دقيق قمح"],
    "chemicals": ["化工原料", "化学试剂", "工业原料", "合成橡胶", "氯丁橡胶",
                  "丁苯橡胶", "丁腈橡胶", "橡胶原料", "塑料颗粒", "树脂原料",
                  "溶剂", "增塑剂", "固化剂", "催化剂",
                  "chemical raw", "synthetic rubber", "مواد كيميائية", "كيماوية"],
}

# Words that contain a banned term but are not the banned thing. Checked first,
# exactly as catalog.SAFE_PHRASES is, and for the same reason: 食品级硅胶 is
# food-GRADE silicone, a lunch box, and "食品" would delete the whole aisle.
# 橡胶 alone is not here because it is not banned alone - a 3M rubber respirator
# published on 2 September is protective equipment, and only the named synthetic
# compounds are the raw chemical he means.
BANNED_SAFE_PHRASES = (
    "食品级", "食品用", "食品接触", "食品密封", "食品保鲜", "食品收纳",
    "食品夹", "食品袋", "食品盒", "零食盒", "零食收纳", "零食夹",
    "宠物食品盒", "宠物碗", "喂食器", "food grade", "food-grade",
    "food container", "food storage", "درجة غذائية",
)


class Decision(str, Enum):
    PUBLISH = "publish"
    UPDATE = "update"
    REJECT = "reject"


def money(value) -> Decimal:
    """Round to 2 decimals the way an invoice would."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

@dataclass
class Variant:
    """One purchasable option on a 1688 listing (a colour/size combination)."""

    sku_id: str
    attributes: dict            # {"color": "أسود", "size": "XL"}
    price_cny: Decimal          # price of THIS variant, not the listing minimum
    stock: int
    weight_kg: Decimal


@dataclass
class Product:
    offer_id: str
    title_zh: str
    description_zh: str
    images: list
    variants: list
    category_path: str = ""
    specifications: dict = field(default_factory=dict)

    def searchable_text(self) -> str:
        spec_text = " ".join(f"{k} {v}" for k, v in self.specifications.items())
        return " ".join([self.title_zh, self.description_zh, self.category_path, spec_text])


@dataclass
class CompetitorHit:
    platform: str
    price_sar: Decimal
    match_score: Decimal        # 0-100
    url: str = ""
    matched_variant: str = ""   # which variant this hit corresponds to


@dataclass
class AuditRecord:
    offer_id: str
    sku_id: str
    decision: str
    reason_code: str
    reason_ar: str
    cost_sar: str = ""
    matched_platform: str = ""
    match_score: str = ""
    competitor_price_sar: str = ""
    final_price_sar: str = ""
    pricing_basis: str = ""
    requires_shipping: str = ""
    shipping_type: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Individual rules
# --------------------------------------------------------------------------

def find_banned_term(product: Product) -> tuple[str, str] | None:
    """
    Return (category, term) if the listing hits an excluded category.

    A safe phrase is only allowed to excuse the term it CONTAINS. Removing all
    of them from the text before searching would let "食品级硅胶食品" through by
    deleting the very word that condemns it, so each term is checked against a
    text with only the safe phrases that cover that term taken out.
    """
    haystack = product.searchable_text().lower()
    for category, terms in BANNED_TERMS.items():
        for term in terms:
            needle = term.lower()
            if needle not in haystack:
                continue
            masked = haystack
            for phrase in BANNED_SAFE_PHRASES:
                if needle in phrase.lower():
                    masked = masked.replace(phrase.lower(), " ")
            if needle in masked:
                return category, term
    return None


def is_electrical(product: Product) -> bool:
    return bool(ELECTRICAL_HINTS.search(product.searchable_text()))


def runs_on_220(text: str) -> bool:
    """
    True when the listing says the product works on Saudi mains.

    Client, 29 August: "the accepted one is 220V only, we do not need 110V".
    So 110V alone is refused. But a product that states a range covering 220 -
    100-240V, 110~220V - does run on 220V, and refusing it would throw away
    most chargers and adapters, which is the opposite of what he asked for.
    """
    if REQUIRED_VOLTAGE.search(text):
        return True
    for low, high in VOLTAGE_RANGE.findall(text):
        if int(low) <= 220 <= int(high):
            return True
    return False


def has_accepted_mains_spec(product: Product) -> bool:
    """
    Client rule, as he revised it on 29 August: 220V is required, a stated
    frequency is not.

    The original rule demanded both. It was put to him because most 1688
    listings state the voltage and never mention the frequency at all, so the
    strict reading rejected nearly every electrical product. His answer: accept
    a product that states 220V and says nothing about frequency.

    Silent is still not the same as wrong. A listing that does state a frequency
    we cannot sell - 400Hz industrial equipment - stays rejected, otherwise
    "did not mention it" and "mentioned it and it is unusable in Saudi Arabia"
    would be treated as the same thing.
    """
    text = product.searchable_text()
    if not runs_on_220(text):
        return False
    if ANY_FREQUENCY.search(text):
        return bool(ACCEPTED_FREQUENCY.search(text))
    return True


def shipping_flag(weight_kg: Decimal) -> tuple[str, str]:
    """
    Client rule, stated twice and kept verbatim:
      0 - 2 kg  -> requires shipping YES -> fast shipping
      over 2 kg -> requires shipping NO  -> free shipping
    No shipping fee is ever charged to the customer either way.
    """
    if weight_kg <= LIGHT_MAX_KG:
        return "yes", "fast"
    return "no", "free"


def undercut_price(competitor_price: Decimal) -> tuple[Decimal, Decimal]:
    for upper, discount in UNDERCUT_BANDS:
        if upper is None or competitor_price <= upper:
            return money(competitor_price * (Decimal("1") - discount)), discount
    raise AssertionError("unreachable: last band has no upper bound")


def marked_up_price(cost_sar: Decimal) -> tuple[Decimal, Decimal]:
    for upper, markup in MARKUP_BANDS:
        if upper is None or cost_sar <= upper:
            return money(cost_sar * (Decimal("1") + markup)), markup
    raise AssertionError("unreachable: last band has no upper bound")


def best_match(hits: list, variant: Variant) -> CompetitorHit | None:
    """
    Cheapest competitor offer that is genuinely the SAME variant.

    Two guards the client asked for explicitly:
      - only hits scoring >= 95 count as the same product;
      - a hit is only usable against the variant it was matched to, so the
        cheap 1688 colour is never compared against an expensive rival colour.
    """
    eligible = [
        hit for hit in hits
        if hit.match_score >= MATCH_THRESHOLD
        and (not hit.matched_variant or hit.matched_variant == variant.sku_id)
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda hit: hit.price_sar)


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------

@dataclass
class PricingResult:
    variant: Variant
    decision: Decision
    audit: AuditRecord
    final_price_sar: Decimal | None = None


class Engine:
    def __init__(self, cny_to_sar: Decimal, existing_skus: set | None = None):
        self.cny_to_sar = Decimal(str(cny_to_sar))
        self.existing_skus = existing_skus or set()

    def landed_cost_sar(self, variant: Variant) -> Decimal:
        """
        The 1688 price in riyals. NOT a landed cost, despite the name.

        There is no freight term in here, because nobody has ever given one:
        no rate card, no per-kilo figure, no volumetric divisor. Every "cost"
        in this file is therefore the goods alone, and every guard built on it
        - the loss guard above, the margin bands - protects the goods price and
        nothing else.

        That is exactly the hole the client described on 3 September, in his
        reason for wanting the comparison at all:

          "المنتجات الكبيرة هي تحسب بالابعاد وبعض الاحيان يكون سعر الشحن اعلى من
           سعر المنتج"

        A bulky, light product - a lampshade, a plastic storage bin - is charged
        on volume, and its freight can exceed everything counted here. The
        comparison is currently the only thing standing in for that, which is
        why he will not publish a heavy product without it: a rival's shelf
        price has the shipping already inside it.

        Filling this in needs two numbers from him, not from me: what he pays
        per real kilo and per volumetric kilo. Guessing them would put an
        invented figure underneath every price in the shop.
        """
        return money(variant.price_cny * self.cny_to_sar)

    def evaluate(self, product: Product, hits_by_variant: dict) -> list:
        """Run every variant of one listing through the rules."""
        banned = find_banned_term(product)
        if banned:
            category, term = banned
            return [
                self._reject(product, variant, "banned_category",
                             f"فئة ممنوعة ({category}) - الكلمة المطابقة: {term}")
                for variant in product.variants
            ]

        # Client, 1 September: block anything containing liquids, completely.
        # Kept as its own rule rather than another BANNED_TERMS entry because
        # the reason has to be readable in the log - he will want to see which
        # word did it, and to overrule a word that is catching too much.
        liquid = liquids.find_liquid_term(product.searchable_text())
        if liquid:
            reason, term = liquid
            return [
                self._reject(product, variant, "contains_liquid",
                             f"يحتوي على سوائل ({reason}) - الكلمة المطابقة: {term}")
                for variant in product.variants
            ]

        if is_electrical(product) and not has_accepted_mains_spec(product):
            return [
                self._reject(product, variant, "mains_spec",
                             "منتج كهربائي بمواصفات غير مقبولة - المطلوب 220 فولت، "
                             "وإذا ذُكر التردد فيجب أن يكون 50 أو 60 هرتز")
                for variant in product.variants
            ]

        return [
            self._evaluate_variant(product, variant, hits_by_variant.get(variant.sku_id, []))
            for variant in product.variants
        ]

    def _evaluate_variant(self, product: Product, variant: Variant, hits: list) -> PricingResult:
        if variant.stock <= 0:
            return self._reject(product, variant, "out_of_stock",
                                "غير متوفر في 1688 - لا يتم النشر")

        cost = self.landed_cost_sar(variant)
        requires_shipping, shipping_type = shipping_flag(variant.weight_kg)
        match = best_match(hits, variant)

        if match:
            price, discount = undercut_price(match.price_sar)
            # The client's loss guard: undercutting must never take us below cost.
            #
            # What happens THEN was his answer of 3 September, and it differs by
            # shipping type:
            #
            #   "اذا تمت المقارنة في 5 التطبيقات وكلها تبيع المنتج بخسارة فيتطبق
            #    هامش الربح الذي ارسلته لك - هذا معتمد في المنتجات الصغيرة التي
            #    تحتوي على الشحن السريع"
            #
            # So a light product is not thrown away for being cheaper abroad; it
            # falls through to the margin below, priced from our own cost. A
            # heavy one still stops here, because heavy is where he says the
            # danger is: shipping is charged on dimensions and can exceed the
            # goods, so a rival sitting under our cost is the warning itself.
            if price <= cost and variant.weight_kg > LIGHT_MAX_KG:
                return self._reject(
                    product, variant, "would_sell_at_loss",
                    f"السعر بعد الخصم ({price}) أقل من التكلفة ({cost}) - لا يتم النشر",
                    cost=cost, match=match, requires_shipping=requires_shipping,
                    shipping_type=shipping_type,
                )
            if price <= cost:
                price, markup = marked_up_price(cost)
                if price < MIN_PRICE_SAR:
                    return self._reject(
                        product, variant, "below_min_price",
                        f"السعر النهائي ({price} ريال) أقل من الحد الأدنى "
                        f"({MIN_PRICE_SAR} ريال) - لا يتم النشر",
                        cost=cost, match=match, requires_shipping=requires_shipping,
                        shipping_type=shipping_type,
                    )
                basis = (f"كل المنصات تبيع بأقل من التكلفة - "
                         f"التكلفة زائد هامش {int(markup * 100)}%")
                # The rival is kept on the row even though it did not set the
                # price: it is the evidence for why the margin was used, and
                # without it this is indistinguishable from a product nobody
                # sells. Its own code so the two can be counted apart.
                return self._accept(product, variant, price, basis, cost, match,
                                    requires_shipping, shipping_type,
                                    reason_code="margin_rivals_below_cost")
            if price < MIN_PRICE_SAR:
                return self._reject(
                    product, variant, "below_min_price",
                    f"السعر النهائي ({price} ريال) أقل من الحد الأدنى "
                    f"({MIN_PRICE_SAR} ريال) - لا يتم النشر",
                    cost=cost, match=match, requires_shipping=requires_shipping,
                    shipping_type=shipping_type,
                )
            basis = f"سعر {match.platform} ناقص {int(discount * 100)}%"
            return self._accept(product, variant, price, basis, cost, match,
                                requires_shipping, shipping_type)

        # Not found on any comparison platform.
        if variant.weight_kg > LIGHT_MAX_KG:
            return self._reject(
                product, variant, "heavy_and_unmatched",
                f"الوزن {variant.weight_kg} كجم أكبر من 2 كجم ولم يُعثر عليه في أي منصة مقارنة",
                cost=cost, requires_shipping=requires_shipping, shipping_type=shipping_type,
            )

        price, markup = marked_up_price(cost)
        if price < MIN_PRICE_SAR:
            return self._reject(
                product, variant, "below_min_price",
                f"السعر النهائي ({price} ريال) أقل من الحد الأدنى "
                f"({MIN_PRICE_SAR} ريال) - لا يتم النشر",
                cost=cost, requires_shipping=requires_shipping,
                shipping_type=shipping_type,
            )
        basis = f"التكلفة زائد هامش {int(markup * 100)}%"
        return self._accept(product, variant, price, basis, cost, None,
                            requires_shipping, shipping_type)

    # -- record builders ----------------------------------------------------

    def _accept(self, product, variant, price, basis, cost, match,
                requires_shipping, shipping_type,
                reason_code: str = "") -> PricingResult:
        decision = Decision.UPDATE if variant.sku_id in self.existing_skus else Decision.PUBLISH
        reason_ar = ("تحديث منتج موجود - السعر والمخزون والصور فقط"
                     if decision is Decision.UPDATE else "مطابق للشروط - يتم النشر")
        audit = AuditRecord(
            offer_id=product.offer_id,
            sku_id=variant.sku_id,
            decision=decision.value,
            reason_code=reason_code or ("matched" if match else "priced_by_margin"),
            reason_ar=reason_ar,
            cost_sar=str(cost),
            matched_platform=match.platform if match else "",
            match_score=str(match.match_score) if match else "",
            competitor_price_sar=str(match.price_sar) if match else "",
            final_price_sar=str(price),
            pricing_basis=basis,
            requires_shipping=requires_shipping,
            shipping_type=shipping_type,
        )
        return PricingResult(variant, decision, audit, price)

    def reject(self, product, variant, code, reason_ar) -> PricingResult:
        """
        Refuse one variant for a reason decided outside the engine - today that
        is the category tree. Public so callers do not have to reach for the
        private one, and so every rejection still produces the same audit row
        as the engine's own.
        """
        return self._reject(product, variant, code, reason_ar)

    def _reject(self, product, variant, code, reason_ar, cost=None, match=None,
                requires_shipping="", shipping_type="") -> PricingResult:
        audit = AuditRecord(
            offer_id=product.offer_id,
            sku_id=variant.sku_id,
            decision=Decision.REJECT.value,
            reason_code=code,
            reason_ar=reason_ar,
            cost_sar=str(cost) if cost is not None else "",
            matched_platform=match.platform if match else "",
            match_score=str(match.match_score) if match else "",
            competitor_price_sar=str(match.price_sar) if match else "",
            requires_shipping=requires_shipping,
            shipping_type=shipping_type,
        )
        return PricingResult(variant, Decision.REJECT, audit, None)

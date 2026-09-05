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

import freight
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

# --------------------------------------------------------------------------
# When one photograph is not evidence about one variant
# --------------------------------------------------------------------------
#
# THE CLIENT'S REPORT, 5 September 2026, on offer 1078382952230 - an industrial
# hydraulic bearing puller. Three complaints in three messages, and they are one
# fault seen from three sides:
#
#   "هذا المنتج يحتوي على 10 صور للشراء بينما في متجرنا فقط 5"
#   "لدينا في المتجر سعر موحد بينما هذا المنتج يحتوي على اكثر من سعر"
#   "هذا المنتج ليس متوفر في امازون لقد قمت بفحصة والتاكد [...] الاسعار داخل
#    امازون تتراوح بين 1000 الى 6000 ريال بينما سعر المنتج الحالي 283.22"
#
# WHAT THE AUDIT SHOWS. The listing has ten SKUs - 整体/分体 at 5, 10, 20, 30 and
# 50 tonnes - costing between 78.15 and 641.91 SAR landed. Every one of the ten
# was priced from ONE Amazon row at 289.00 SAR: five published at 283.22 and
# five refused as selling at a loss. That is his "10 became 5" and his "one
# price" in the same stroke.
#
# WHY. Two facts about the search, both measured in the stored comparison:
#
#   1. All ten SKUs share a single photograph on 1688 - the listing has one
#      image and the tonnage is written in the option name, not shown. The
#      picture search therefore runs once per PRODUCT (LENS_SCOPE=product), and
#      its answer is handed to every variant with an empty variant tag.
#   2. The five rival rows it returned are 289.00, 336.10, 521.55, 687.90 and
#      1169.99 SAR - and all five are stamped match_score 100, because in the
#      shopping stage a row inherits the picture's score rather than earning its
#      own. A set of prices spanning 4.0x cannot all be the same product.
#
# So the photograph identified a KIND of tool, and the cheapest member of that
# kind then priced a machine eight times its size. His rule from 28 August said
# this in advance - "prohibited to cross the cheap 1688 variant with the
# expensive rival" - and product-scope searching quietly broke it.
#
# TWO GUARDS, both failing to the margin, which is where he already agreed an
# unmatched product should land:
#
#   * rival prices that disagree among themselves by more than MAX_HIT_SPREAD
#     are not an identification, whatever score they carry;
#   * an untagged (product-scope) hit may not price a listing whose own variants
#     disagree by more than MAX_VARIANT_SPREAD - one photo, many prices, so the
#     photo cannot say which one it found.
#
# 1.5 is deliberately loose. Genuine sellers of one item differ by tens of
# percent; these differ by four hundred. A tighter bar would start throwing away
# real matches, and the point is to catch the case where the evidence refutes
# itself, not to require rival sellers to agree.
MAX_HIT_SPREAD = Decimal(os.environ.get("KDX_MAX_HIT_SPREAD", "1.5"))
MAX_VARIANT_SPREAD = Decimal(os.environ.get("KDX_MAX_VARIANT_SPREAD", "1.5"))


def _spread(values: list) -> Decimal:
    """Ratio of the largest to the smallest, or 1 when there is nothing to compare."""
    usable = [Decimal(str(value)) for value in values if value and Decimal(str(value)) > 0]
    if len(usable) < 2:
        return Decimal("1")
    return max(usable) / min(usable)


def hits_disagree(hits: list) -> bool:
    """
    Is there no price in this set that another price stands near?

    The question the audit reason answers, so it has to be asked the same way
    cheapest_supported asks it - a row that says "rivals disagree" about a set
    that did in fact price the product would be a lie in the file he reads.
    """
    eligible = [hit for hit in hits if hit.match_score >= MATCH_THRESHOLD]
    return bool(eligible) and cheapest_supported(eligible) is None


def variants_disagree(product) -> bool:
    """
    Does this listing sell things at prices too different for one photo to mean?

    Read off the 1688 prices, before freight and before any margin, because it
    is a question about what the seller is selling - a 5 tonne puller and a 50
    tonne puller - and not about what we would charge for it.

    Kept because it is the plain reading of the fault and it is what the client
    was told. It is NOT what the engine acts on: see photo_covers_listing,
    which asks the same question without a threshold in it.
    """
    return _spread([variant.price_cny for variant in getattr(product, "variants", [])
                    if variant.stock > 0]) > MAX_VARIANT_SPREAD

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
    # 5 September. His rate card turns freight into part of the cost, so the
    # log has to show where each figure came from: a shipping charge derived
    # from a size the seller stated and one derived from a default carton are
    # not the same claim, and he must be able to tell them apart without
    # reading the code. volume_source is one of override / declared / family /
    # default, and volume_note carries the box itself - "35x28x6cm (clothing)"
    # - so a wrong number can be traced to a wrong box in one glance.
    freight_sar: str = ""
    volume_m3: str = ""
    volume_source: str = ""
    volume_note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Individual rules
# --------------------------------------------------------------------------

def _volume_note(quote: dict) -> str:
    """The box, plus a flag when a heavy product is riding on a guessed one.

    Nothing is refused and no price moves for the flag - it exists so that the
    handful of rows where a wrong box costs real money are visible in the file
    he already reads, and can be answered with four numbers in dims.csv.
    """
    note = quote.get("evidence") or ""
    if quote.get("wants_measuring"):
        return f"{note} - يُفضّل قياسه يدوياً".strip()
    return note


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


def best_match(hits: list, variant: Variant,
               allow_untagged: bool = True) -> CompetitorHit | None:
    """
    Cheapest competitor offer that is genuinely the SAME variant.

    Three guards the client asked for, the third added on 5 September after he
    found a hydraulic puller priced from the wrong machine:
      - only hits scoring >= 95 count as the same product;
      - a hit is only usable against the variant it was matched to, so the
        cheap 1688 colour is never compared against an expensive rival colour;
      - `allow_untagged=False` withdraws the licence product-scope hits have to
        stand in for every variant. The caller decides, because whether one
        photo can speak for ten options is a fact about the LISTING, not about
        the hit - see variants_disagree.

    And one guard on the evidence itself: rival prices that disagree by more
    than MAX_HIT_SPREAD refute each other, so none of them is used. The
    cheapest of a contradictory set is the worst possible choice - it is
    exactly the row that undercuts our own cost.
    """
    eligible = [
        hit for hit in hits
        if hit.match_score >= MATCH_THRESHOLD
        and (hit.matched_variant == variant.sku_id
             or (allow_untagged and not hit.matched_variant))
    ]
    if not eligible:
        return None
    return cheapest_supported(eligible)


def cheapest_supported(hits: list) -> CompetitorHit | None:
    """
    The cheapest rival price that another rival price stands near.

    His instruction is to take the cheapest of the five apps, and that stays -
    this only decides which rows are allowed to be "the cheapest".

    WHY NOT A SPREAD BAR. The first version of this refused the whole set when
    its prices spanned more than MAX_HIT_SPREAD. Measured against every rival
    set the system has really collected - 66 offers that returned two or more
    distinct prices - that is unshippable: the spreads run smoothly from 1.05x
    to 41.48x with no gap anywhere to put a line in.

        bar 1.5x  drops 59 of 66      bar 2.5x  drops 47 of 66
        bar 2.0x  drops 52 of 66      bar 3.0x  drops 40 of 66

    Any bar throws away most of the comparison he paid for, and no bar is
    defensible over the one next to it. A threshold on that one number cannot
    separate these cases, so the rule needs a second signal instead.

    THE SECOND SIGNAL IS COMPANY. Several sellers of one product cluster; a
    category listing has a long cheap tail of different things. So a price is
    usable when at least one OTHER price sits within MAX_HIT_SPREAD of it, and
    the cheapest such price wins. An isolated bottom row - 12.00 SAR under a
    cluster at 110-115 - is not the same product being sold cheaply, it is a
    different product, and it is exactly the row that would undercut our own
    cost.

    A set with a single price has nothing to be tested against and is taken as
    it always was: refusing it would delete the ordinary case, where one app
    out of five carries the item.
    """
    prices = sorted(hits, key=lambda hit: hit.price_sar)
    if len(prices) == 1:
        return prices[0]
    for index, hit in enumerate(prices):
        for other in prices[index + 1:]:
            if other.price_sar <= hit.price_sar * MAX_HIT_SPREAD:
                return hit
        # Nothing above it is near it. If nothing below it was near it either -
        # and nothing was, or we would have returned already - this row stands
        # alone and the next one up gets its turn.
    return None


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

    def goods_cost_sar(self, variant: Variant) -> Decimal:
        """The 1688 price in riyals, and nothing else."""
        return money(variant.price_cny * self.cny_to_sar)

    def freight_quote(self, product: Product | None, variant: Variant) -> dict:
        """
        What it costs to bring this variant in, by the client's rate card of
        5 September: cubic metres times 1018 SAR, or 1244 if it runs on mains.

        The hole this closes is one he described himself on 3 September, and
        it is why he would not publish a heavy product without a rival price:

          "المنتجات الكبيرة هي تحسب بالابعاد وبعض الاحيان يكون سعر الشحن اعلى
           من سعر المنتج"

        Until today there was no freight term anywhere in this engine, because
        no rate had ever been given. Every guard built on cost - the loss guard,
        the margin bands - was protecting the goods price alone.
        """
        if product is None:
            return freight.quote(weight_kg=variant.weight_kg, is_electrical=False)
        return freight.quote(text=product.searchable_text(),
                             weight_kg=variant.weight_kg,
                             is_electrical=is_electrical(product),
                             offer_id=product.offer_id,
                             # A stated size is looked for everywhere, but the
                             # default box is chosen from what the thing IS -
                             # its category first, its title only if the
                             # category names no family - and never from the
                             # list of places its description says it can be
                             # used.
                             family_category=product.category_path,
                             family_title=product.title_zh)

    def landed_cost_sar(self, variant: Variant, product: Product | None = None) -> Decimal:
        """
        Goods plus freight - a landed cost that now deserves the name.

        `product` is optional so that a caller holding only a variant still
        gets the old, freight-free answer rather than a wrong one: without the
        listing there is no text to read a size from and no way to tell whether
        the thing is electrical, and both change the number.
        """
        goods = self.goods_cost_sar(variant)
        if product is None:
            return goods
        return money(goods + self.freight_quote(product, variant)["sar"])

    def photo_covers_listing(self, product: Product, hits_by_variant: dict) -> bool:
        """
        May one product-scope rival price stand for every option in this listing?

        Only when the price it would set covers the DEAREST option's landed
        cost. That is not a threshold anyone chose - it is his own loss guard,
        asked once for the listing instead of once per option.

        WHY NOT A SPREAD BAR HERE EITHER. The first version refused any listing
        whose options differed by more than 1.5x. Measured over the 95 offers
        that have really received a rival price, that bar drops 41 of them, and
        the option-spread distribution is as smooth as the rival-price one - no
        cliff at 1.5, or 2, or 3. It would have deleted nearly half the
        comparison he paid for on a number picked by feel.

        This asks the question the fault actually poses, and it has exactly one
        answer that means "the photograph is talking about a subset":

            min(option costs) < undercut price <= max(option costs)

        The price can pay for some of the options and not the others, so it
        cannot be about all of them. The hydraulic puller lands here on its own
        numbers: 289.00 SAR undercut to 283.22, against options costing 78.15 to
        641.91 landed.

        The two cases on either side are deliberately left alone, because
        neither is an attribution problem and one of them is his rule:

          price above every option   nothing sells at a loss, so which option
                                     the rival was does not change any outcome.
          price below every option   every option would be a loss. That is his
                                     loss guard of 3 September - light products
                                     fall through to the margin, heavy ones stop
                                     - and it must keep firing, with the rival
                                     still on the row as the evidence for it.
                                     Withdrawing the hit here would silently
                                     publish heavy products he asked to refuse.

        A listing whose search returned nothing untagged is unaffected, and so
        is one whose options all cost the same.
        """
        untagged = [hit for lst in (hits_by_variant or {}).values() for hit in lst
                    if not hit.matched_variant and hit.match_score >= MATCH_THRESHOLD]
        if not untagged:
            return True
        chosen = cheapest_supported(untagged)
        if chosen is None:
            return True                 # nothing usable anyway; best_match will refuse
        price, _ = undercut_price(chosen.price_sar)
        costs = [self.landed_cost_sar(variant, product)
                 for variant in product.variants if variant.stock > 0]
        if not costs:
            return True
        return not (min(costs) < price <= max(costs))

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

        # Asked once per listing, not once per variant: it is a property of the
        # listing, and asking it per variant would let the answer differ between
        # two options of the same product.
        allow_untagged = self.photo_covers_listing(product, hits_by_variant)
        return [
            self._evaluate_variant(product, variant,
                                   hits_by_variant.get(variant.sku_id, []),
                                   allow_untagged=allow_untagged)
            for variant in product.variants
        ]

    def _evaluate_variant(self, product: Product, variant: Variant, hits: list,
                          allow_untagged: bool = True) -> PricingResult:
        if variant.stock <= 0:
            return self._reject(product, variant, "out_of_stock",
                                "غير متوفر في 1688 - لا يتم النشر")

        # Before freight, because freight is about to stop this from being
        # caught by the floor. A listing quoting zero is broken data, not a
        # free product; until today its price came out at zero and the minimum
        # price refused it. With shipping inside the cost it would come out at
        # the price of its own carriage - a real number, comfortably above the
        # floor - and a listing with no price would start publishing.
        if self.goods_cost_sar(variant) <= 0:
            return self._reject(product, variant, "no_price",
                                "لا يوجد سعر حقيقي للمنتج في 1688 - لا يتم النشر")

        quote = self.freight_quote(product, variant)
        # Cost is goods plus freight from here down, so every rule that reads
        # it moves with the rate card automatically: the loss guard now refuses
        # a rival price that cannot cover the shipping, and the margin bands
        # are picked from the price the shipping is already inside - which is
        # the order he wrote, "ثم يلصق سعر الشحن على سعر المنتج" and only then
        # "ثم اجعل هامش الربح".
        cost = money(self.goods_cost_sar(variant) + quote["sar"])
        requires_shipping, shipping_type = shipping_flag(variant.weight_kg)
        match = best_match(hits, variant, allow_untagged=allow_untagged)
        # Why the comparison was dropped, when it was dropped by one of the two
        # 5-September guards rather than by nobody selling the thing. Kept apart
        # in the audit so he can count them: "found nothing" and "found
        # something that refuted itself" are different facts about his catalogue
        # and he asked for the reason on every row.
        refuted = ""
        if match is None and hits:
            if not allow_untagged and any(not hit.matched_variant for hit in hits):
                refuted = "photo_not_variant"
            elif hits_disagree(hits):
                refuted = "rivals_disagree"

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
        #
        # Until 5 September a heavy product stopped here unpublished, because
        # there was no freight figure anywhere in the engine and pricing one
        # from the goods alone would have sold the carriage for nothing. His
        # rate card removed that hole, and he opened the gate himself the same
        # day, in answer to the question put to him:
        #
        #   "نعّم أوافقك مع عدم ايقاف عملية المقارنة يعني العملية هي نفسها
        #    التي في الشحن السريع اذا لم يحصل المنتج في التطبيقات الخمسة يبدا
        #    النظام يجعل قيمة الشحن وهامش الربح"
        #
        # Note what he did NOT open. The comparison still runs on every product
        # and a rival that is found still sets the price; this is only what
        # happens when the five apps come back empty. And a heavy product with
        # a rival price BELOW its landed cost is still refused above - that
        # guard is about a rival we found, not about one we never had.
        heavy_unmatched = variant.weight_kg > LIGHT_MAX_KG

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
        if refuted == "photo_not_variant":
            basis += " (صورة واحدة لعدة أسعار - لا تصلح للمقارنة)"
        elif refuted == "rivals_disagree":
            basis += " (أسعار المنافسين متضاربة - لم تُعتمد)"
        # Its own reason code so he can count the products this gate let
        # through, and so the day he wants it shut again is one line, not an
        # archaeology exercise over the audit file.
        code = "margin_unmatched_heavy" if heavy_unmatched else ""
        if refuted:
            code = f"margin_{refuted}"
        return self._accept(product, variant, price, basis, cost, None,
                            requires_shipping, shipping_type,
                            reason_code=code)

    # -- record builders ----------------------------------------------------

    def _accept(self, product, variant, price, basis, cost, match,
                requires_shipping, shipping_type,
                reason_code: str = "") -> PricingResult:
        decision = Decision.UPDATE if variant.sku_id in self.existing_skus else Decision.PUBLISH
        reason_ar = ("تحديث منتج موجود - السعر والمخزون والصور فقط"
                     if decision is Decision.UPDATE else "مطابق للشروط - يتم النشر")
        # Recomputed rather than threaded through every call site. It is a pure
        # function of the listing and the variant, so it cannot disagree with
        # the one the price was built from, and every row - accepted or not -
        # ends up carrying the same three columns.
        quote = self.freight_quote(product, variant)
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
            freight_sar=str(quote["sar"]),
            volume_m3=str(quote["m3"]),
            volume_source=quote["source"],
            volume_note=_volume_note(quote),
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
        quote = self.freight_quote(product, variant)
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
            freight_sar=str(quote["sar"]),
            volume_m3=str(quote["m3"]),
            volume_source=quote["source"],
            volume_note=_volume_note(quote),
        )
        return PricingResult(variant, Decision.REJECT, audit, None)

"""
Refuse any listing that is, or contains, a liquid.

Client, 1 September 2026: "امنع المنتجات التي تحتوي على سوائل بشكل كامل".
Liquids are the class most likely to be seized in transit - aerosols,
flammables, cosmetics - so a listing that slips through does not cost a sale,
it costs a shipment and possibly his account.

The hard part is not listing liquids. It is not deleting the catalogue while
doing it. A substring ban over Chinese names is a blunt instrument:

    水  is in 水杯 (cup), 防水 (waterproof), 水果 (fruit), 水晶 (crystal)
    油  is in 油画 (oil painting), 油炸锅 (fryer), 抽油烟机 (extractor hood)
    液  is in 液晶显示屏 (LCD screen)
    oil is inside b-oil-er, f-oil, c-oil, t-oil-et
    ink is inside dr-ink, th-ink, p-ink, s-ink

So nothing here matches a bare one-character Chinese word or an unbounded
English fragment. Chinese entries are compounds of two characters or more,
English entries are whole words or whole phrases, and anything that survives
both is checked against a list of innocent compounds before it is refused.

The same word can name the liquid and the empty container that holds it - a
perfume bottle is glassware, a sprayer is a garden tool, a milk frother is an
appliance. Those live in CONTAINERS and are exempted, because refusing them
would throw away ordinary merchandise that ships perfectly well.

Where a term is genuinely ambiguous and I could not resolve it, it BLOCKS.
A wrongly blocked product is one missing line in the audit log, which he can
read and overrule. A wrongly published one is at customs.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Chinese: compounds only, never a single character.
# --------------------------------------------------------------------------

ZH_TERMS = {
    # 洗护 - personal care
    "洗发水": "personal_care", "洗发露": "personal_care", "护发素": "personal_care",
    "沐浴露": "personal_care", "沐浴液": "personal_care", "洗面奶": "personal_care",
    "洗手液": "personal_care", "洁面乳": "personal_care", "卸妆水": "personal_care",
    "卸妆油": "personal_care", "爽肤水": "personal_care", "精华液": "personal_care",
    "精华油": "personal_care", "乳液": "personal_care", "身体乳": "personal_care",
    "护手霜": "personal_care", "面霜": "personal_care", "防晒霜": "personal_care",
    "花露水": "personal_care", "香水": "personal_care", "古龙水": "personal_care",
    "指甲油": "personal_care", "卸甲水": "personal_care", "染发剂": "personal_care",
    "啫喱水": "personal_care", "摩丝": "personal_care", "发胶": "personal_care",
    "按摩油": "personal_care", "婴儿油": "personal_care", "精油": "personal_care",
    "香薰油": "personal_care", "牙膏": "personal_care", "漱口水": "personal_care",
    # 清洁 - cleaning
    "洗衣液": "cleaning", "洗洁精": "cleaning", "洗涤剂": "cleaning",
    "柔顺剂": "cleaning", "消毒液": "cleaning", "消毒水": "cleaning",
    "清洁剂": "cleaning", "清洗剂": "cleaning", "除锈剂": "cleaning",
    "空气清新剂": "cleaning", "杀虫剂": "cleaning", "驱蚊液": "cleaning",
    "洁厕灵": "cleaning", "漂白水": "cleaning", "双氧水": "cleaning",
    "酒精": "cleaning", "免洗凝胶": "cleaning",
    # 化工 - chemicals, fuels, workshop
    "液体": "chemical", "油漆": "chemical", "涂料": "chemical", "稀释剂": "chemical",
    "胶水": "chemical", "强力胶": "chemical", "502胶": "chemical", "墨水": "chemical",
    "碳粉墨": "chemical", "润滑油": "chemical", "润滑液": "chemical", "机油": "chemical",
    "防冻液": "chemical", "玻璃水": "chemical", "制动液": "chemical", "刹车油": "chemical",
    "汽油": "chemical", "柴油": "chemical", "煤油": "chemical", "燃油": "chemical",
    "打火机油": "chemical", "松节油": "chemical", "固化剂": "chemical",
    # 食品饮料 - food and drink
    "饮料": "food", "果汁": "food", "矿泉水": "food", "纯净水": "food",
    "牛奶": "food", "酸奶": "food", "蜂蜜": "food", "食用油": "food",
    "橄榄油": "food", "花生油": "food", "香油": "food", "酱油": "food",
    "食醋": "food", "米醋": "food", "糖浆": "food", "蜂蜜水": "food",
    # 医药 - medical
    "眼药水": "medical", "生理盐水": "medical", "碘伏": "medical",
    "药水": "medical", "口服液": "medical",
}

# --------------------------------------------------------------------------
# English and Arabic: whole words and whole phrases.
#
# Bare "oil", "gel", "cream" and "spray" are deliberately absent. Each of them
# names as many solid products as liquid ones, so they appear here only inside
# a phrase whose head noun is the liquid itself.
# --------------------------------------------------------------------------

LATIN_TERMS = {
    "shampoo": "personal_care", "hair conditioner": "personal_care",
    "body wash": "personal_care", "shower gel": "personal_care",
    "hand sanitizer": "personal_care", "sanitiser": "personal_care",
    "sanitizer": "personal_care", "micellar water": "personal_care",
    "makeup remover": "personal_care", "facial toner": "personal_care",
    "skin toner": "personal_care", "face serum": "personal_care",
    "body lotion": "personal_care", "lotion": "personal_care",
    "perfume": "personal_care", "cologne": "personal_care",
    "eau de toilette": "personal_care", "eau de parfum": "personal_care",
    "nail polish": "personal_care", "polish remover": "personal_care",
    "hair dye": "personal_care", "hair gel": "personal_care",
    "styling gel": "personal_care", "essential oil": "personal_care",
    "massage oil": "personal_care", "baby oil": "personal_care",
    "hair oil": "personal_care", "body oil": "personal_care",
    "aloe vera gel": "personal_care", "mouthwash": "personal_care",
    "toothpaste": "personal_care",
    "detergent": "cleaning", "disinfectant": "cleaning", "antiseptic": "cleaning",
    "bleach": "cleaning", "fabric softener": "cleaning", "insecticide": "cleaning",
    "air freshener": "cleaning", "hydrogen peroxide": "cleaning",
    "rubbing alcohol": "cleaning", "isopropyl": "cleaning",
    "liquid soap": "cleaning", "floor cleaner": "cleaning",
    "glass cleaner": "cleaning",
    "paint": "chemical", "varnish": "chemical", "lacquer": "chemical",
    "solvent": "chemical", "thinner": "chemical", "adhesive glue": "chemical",
    "super glue": "chemical", "glue": "chemical", "resin liquid": "chemical",
    "engine oil": "chemical", "motor oil": "chemical", "lubricant": "chemical",
    "coolant": "chemical", "antifreeze": "chemical", "brake fluid": "chemical",
    "gasoline": "chemical", "kerosene": "chemical", "lighter fluid": "chemical",
    "aerosol": "chemical", "spray paint": "chemical", "ink": "chemical",
    "fluid": "chemical", "liquid": "chemical",
    "juice": "food", "beverage": "food", "syrup": "food", "vinegar": "food",
    "olive oil": "food", "cooking oil": "food", "milk": "food", "honey": "food",
    "eye drops": "medical", "saline solution": "medical",
    # Arabic, as a backstop: the titles this runs against are Chinese, but the
    # specification block sometimes arrives already translated.
    "سائل": "chemical", "سوائل": "chemical", "شامبو": "personal_care",
    "عطر": "personal_care", "غسول": "personal_care", "معقم": "cleaning",
    "مطهر": "cleaning", "منظف": "cleaning", "دهان": "chemical",
    "طلاء": "chemical", "غراء": "chemical", "حبر": "chemical",
    "عصير": "food", "مشروب": "food", "حليب": "food", "عسل": "food",
    "زيت عطري": "personal_care", "بخاخ": "chemical", "مبيد": "cleaning",
}

# --------------------------------------------------------------------------
# The word names the liquid, but this compound names the empty container, the
# appliance that holds it, the fabric named after it, or the colour.
#
# Checked AFTER the term matched and BEFORE the listing is refused. If any of
# these appears anywhere in the text, that one match is forgiven; a second,
# unforgiven term still blocks.
# --------------------------------------------------------------------------

CONTAINERS = (
    # Chinese: bottles, pumps, dispensers, holders, machines
    "香水瓶", "香水座", "香水架", "香水分装", "分装瓶", "乳液瓶", "乳液泵",
    "洗发水瓶", "沐浴露瓶", "洗手液瓶", "按压瓶", "空瓶", "喷雾瓶", "喷雾器",
    "喷雾机", "喷雾仪", "喷雾枪", "墨水瓶", "油漆刷", "油漆桶", "油漆滚筒",
    "刷子", "饮料杯", "饮料瓶", "饮料机", "饮料包装", "果汁机", "果汁杯",
    "榨汁", "矿泉水瓶", "牛奶杯", "牛奶盒", "牛奶罐", "打奶泡", "奶泡机",
    "牛奶加热", "牛奶壶", "牛奶锅", "牛奶机", "牛奶保温", "温奶器",
    # Department names where the liquid is what the MACHINERY or the agency
    # deals in. These three came out of the real 1497-category walk, where
    # "食品、饮料加工及餐饮行业设备" - catering industry equipment - was being
    # refused as a beverage.
    "饮料加工", "饮料设备", "饮料机械", "饮料生产", "饮料代理", "饮料项目",
    "饮料行业", "饮料灌装", "油漆设备", "涂料设备", "胶水设备",
    "蜂蜜罐", "蜂蜜勺", "油壶", "调味瓶", "酱油瓶", "精油瓶", "香薰炉",
    "香薰石", "扩香", "药水瓶", "滴管", "量杯", "储液", "收纳",
    # Chinese: the word is a material or a colour, not a liquid
    "牛奶绒", "牛奶丝", "牛奶棉", "液晶", "乳胶", "乳白", "奶油色", "蜂蜜色",
    "油画", "油炸", "油烟", "油纸", "机油滤", "滤芯",
    # Latin: same idea
    "liquid crystal", "perfume bottle", "spray bottle", "empty bottle",
    "refill bottle", "lotion pump", "pump head", "dispenser", "milk frother",
    "milk bottle", "milk silk", "milk fleece", "juicer", "juice cup",
    "honeycomb", "honey comb", "honey color", "honey colour", "cream color",
    "cream colour", "oil painting", "oil paper", "oil filter", "air conditioner",
    "paint brush", "paint roller", "paint bucket", "paint tray",
    "paint by number", "painting", "ink pad stamp", "toner cartridge",
    "adhesive tape", "adhesive hook", "adhesive sticker", "glue gun",
    "glue stick", "baking soda", "milk tea cup", "sanitizer holder",
    "bottle holder",
)

# English/Arabic terms are matched as whole words. \b does not fire between two
# Chinese characters, which is why the two alphabets are matched separately.
_LATIN = [(re.compile(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", re.IGNORECASE), term,
           reason) for term, reason in LATIN_TERMS.items()]


def find_liquid_term(text: str) -> tuple[str, str] | None:
    """
    Return (reason, matched_term) when the text names a liquid, else None.

    The reason is one of personal_care / cleaning / chemical / food / medical,
    so the audit log tells him WHY a product was held back and not merely that
    it was.
    """
    if not text:
        return None
    lowered = text.lower()
    exempt = [phrase for phrase in CONTAINERS if phrase.lower() in lowered]

    for term, reason in ZH_TERMS.items():
        if term in text and not _forgiven(term, exempt):
            return reason, term

    for pattern, term, reason in _LATIN:
        if pattern.search(text) and not _forgiven(term, exempt):
            return reason, term

    return None


def _forgiven(term: str, exempt: list) -> bool:
    """
    True when the only reason the term appeared is an innocent compound.

    "香水瓶" contains "香水", so the exemption has to swallow the term itself -
    but "洗发水 香水瓶" must still block, because the shampoo is not explained
    by the perfume bottle. So an exemption only forgives the term it contains.
    """
    return any(term.lower() in phrase.lower() for phrase in exempt)

"""
Proof for the liquids ban.

    python3 verify_liquids.py

Two halves, and the second is the important one.

The first half checks that liquids are caught. That is the easy half - any word
list catches liquids, including one that also catches everything else.

The second half is the negative control: thirty ordinary products whose names
contain a liquid word and which must still be published. A water bottle, an
LCD screen, a water boiler, an oil painting, a milk-fleece hoodie, a milk
frother, a perfume bottle, a paint brush. If the ban list ever grows a
one-character token or an unbounded fragment, this half goes red immediately,
which is the whole reason it exists.

The last section runs the filter over the client's own 151 prepared products
and prints exactly what it would remove, so the cost of the ban is a measured
number rather than a promise. No network.
"""

from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import liquids  # noqa: E402

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def blocks(text: str) -> bool:
    return liquids.find_liquid_term(text) is not None


# ------------------------------------------------------------------ caught
print("liquids are refused, in every language the listing might use them")
CAUGHT = [
    ("洗发水去屑控油洗护套装", "personal_care"),
    ("儿童沐浴露泡泡浴", "personal_care"),
    ("法国香水女士持久淡香", "personal_care"),
    ("薰衣草精油按摩护理", "personal_care"),
    ("指甲油快干不掉色", "personal_care"),
    ("洗衣液薰衣草香型5kg", "cleaning"),
    ("84消毒液家用杀菌", "cleaning"),
    ("75%酒精免洗洗手液", "personal_care"),
    ("强力胶水粘鞋专用", "chemical"),
    ("汽车防冻液玻璃水四季通用", "chemical"),
    ("墙面乳胶漆油漆白色", "chemical"),
    ("打印机墨水连供四色", "chemical"),
    ("橄榄油食用油压榨", "food"),
    ("鲜榨果汁饮料整箱", "food"),
    ("眼药水缓解疲劳", "medical"),
    ("Hand Sanitizer Gel 500ml Alcohol", "personal_care"),
    ("Essential Oil Diffuser Refill Lavender", "personal_care"),
    ("Car Engine Oil 5W-30 Full Synthetic", "chemical"),
    ("Acrylic Paint Set 24 Colors", "chemical"),
    ("Liquid Foundation Makeup Full Coverage", "chemical"),
    ("شامبو للشعر الجاف 400 مل", "personal_care"),
    ("معقم يدين بالكحول", "cleaning"),
]
for text, expected in CAUGHT:
    hit = liquids.find_liquid_term(text)
    check(f"blocked: {text[:38]}",
          hit is not None and hit[0] == expected,
          f"got {hit}, expected reason {expected!r}")

# ------------------------------------------------------- negative controls
print("\nordinary products that contain a liquid word must still be published")
KEPT = [
    "不锈钢保温杯户外运动水杯",          # water cup
    "商用不锈钢电热开水器30L 3000W",     # water boiler - contains 'oil' in latin
    "液晶显示屏15.6寸高清",               # LCD - contains 液
    "户外防水帐篷双人露营",               # waterproof, camping - 水 and 露
    "新鲜水果收纳篮",                     # fruit - 水
    "水晶吊灯客厅现代简约",               # crystal - 水
    "抽油烟机厨房大吸力",                 # extractor hood - 油
    "空气炸锅家用无油炸锅",               # air fryer - 油炸
    "油画装饰画客厅挂画",                 # oil painting - 油画
    "牛奶绒加厚保暖睡衣套装",             # milk fleece pyjamas - 牛奶
    "牛奶丝短袖T恤女",                    # milk silk tee - 牛奶
    "奶油色针织开衫",                     # cream colour cardigan
    "香水瓶空瓶分装喷雾瓶玻璃",           # empty perfume bottles
    "乳液泵头按压嘴替换",                 # lotion pump heads
    "喷雾器园艺浇花手压式",               # garden sprayer
    "油漆刷排刷软毛",                     # paint brushes
    "榨汁机家用小型果汁机",               # juicer
    "打奶泡机牛奶加热器",                 # milk frother
    "蜂蜜罐带勺玻璃密封",                 # honey jar
    "调味瓶油壶酱油瓶厨房",               # oil and soy sauce cruets
    "汽车机油滤芯保养",                   # oil filter
    "乳胶枕头护颈助眠",                   # latex pillow - 乳胶
    "Stainless Steel Water Boiler 30L",   # 'oil' inside boiler
    "Aluminium Foil Roll 30cm Kitchen",   # 'oil' inside foil
    "Heating Coil Element 2000W",         # 'oil' inside coil
    "Toilet Brush Holder Set",            # 'oil' inside toilet
    "Pink Drink Bottle 1L BPA Free",      # 'ink' inside pink and drink
    "Wireless Doorbell Chime Kitchen Sink",  # 'ink' inside sink
    "Liquid Crystal Display Module 7 inch",  # liquid crystal
    "Air Conditioner Remote Control Universal",  # 'conditioner'
    "Adhesive Tape Double Sided 10m",     # adhesive tape, not glue
    "Glue Gun Sticks Hot Melt 20pcs",     # the tool, not the glue
    "Oil Painting Canvas Frame 40x50",
    "Paint Brush Set Artist 12pcs",
    "Baking Soda Cleaning Powder 1kg",
    # Real department names from the 1497-category walk: machinery and agency
    # branches, not liquids. Blocking these would delete a whole aisle.
    "食品、饮料加工及餐饮行业设备",
    "食品饮料代理加盟",
    "食品饮料项目合作",
]
for text in KEPT:
    hit = liquids.find_liquid_term(text)
    check(f"kept: {text[:38]}", hit is None, f"blocked by {hit}")

# ------------------------------------------------------- the exemption rule
print("\nan exemption forgives its own word only, never the whole listing")
check("a shampoo listed beside an empty perfume bottle still blocks",
      liquids.find_liquid_term("洗发水 香水瓶 套装") is not None,
      "the perfume bottle must not excuse the shampoo")
check("and the term named is the shampoo, not the perfume",
      liquids.find_liquid_term("洗发水 香水瓶 套装")[1] == "洗发水")
check("a perfume bottle on its own is still merchandise",
      liquids.find_liquid_term("香水瓶 玻璃 空瓶") is None)

print("\nthe list itself cannot regress into a blunt instrument")
check("no Chinese term is a single character",
      all(len(term) >= 2 for term in liquids.ZH_TERMS),
      str([t for t in liquids.ZH_TERMS if len(t) < 2]))
BARE = {"oil", "gel", "cream", "spray", "water", "wax", "polish", "toner"}
check("no bare English word that names solids as often as liquids",
      not (BARE & set(liquids.LATIN_TERMS)),
      str(sorted(BARE & set(liquids.LATIN_TERMS))))
check("every term carries a reason the audit log can print",
      all(reason for reason in
          list(liquids.ZH_TERMS.values()) + list(liquids.LATIN_TERMS.values())))
check("empty text is not a liquid", liquids.find_liquid_term("") is None)
check("None is not a liquid", liquids.find_liquid_term(None) is None)

# ------------------------------------------------------ wired into the rules
print("\nand the engine actually refuses one")
from rules import Product, Variant, Engine, find_banned_term  # noqa: E402
from decimal import Decimal  # noqa: E402


def listing(title: str) -> Product:
    return Product(offer_id="1", title_zh=title, description_zh="", images=["x.jpg"],
                   variants=[Variant(sku_id="s1", attributes={}, price_cny=Decimal("10"),
                                     stock=5, weight_kg=Decimal("0.5"))])


engine = Engine(cny_to_sar=Decimal("0.558"))
shampoo = engine.evaluate(listing("洗发水去屑控油"), {})
check("a shampoo is rejected before it is priced",
      all(r.decision.value == "reject" for r in shampoo))
check("with its own reason code, not lumped in with banned_category",
      shampoo[0].audit.reason_code == "contains_liquid",
      shampoo[0].audit.reason_code)
check("and the Arabic reason names the matched word",
      "洗发水" in shampoo[0].audit.reason_ar, shampoo[0].audit.reason_ar)
boiler = engine.evaluate(listing("商用不锈钢电热开水器"), {})
check("a water boiler is still evaluated normally",
      all(r.audit.reason_code != "contains_liquid" for r in boiler))
check("find_banned_term is unchanged by this",
      find_banned_term(listing("洗发水")) is None,
      "liquids are a separate rule, not an extra banned category")

# ------------------------------------------------- measured on his catalogue
print("\nwhat this costs on the client's own 151 prepared products")
found = sorted(glob.glob(os.path.join(HERE, "out", "*", "*.json")))
if not found:
    print("  (no prepared products on disk - skipped)")
else:
    removed = []
    for path in found:
        try:
            item = json.load(open(path, encoding="utf-8"))
        except (ValueError, OSError):
            continue
        text = " ".join(str(item.get(key, "")) for key in
                        ("name_original", "name_en", "name_ar"))
        hit = liquids.find_liquid_term(text)
        if hit:
            removed.append((item.get("source_offer_id", "?"), hit, text[:60]))
    print(f"  {len(found)} products on disk, {len(removed)} would be held back")
    for offer_id, hit, text in removed[:25]:
        print(f"    {offer_id}  {hit[0]:<14} {hit[1]:<10} {text}")
    check("the ban does not empty the catalogue",
          len(removed) < len(found) * 0.5,
          f"{len(removed)} of {len(found)} removed - the list is too blunt")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

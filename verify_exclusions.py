"""
His two exclusions of 3 September, and the false positives that nearly shipped.

    "استبعاد المنتجات التي تحتوي على وجبات سواء كانت الى الأنسان او كانت الى
     الحيوان"
    "استبعاد المنتجات التي تحتوي على طحين او مواد كيماوية او كيميائية"

Every Chinese title in here is a real one, taken from a product this system
actually assembled and published, so a rule that passes this suite is a rule
measured against the catalogue rather than against my idea of it.

The controls matter more than the bans. A word list that removes food also
removes food-GRADE lunch boxes and gas masks worn against chemicals unless it is
told not to, and those are ordinary merchandise in departments he sells.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import catalog  # noqa: E402
import rules  # noqa: E402

passed = failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f" [{detail}]" if detail else ""))


def listing(title_zh: str, description_zh: str = "", category_path: str = "") -> rules.Product:
    return rules.Product(
        offer_id="1", title_zh=title_zh, description_zh=description_zh,
        images=["x.jpg"], category_path=category_path,
        variants=[rules.Variant(sku_id="s1", attributes={}, price_cny=Decimal("10"),
                                stock=5, weight_kg=Decimal("0.5"))])


print("1. food, for people and for animals")

# Real titles. The first three were published on 3 September before he stopped
# the run, from 宠物及园艺 - a department he KEEPS - which is why the department
# list could never have caught them.
CAT_TREATS = "冻干猫零食鸡肉粒鸡胸肉宠物猫咪零食增肥发腮小肉干狗营养条猫粮"
DOG_FOOD = "柴犬狗粮10kg20kg40kg幼犬成犬通用型天然粮"
SOFT_DOG_FOOD = "鲜肉软狗粮全期通用小型犬"

for title in (CAT_TREATS, DOG_FOOD, SOFT_DOG_FOOD):
    hit = rules.find_banned_term(listing(title))
    check(f"animal food is refused: {title[:18]}",
          hit is not None and hit[0] in ("food", "animal_food"), str(hit))

check("human food is refused too - his rule names both",
      rules.find_banned_term(listing("休闲零食大礼包夹心饼干糖果")) is not None)

# 4 September. The three titles above went in on the 3rd and the shop still got
# goat milk powder for cats and dogs at 22:30 that same night: a supplement is
# not 粮, not 零食, not 罐头, so not one word on the list described it. The
# category gate (catalog.FOOD_TOKENS) is the wider net and blocks 狗狗保健品 and
# 猫猫保健品 outright; these words are the second line, for the same goods filed
# somewhere else.
for title in ("宠物羊奶粉幼犬幼猫补充营养山羊奶粉",
              "猫咪狗狗益生菌调理肠胃宠物益生菌",
              "犬猫通用宠物营养品微量元素片"):
    hit = rules.find_banned_term(listing(title))
    check(f"a pet supplement is refused: {title[:14]}",
          hit is not None and hit[0] in ("food", "animal_food"), str(hit))

# CONTROL milk powder for a BABY is human food, refused by the same rule for the
# other half of his sentence, and the tin it comes in is neither.
check("CONTROL a baby formula tin is a container, not a meal",
      rules.find_banned_term(listing("奶粉罐密封储存罐防潮奶粉盒便携")) is None,
      str(rules.find_banned_term(listing("奶粉罐密封储存罐防潮奶粉盒便携"))))
check("and the reason names the exact word, so the audit row can be read",
      rules.find_banned_term(listing(CAT_TREATS))[1] in ("零食", "猫粮", "冻干猫"),
      str(rules.find_banned_term(listing(CAT_TREATS))))

print("\n2. CONTROLS: what must survive the food rule")
# 食品级 is "food grade" - a lunch box, a silicone mould, a storage jar. Without
# BANNED_SAFE_PHRASES the word 食品 inside it deletes the whole aisle, and he
# sells 日用餐厨饮具 (tableware) as a department.
for title in ("食品级硅胶保鲜盒密封饭盒",
              "食品级不锈钢保温饭盒学生",
              "零食收纳盒桌面塑料整理盒",
              "宠物碗猫咪双碗喂食器"):
    check(f"kept, it is a container not a meal: {title[:14]}",
          rules.find_banned_term(listing(title)) is None,
          str(rules.find_banned_term(listing(title))))

check("CONTROL a safe phrase excuses only the word it contains - "
      "food-grade box that ALSO sells snacks is still refused",
      rules.find_banned_term(listing("食品级硅胶盒装休闲零食大礼包")) is not None)

print("\n3. flour, and the Arabic word that is not flour")
check("flour is refused", rules.find_banned_term(listing("高筋小麦粉面粉烘焙原料")) is not None)
check("starch is refused", rules.find_banned_term(listing("玉米淀粉食用")) is not None)
# Measured, not imagined: scored over the assembled catalogue the bare word
# "دقيق" caught a German precision pressure-reducing valve, because دقيق is
# "flour" AND "precise". The list carries the phrase, never the bare noun.
check("CONTROL the bare Arabic word for flour is NOT in the list - "
      "it also means 'precise' and caught a precision valve",
      "دقيق" not in rules.BANNED_TERMS["flour"],
      str(rules.BANNED_TERMS["flour"]))
check("CONTROL so a precision valve survives",
      rules.find_banned_term(
          listing("德国进口高精密减压阀", description_zh="صمام دقيق")) is None)

print("\n4. chemical and chemistry materials")
# The thirteen compounds published on 3 September, by their real titles.
RUBBERS = [
    "丁苯橡胶SBR1502 中石油抚顺石化丁苯橡胶1502 经销各类合成橡胶",
    "优惠供应兰化丁腈橡胶NBR3305E  兰州石化丁腈橡胶3305E 合成橡胶",
    "山纳氯丁橡胶SN244X 山纳合成SN244X (1,2,3,4,5)氯丁胶",
    "供应中石化齐鲁石化充油丁苯橡胶SBR1712 合成橡胶",
]
for title in RUBBERS:
    hit = rules.find_banned_term(listing(title))
    check(f"raw synthetic rubber is refused: {title[:16]}",
          hit is not None and hit[0] == "chemicals", str(hit))

check("chemical reagents are refused",
      rules.find_banned_term(listing("分析纯化学试剂实验室用")) is not None)

print("\n5. CONTROLS: what must survive the chemicals rule")
# All four are real, all published, all in 安全、防护 - safety equipment. They
# are worn AGAINST chemicals. A rule that reads "chemical" as a topic rather
# than a material takes the entire respirator aisle with it.
for title in ("3m3270防毒面具防尘橡胶面具套装半面罩防工业粉尘防护焊接头戴式",
              "6200防毒面具喷漆专用化工农药防护面罩",
              "KN95防尘口罩工业防护可水洗",
              "自吸过滤式防毒面具有机气体酸性气体"):
    check(f"kept, it is protection not a compound: {title[:14]}",
          rules.find_banned_term(listing(title)) is None,
          str(rules.find_banned_term(listing(title))))

check("CONTROL plain rubber is not banned on its own - "
      "only the named synthetic compounds are",
      "橡胶" not in rules.BANNED_TERMS["chemicals"])

print("\n6. the department list is a filter now, not just a word source")
off = catalog.departments_off()
check("data/departments.json yields the departments he switched off",
      len(off) > 0, f"{len(off)} off")
check("食品酒水 is one of them - he excluded food and drink on 2 September",
      "食品酒水" in off, str(sorted(off)[:6]))
check("化工 is one of them", "化工" in off)
# The correction that matters. My first reading of the 3 September rubber blamed
# this list, and the list says otherwise: he ASKED for 橡塑 on 2 September along
# with seventeen other industrial departments. The rubber is excluded by its
# words, not by its department.
check("CONTROL 橡塑 is NOT off - he asked for it, so the rubber was never a "
      "department leak",
      "橡塑" not in off)

index = catalog.CategoryIndex([
    {"id": "2", "name_zh": "食品酒水", "name_ar": "طعام", "parent_id": None,
     "state": "allowed"},
    {"id": "20", "name_zh": "休闲食品", "name_ar": "وجبات", "parent_id": "2",
     "state": "allowed"},
    {"id": "6", "name_zh": "家用电器", "name_ar": "أجهزة", "parent_id": None,
     "state": "allowed"},
])
check("a leaf resolves to its top-level department",
      index.department_of("20") == "食品酒水", index.department_of("20"))
check("and a product in a switched-off department is refused with his reason",
      bool(index.department_is_off("20")), index.department_is_off("20"))
check("CONTROL a department he keeps is not refused",
      index.department_is_off("6") == "")
check("CONTROL an unresolvable category is not refused by THIS rule - "
      "the words decide those",
      index.department_is_off("999999") == "")

print("\n7. the index the LIVE run holds, not the one the suite builds")
# This section exists because the suite passed without it and the run died.
# department_of/department_is_off were written onto CategoryIndex; daily_run
# hands the pipeline a category_live.LiveIndex, and the first product of the
# first batch after deploy raised
#   AttributeError: 'LiveIndex' object has no attribute 'department_is_off'
# Testing the class I happened to construct is not testing the gate.
import category_live  # noqa: E402

live = category_live.LiveIndex(index, client=None, cache=os.path.join(
    HERE, "out", "verify-exclusions-cache.json"))
check("LiveIndex answers the department gate at all",
      hasattr(live, "department_is_off") and hasattr(live, "department_of"))
check("and gives the same answer as the built index for a department that is off",
      bool(live.department_is_off("20")), live.department_is_off("20"))
check("and the same answer for one he keeps",
      live.department_is_off("6") == "")
check("CONTROL both indexes inherit ONE gate, so they cannot drift apart",
      type(live).department_is_off is type(index).department_is_off)

# The whole interface, written down. Its limit is honest: a seventh method added
# to one class and called by the pipeline would still pass until its name is
# added here. The mixin above is what makes the gate itself undriftable; this is
# a reminder that pipeline.build() swaps the index underneath the suite.
for name in ("chain", "resolve", "state_of", "by_id",
             "department_of", "department_is_off"):
    check(f"both indexes answer .{name}",
          hasattr(live, name) and hasattr(index, name))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

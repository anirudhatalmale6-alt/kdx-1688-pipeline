#!/usr/bin/env python3
"""
Checks on the search words - what the shop is allowed to go looking for.

This file exists because of two failures, one editorial and one mechanical, and
most of what is below is an assertion that neither can come back.

  * A night's rotation put five women's lingerie sets into a Saudi shop. The ban
    classifier had done its job - underwear is not banned - but nothing had ever
    asked the client which departments he sells. So: the departments file is the
    line, and a word from a department marked `sell: false` must not exist.

  * The words were taken from the tree's leaves, and at two levels deep a leaf
    is a tail bucket: 加工定制, 项目合作, 代理加盟, 库存. The merchandise -
    连衣裙, 台灯夜灯, 毛巾 - has children and was never a leaf, and seven whole
    retail departments had no allowed leaf at all. So: children of a selling
    department, leaf or not, tail buckets dropped by name.

Run: python3 verify_wordlist.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import catalog  # noqa: E402
import wordlist  # noqa: E402

PASS = FAIL = 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}\n         got:  {got!r}\n         want: {want!r}")


def truthy(label: str, got) -> None:
    check(label, bool(got), True)


def section(title: str) -> None:
    print(f"\n{title}\n" + "-" * len(title))


# A small tree with the shapes that matter: a selling department with a
# non-leaf product category under it, a tail bucket, a retired category, a
# department the client does not sell, and a banned word inside a sold one.
TREE = [
    {"id": 10166, "parent_id": None, "depth": 1, "name_zh": "女装",
     "is_leaf": False, "state": "allowed"},
    {"id": 1, "parent_id": 10166, "depth": 2, "name_zh": "连衣裙",
     "is_leaf": False, "state": "allowed"},
    {"id": 2, "parent_id": 10166, "depth": 2, "name_zh": "女装加工定制",
     "is_leaf": True, "state": "allowed"},
    {"id": 3, "parent_id": 10166, "depth": 2, "name_zh": "童皮衣（停用）",
     "is_leaf": True, "state": "allowed"},
    {"id": 4, "parent_id": 10166, "depth": 2, "name_zh": "毛巾、面巾",
     "is_leaf": False, "state": "allowed"},
    {"id": 5, "parent_id": 10166, "depth": 2, "name_zh": "台灯（欧式）",
     "is_leaf": False, "state": "allowed"},
    {"id": 6, "parent_id": 10166, "depth": 2, "name_zh": "圣诞用品",
     "is_leaf": False, "state": "allowed"},
    {"id": 7, "parent_id": 10166, "depth": 2, "name_zh": "宠物活体",
     "is_leaf": False, "state": "allowed"},
    {"id": 8, "parent_id": 10166, "depth": 2, "name_zh": "配件",
     "is_leaf": False, "state": "allowed"},
    {"id": 9, "parent_id": 10166, "depth": 2, "name_zh": "白酒",
     "is_leaf": False, "state": "blocked"},
    {"id": 65, "parent_id": None, "depth": 1, "name_zh": "机械及行业设备",
     "is_leaf": False, "state": "allowed"},
    {"id": 20, "parent_id": 65, "depth": 2, "name_zh": "机床",
     "is_leaf": False, "state": "allowed"},
]

DEPARTMENTS = [
    {"id": 10166, "name_zh": "女装", "sell": True},
    {"id": 65, "name_zh": "机械及行业设备", "sell": False,
     "why": "آلات ومعدات مصانع"},
]


section("the department the client does not sell contributes nothing")
words = wordlist.build(TREE, DEPARTMENTS)
check("机床 is not a search word", "机床" in words, False)
truthy("连衣裙 is", "连衣裙" in words)

section("a product category is a word even though it is not a leaf")
# This is the whole mechanical fix. 连衣裙 has children on 1688, so it was never
# a leaf, so the first version never asked for it - while 女装加工定制, which
# has nothing under it and sells nothing, was asked for every rotation.
truthy("连衣裙 (not a leaf) is in", "连衣裙" in words)
check("女装加工定制 (a leaf) is out", "女装加工定制" in words, False)

section("the buckets that are categories but not merchandise")
for name in ("加工", "定制", "项目合作", "代理加盟", "库存", "其他"):
    check(f"a name containing {name} yields nothing",
          wordlist.clean(f"女装{name}"), [])

section("names that need taking apart")
check("毛巾、面巾 is two words", wordlist.clean("毛巾、面巾"), ["毛巾", "面巾"])
check("a bracketed qualifier is stripped", wordlist.clean("台灯（欧式）"), ["台灯"])
check("a retired category yields nothing", wordlist.clean("童皮衣（停用）"), [])
check("both separators split", wordlist.clean("睡衣/家居服"), ["睡衣", "家居服"])
check("a one-character part is dropped", wordlist.clean("鞋/女鞋"), ["女鞋"])

section("the ban list applies to a word as much as to a category")
# 圣诞用品 sits under a department he sells, so only the ban list can stop it.
check("圣诞用品 is refused", wordlist.clean("圣诞用品"), [])
check("圣诞 classifies as religious", catalog.classify("圣诞用品")[:2],
      (catalog.BLOCKED, "religious"))
check("祭祀 classifies as religious", catalog.classify("祭祀用品")[:2],
      (catalog.BLOCKED, "religious"))
check("殡葬 classifies as funeral", catalog.classify("殡葬用品")[:2],
      (catalog.BLOCKED, "funeral"))
check("圣诞用品 is not in the built words", "圣诞用品" in words, False)
# And the safe phrases still hold: adding tokens must not delete the catalogue.
check("酒店用品 still allowed", catalog.classify("酒店用品")[0], catalog.ALLOWED)
check("剪刀 still allowed", catalog.classify("剪刀")[0], catalog.ALLOWED)

# A shop in Saudi Arabia should not go looking for decanters, even though the
# category must stay allowed so that 酒店 and 酒精灯 survive the alcohol ban.
check("酒具 is not a search word", wordlist.clean("酒具"), [])
check("but the category itself is still allowed",
      catalog.classify("酒具")[0], catalog.ALLOWED)
check("and hotel linen, which only shares the character, survives",
      wordlist.clean("酒店布草"), ["酒店布草"])

section("what cannot travel in a parcel")
check("live animals are refused", wordlist.clean("宠物活体"), [])
check("live plants are refused", wordlist.clean("园林植物"), [])
check("seed is refused", wordlist.clean("种子"), [])
check("whole motorcycles are refused", wordlist.clean("摩托车配附件"), [])
check("antiques are refused", wordlist.clean("古董"), [])
check("publications are refused", wordlist.clean("出版物"), [])
check("pesticides are refused", wordlist.clean("驱虫防蚊灭鼠杀虫用品"), [])
check("LED driver components are refused", wordlist.clean("LED驱动与控制"), [])
# ...but a bicycle is a product, and 整车 alone is not a licence to drop it.
check("a complete bicycle survives", wordlist.clean("骑行整车"), ["骑行整车"])
check("a lamp survives", wordlist.clean("台灯夜灯"), ["台灯夜灯"])

section("words too broad to be worth a search")
check("配件 alone is dropped", wordlist.clean("配件"), [])
check("安全 alone is dropped", wordlist.clean("安全"), [])
check("配件 is not in the built words", "配件" in words, False)
check("箱包配件 survives - it is specific", wordlist.clean("箱包配件"), ["箱包配件"])

section("a blocked category under a sold department stays blocked")
check("白酒 never becomes a word", "白酒" in words, False)

section("no duplicates, and the order is the tree's")
dupes = [w for w in words if words.count(w) > 1]
check("nothing appears twice", dupes, [])
check("毛巾 comes before 面巾", words.index("毛巾") < words.index("面巾"), True)

section("the day's slice")
ten = [f"w{n}" for n in range(10)]
check("a slice is the size asked for", len(wordlist.slice_for_day(ten, "2026-09-02", 4)), 4)
check("no words, no slice", wordlist.slice_for_day([], "2026-09-02", 4), [])
check("no count, no slice", wordlist.slice_for_day(ten, "2026-09-02", 0), [])
check("a slice larger than the list is the list",
      sorted(wordlist.slice_for_day(ten, "2026-09-02", 99)), sorted(ten))

# The bug this catches: with the offset being the date itself, the window moved
# by one word a day, so twelve words today and eleven of the same twelve
# tomorrow. Consecutive days must not overlap.
today = wordlist.slice_for_day(ten, "2026-09-02", 4)
tomorrow = wordlist.slice_for_day(ten, "2026-09-03", 4)
check("consecutive days do not overlap", set(today) & set(tomorrow), set())
check("the same day gives the same words - two runs, one list",
      wordlist.slice_for_day(ten, "2026-09-02", 4), today)
check("a slice never repeats a word", len(set(today)), len(today))

section("the interleave that spreads a day over the departments")
alphabet = [f"w{n}" for n in range(100)]
spread = wordlist.interleave(alphabet, 10)
check("it is a permutation - nothing lost", sorted(spread), sorted(alphabet))
check("and nothing duplicated", len(set(spread)), len(alphabet))
check("neighbours in the tree are no longer neighbours",
      abs(alphabet.index(spread[1]) - alphabet.index(spread[0])) > 1, True)
check("a short list is left alone", wordlist.interleave(["a", "b"], 4), ["a", "b"])

section("the real departments file")
rows = json.load(open(os.path.join(HERE, "data", "categories.json"), encoding="utf-8"))
departments = wordlist.load_departments()
selling = [d for d in departments if d.get("sell")]
truthy("every department in the tree has a row",
       {str(r["id"]) for r in rows if r.get("depth") == 1}
       <= {str(d["id"]) for d in departments})
truthy("every excluded department says why",
       all(d.get("why") for d in departments if not d.get("sell")))
truthy("underwear is sold - the client asked for it to stay",
       any(str(d["id"]) == "312" and d["sell"] for d in departments))
# 1426 机床 was off until 2 September, when the client asked for it and
# seventeen more industrial departments by name.
truthy("machine tools are sold - he asked for them on 2 September",
       any(str(d["id"]) == "1426" and d["sell"] for d in departments))
# Fifteen of the other seventeen. 橡塑 (55) and 机械及行业设备 (65) came off on
# 4 September - he was shown the twenty products they had put in his shop and
# answered "اوافقك". The rest of that list is untouched, and checking it here is
# what stops a later "close the industrial departments" from quietly taking
# 五金工具 or 电子元器件 with it.
truthy("and so are the other fifteen he named",
       all(any(str(d["id"]) == wanted and d["sell"] for d in departments)
           for wanted in ("1", "5", "9", "4", "13", "57", "59", "64",
                          "68", "70", "71", "72", "509", "10208", "201346017")))
truthy("rubber and industrial machinery are off - his decision of 4 September",
       all(any(str(d["id"]) == closed and not d["sell"] for d in departments)
           for closed in ("55", "65")))
truthy("chemicals are still off - he did not ask for them",
       any(str(d["id"]) == "8" and not d["sell"] for d in departments))
truthy("adult products are not",
       any(str(d["id"]) == "130823000" and not d["sell"] for d in departments))

live = wordlist.build(rows)
truthy("the real list is not empty", live)
truthy("it is bigger than the 458 leaves it replaced", len(live) > 458)
truthy("dresses are in it", "连衣裙" in live)
truthy("shoes are in it - a department with no allowed leaf at all",
       "女鞋" in live)
truthy("tableware is in it - another one", "餐具" in live)
truthy("towels are in it", "毛巾" in live)
check("no machine tools", "机床" in live, False)
check("no chemicals department words", "实验室用品" in live, False)
check("no live animals", "宠物活体" in live, False)
check("no Christmas goods", "圣诞用品" in live, False)

# Tree order groups a department together, so a contiguous window of the list
# is one department. The first live batch under the previous scheme published
# four suitcases, and every batch for the rest of that day would have been
# luggage too - the shop fills one shelf at a time instead of filling up.
section("a day's words are spread over the shop, not taken from one shelf")
parent_of = {}
for row in rows:
    for word in wordlist.clean(str(row.get("name_zh") or "")):
        parent_of.setdefault(word, str(row.get("parent_id")))
picked = wordlist.slice_for_day(live, "2026-09-02", 12)
families = {parent_of.get(word) for word in picked}
truthy(f"twelve words come from {len(families)} different departments",
       len(families) >= 10)
truthy("CONTROL: twelve CONSECUTIVE words really are one or two departments",
       len({parent_of.get(word) for word in live[:12]}) <= 2)
truthy("every day of a month asks a different twelve",
       len({tuple(wordlist.slice_for_day(live, f"2026-09-{d:02d}", 12))
            for d in range(1, 29)}) == 28)
truthy("and no day repeats a word inside itself",
       all(len(set(wordlist.slice_for_day(live, f"2026-09-{d:02d}", 12))) == 12
           for d in range(1, 29)))
for word in live:
    if len(word) < wordlist.MIN_LENGTH:
        check(f"{word} is long enough to search", False, True)
        break
else:
    check("every word is at least two characters", True, True)

section("a department flipped off removes its words, and nothing else")
flipped = [dict(d, sell=False) if str(d["id"]) == "312" else d
           for d in departments]
without = wordlist.build(rows, flipped)
check("underwear words are gone", "文胸" in without, False)
check("dresses are untouched", "连衣裙" in without, True)
truthy("and it is only underwear that went",
       set(live) - set(without) and "连衣裙" not in (set(live) - set(without)))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

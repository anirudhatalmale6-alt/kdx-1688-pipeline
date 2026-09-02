"""
The Chinese words the pool search is allowed to ask for.

Why this module exists
----------------------
The keyword channel decides what the shop can reach: `jxhy.product.getPageList`
serves about 2,000 offers per word, so the word list *is* the catalogue. The
first version took its words from the category tree's `allowed` leaves, and that
was wrong twice over.

Wrong the first time, editorially. `allowed` means "not on the client's ban
list", which is a safety net, not a shopping list. 260 of 452 allowed leaves
were industry and services - machine tools, chemicals, used equipment, business
services - and one night's rotation landed on 内衣礼盒装 and put five women's
lingerie sets into a Saudi shop. A ban classifier can say what must never be
sold; only the client can say what he sells. So the line is drawn in
`data/departments.json`, one row per 1688 department with `sell: true|false`,
and he can flip any row without touching code.

Wrong the second time, mechanically, and this one cost more than it looked.
Asking for leaves gave the wrong *kind* of word. The built tree is two levels
deep, so a depth-2 node is only a leaf when 1688 has nothing under it - and the
categories with nothing under them are the tail buckets every department ends
with:

    加工定制   custom manufacturing        库存五金、工具   clearance stock
    项目合作   project partnerships        代理加盟         franchise enquiries

Real merchandise - 连衣裙, 台灯夜灯, 厨房电器, 毛巾 - has children, so it was
never a leaf and never became a word. Worse, whole retail departments have no
allowed leaf at all and were invisible: 鞋 (shoes), 汽车用品 (car accessories),
宠物及园艺 (pets and gardening), 日用餐厨饮具 (tableware), 收纳清洁用具
(storage and cleaning), 居家日用品, 个护/家清.

So the words are the *children* of a selling department, leaf or not, with the
tail buckets dropped by name.

What a word looks like
----------------------
A category name is not always one word. 毛巾、面巾 is two, and searching for the
pair returns nothing, so names are split on 、 and /. A trailing qualifier in
brackets - 台灯（欧式）- narrows the search to nothing and is stripped. What
comes out is what a supplier would actually type in a title.
"""

from __future__ import annotations

import json
import os
import re

import catalog

# The tail buckets. These are real categories, but nobody sells "project
# partnerships" - a word that matches one buys a walk through offers that are
# enquiries, services and clearance lots rather than products.
TAIL_MARKERS = (
    "加工", "定制", "项目合作", "代理加盟", "库存", "其他", "展示架", "鉴定",
    "制造设备", "检测设备", "配套服务", "原料", "辅料", "芯片", "封装", "模组",
    "回收", "招商", "外发", "来图", "来样", "贴牌", "批发市场", "杂款",
)

# Words that are grammatically fine and commercially useless: a whole department
# name, a service, or a term so broad the pool returns a random slice of 1688.
NOISE = {
    "AR", "合成", "器材", "工作", "电动", "电子", "配件", "配饰", "休闲", "套餐",
    "套装", "服饰", "衣饰", "运动", "增值业务", "手机号码", "气氛", "计时",
    "耗材", "半成品饰品配件", "布置用品", "场地铺设器材", "球类配套器材",
    "裁判", "安全", "应急", "自驾", "改装", "外饰", "山地", "成人", "整车",
}

# Categories inside a department he sells that still must not become a search
# word. Not a ban on the goods - a statement that they cannot cross a border in
# a parcel, or that 1688 will not let them. Each one was read off the generated
# list before it was ever searched.
UNSHIPPABLE = {
    "活体": "live animals",
    "种子": "live plants and seed - phytosanitary",
    "种苗": "live plants and seed - phytosanitary",
    "盆栽": "live plants", "盆景": "live plants", "花卉": "live plants",
    "园林植物": "live plants", "林业": "live plants",
    "摩托车": "whole vehicles", "电动车": "whole vehicles", "汽车配件": "whole vehicles",
    "古董": "antiques - export restricted in China", "古玩": "antiques - export restricted in China",
    "出版物": "printed and recorded media - censorship", "书籍": "printed and recorded media - censorship",
    "音像制品": "printed and recorded media - censorship",
    "正版动漫形象": "licensed characters - trademark risk",
    "明星周边": "celebrity merchandise - likeness risk",
    "杀虫": "pesticides", "驱虫": "pesticides", "消毒抑菌": "regulated disinfectant",
    "实验室": "laboratory chemicals and glassware",
    "游艺设施": "fairground machinery",
    "鞋材": "raw material, not a finished product",
    "电光源材料": "lighting components, not lamps",
    "LED驱动": "lighting components, not lamps",
    "LED背光源": "lighting components, not lamps",
    "冷光源": "lighting components, not lamps",
    "独立光源": "lighting components, not lamps",
    "气体放电灯": "lighting components, not lamps",
    "LED显示屏": "signage hardware", "LED广告标识": "signage hardware",
    "OLED": "display panels, not lamps",
}

# Allowed as a category, wrong as a search word for THIS shop. `catalog` keeps
# 酒具 out of the alcohol ban on purpose - deleting the branch would take the
# 酒店 (hotel) and 酒精灯 (spirit lamp) names with it - but a shop in Saudi
# Arabia should not be going looking for wine glasses and decanters. Blocking
# the word is not the same as blocking the branch, and only the word is blocked
# here.
EDITORIAL = {
    "酒具": "drinking vessels for alcohol",
    "醒酒": "drinking vessels for alcohol",
}

# 1688 retired the category; the name survives in the tree and returns nothing.
RETIRED = "停用"

MIN_LENGTH = 2


def departments_path() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("KDX_DEPARTMENTS",
                          os.path.join(here, "data", "departments.json"))


def load_departments(path: str = "") -> list:
    with open(path or departments_path(), encoding="utf-8") as handle:
        rows = json.load(handle)
    return rows if isinstance(rows, list) else []


def selling_ids(rows: list) -> set:
    """The departments the client says his shop sells, as ids of both types."""
    ids = set()
    for row in rows:
        if not row.get("sell"):
            continue
        ids.add(str(row.get("id")))
    return ids


def clean(name: str) -> list:
    """
    One category name to the words worth searching for. Possibly none.
    """
    if not name or RETIRED in name:
        return []
    if any(marker in name for marker in TAIL_MARKERS):
        return []
    if any(token in name for token in UNSHIPPABLE):
        return []
    if any(token in name for token in EDITORIAL):
        return []
    words = []
    for part in re.split(r"[、/]", name):
        part = re.sub(r"[（(][^）)]*[）)]?", "", part).strip(" 　")
        if len(part) < MIN_LENGTH or part in NOISE:
            continue
        if any(token in part for token in UNSHIPPABLE):
            continue
        if any(token in part for token in EDITORIAL):
            continue
        # The same ban list the categories were filtered with. A word is about
        # to be handed to a search whose results get published, so it has to
        # clear the bar a category clears - and 圣诞用品 sits under a department
        # the client sells.
        state, _, _ = catalog.classify(part)
        if state != catalog.ALLOWED:
            continue
        words.append(part)
    return words


def build(rows: list, departments: list | None = None) -> list:
    """
    Every search word the shop may ask for, in tree order.

    `rows` is the category table - the built tree, or anything that presents the
    same dicts. Order is stable so that the day's rotation is reproducible: the
    same day asks the same words, and a different day asks different ones.
    """
    departments = departments if departments is not None else load_departments()
    selling = selling_ids(departments)
    if not selling:
        return []
    words, seen = [], set()
    for row in rows:
        parent = row.get("parent_id")
        if parent is None or str(parent) not in selling:
            continue
        if row.get("state") != catalog.ALLOWED:
            continue
        for word in clean(str(row.get("name_zh") or "")):
            if word in seen:
                continue
            seen.add(word)
            words.append(word)
    return words


def slice_for_day(words: list, day: str, count: int) -> list:
    """
    The day's share of the list: spread across the whole of it, not a window
    cut out of one place in it.

    The offset is the same for every run within a day - a run at 09:00 and a run
    at 21:00 search the same words, and the ledger keeps the second one from
    republishing what the first one took - and it moves by a whole slice from
    one day to the next. Moving by a whole slice is the point of the
    multiplication: the date read as a number goes up by one a day, so an offset
    of the date itself advanced the window by a single word, twelve words today
    and eleven of the same twelve tomorrow.

    The stride is the second half, and it is the one that shows in the shop.
    `words` is in tree order, so twelve consecutive words are twelve categories
    from the SAME department: the first live batch under this scheme published
    four suitcases, and every batch for the rest of that day would have
    published luggage too. Taking every nth word instead spreads the day over
    the departments - a dress, a lamp, a towel, a pair of shoes - while still
    covering the list exactly once before repeating.
    """
    if not words or count <= 0:
        return []
    total = len(words)
    if count >= total:
        return list(words)
    spread = interleave(words, count)
    stamp = int("".join(ch for ch in str(day) if ch.isdigit()) or "0")
    offset = (stamp * count) % total
    return (spread + spread)[offset:offset + count]


def interleave(words: list, count: int) -> list:
    """
    The same words, reordered so that neighbours are not neighbours in the tree.

    Stepping through the list by a stride coprime with its length visits every
    word exactly once - so nothing is lost and nothing is repeated - while any
    run of `count` consecutive words is drawn from right across it. The stride
    starts near len/count, which is what makes a day's slice reach every
    department, and moves up until it is coprime.

    Doing it here, once, rather than in the slice is what keeps both properties
    at the same time: the day's words are spread over the shop AND two
    consecutive days share none, because the slice is still a plain window - it
    is the list underneath that has been shuffled.
    """
    total = len(words)
    if total < 3 or count <= 0:
        return list(words)
    stride = max(1, total // max(count, 1))
    while stride < total and _gcd(stride, total) != 1:
        stride += 1
    if _gcd(stride, total) != 1:
        return list(words)
    return [words[(n * stride) % total] for n in range(total)]


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a

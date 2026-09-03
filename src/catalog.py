"""
Stage 0: the category tree, translated and filtered, built once and kept.

This is the only stage that can run today: alibaba.category.get is the single
1688 API the current key is permitted to call. Everything here is deliberately
cached to disk, because every call spends from the same 300 points a day that
the product pull will need later, and a category tree does not change hourly.

Why the filter lives here and not next to the products
------------------------------------------------------
The client's rule is that banned goods never get pulled. Deciding that from a
product title means the point has already been spent to read the product. A
category is decided from a name that cost nothing extra, and a blocked branch is
never descended into - so the veto happens before the spend, which is what he
asked for.

Why there are three states and not two
--------------------------------------
Matching banned words against Chinese category names produces false positives
that would quietly delete good branches:

    水枪   water pistol (a toy)          contains 枪 "gun"
    喷枪   spray gun (a tool)            contains 枪 "gun"
    剪刀   scissors                      contains 刀 "knife"
    酒店用品 hotel supplies              contains 酒 "alcohol"

So an unambiguous word blocks, an ambiguous one is only FLAGGED. Flagged
branches are held back rather than published, because selling alcohol in Saudi
Arabia is a legal problem while losing the scissors branch is a lost sale - but
they are written to their own file so the client can release any of them by id,
instead of the system silently deciding on his behalf.
"""

from __future__ import annotations

import json
import os
import re

import liquids

# Unambiguous: the word only appears in names that genuinely are the banned
# thing. Reason codes match the ones rules.BANNED_TERMS already uses, plus the
# two that only matter at category level.
BLOCK_TOKENS = {
    "成人用品": "sexual", "情趣": "sexual", "性用品": "sexual", "避孕": "sexual",
    "电子烟": "tobacco", "香烟": "tobacco", "烟具": "tobacco", "水烟": "tobacco",
    "烟草": "tobacco", "卷烟": "tobacco",
    "仿真枪": "weapons", "弓弩": "weapons", "军刀": "weapons", "弹药": "weapons",
    "枪支": "weapons", "武器": "weapons", "催泪": "weapons",
    "宗教": "religious", "佛像": "religious", "佛教": "religious", "基督": "religious",
    "十字架": "religious", "念珠": "religious", "香炉": "religious",
    # Added 2 September, when the departments the client sells started producing
    # the search words themselves. These sit under 办公、文化 and 家纺家饰 -
    # departments he does sell - so nothing above them would have stopped
    # 圣诞用品 (Christmas goods) or 祭祀 (ancestral offerings) being asked for.
    "圣诞": "religious", "祭祀": "religious", "佛珠": "religious",
    "殡葬": "funeral", "骨灰": "funeral", "寿衣": "funeral",
    "白酒": "alcohol", "红酒": "alcohol", "啤酒": "alcohol", "洋酒": "alcohol",
    "葡萄酒": "alcohol", "黄酒": "alcohol", "米酒": "alcohol", "酒类": "alcohol",
    "猪肉": "pork", "猪皮": "pork",
    "大麻": "drugs", "毒品": "drugs",
    "高仿": "counterfeit", "仿牌": "counterfeit",
    # Added 2 September with the eighteen industrial departments the client
    # asked for. These are not editorial judgements - his line is his to draw -
    # they are things that cannot arrive at a customer in Riyadh at all:
    #
    #   batteries and power banks are dangerous goods by air, the same class of
    #     risk as the liquids he banned on 1 September
    #   a drone needs a GACA permit to enter Saudi Arabia
    #   a whole car is not a parcel
    #   a petrol generator or a fuel dispenser carries a fuel system
    #   live plants and animals need phytosanitary and veterinary papers
    #
    # 电池 also catches 电池座, an empty battery holder, which is ordinary
    # merchandise. That is one over-reach in exchange for never shipping a
    # lithium cell by mistake, and the client has been told so he can overrule.
    "电池": "dangerous_goods", "蓄电池": "dangerous_goods",
    "充电宝": "dangerous_goods", "充电桩": "dangerous_goods",
    "无人机": "restricted_import",
    # 整车 is NOT here. It means "the complete unit" and a bicycle is sold that
    # way - 自行车整车 is a whole bike, which ships. The suite caught it.
    "乘用车": "vehicle", "商用车": "vehicle", "专用汽车": "vehicle",
    "二手汽车": "vehicle",
    "加油站": "fuel_system", "发电机": "fuel_system",
    "果树": "live_plants", "苗木": "live_plants", "种苗": "live_plants",
    "养殖动物": "live_animals", "畜牧": "live_animals", "活体": "live_animals",
}

# Ambiguous: the word is banned in some names and ordinary merchandise in
# others. Never blocks by itself - it flags for the client to decide.
REVIEW_TOKENS = {
    "枪": "weapons", "刀": "weapons", "弓": "weapons",
    "酒": "alcohol",
    "烟": "tobacco",
    "药": "medicine",
    "猪": "pork",
}

# Names where the ambiguous word is part of an innocent compound. Checked before
# the review tokens, so these stay in the catalogue instead of being flagged.
# Several of these were not guessed: they came out of the flagged list of the
# first real walk of the 1688 tree, where a screwdriver (手动螺丝刀) and
# beverage packaging (酒水饮料包装) had been flagged as a weapon and as drink.
SAFE_PHRASES = (
    "水枪", "喷枪", "胶枪", "打钉枪", "气枪钉", "热熔胶枪", "洗车枪", "测温枪",
    "剪刀", "菜刀", "刀具", "美工刀", "剃须刀", "指甲刀", "削皮刀", "切菜",
    "螺丝刀", "刀剪", "刀片", "刨刀", "刀架", "壁纸刀", "开箱刀",
    "酒店", "酒柜", "酒架", "酒具", "醒酒器", "酒精灯", "消毒酒精",
    "包装",
    "烟灰缸", "抽油烟机", "烟感", "排烟", "油烟",
    "药箱", "药盒", "医药箱", "药膏贴", "制药设备", "制药辅料", "药用包装",
    "猪笼草",
)

ALLOWED, BLOCKED, REVIEW = "allowed", "blocked", "review"


def classify(name_zh: str) -> tuple[str, str, str]:
    """
    Return (state, reason_code, matched_token) for one category name.

    Order matters: an unambiguous ban wins over a safe phrase, so a name like
    "情趣用品酒店" is still blocked.
    """
    name = (name_zh or "").strip()
    if not name:
        return ALLOWED, "", ""

    for token, reason in BLOCK_TOKENS.items():
        if token in name:
            return BLOCKED, reason, token

    for phrase in SAFE_PHRASES:
        if phrase in name:
            return ALLOWED, "", ""

    # Liquids, 1 September. Blocking the department is worth more than blocking
    # the product: a blocked branch is never descended, so its children cost no
    # points and never reach the product filter at all.
    #
    # It sits BELOW the safe phrases on purpose. Put above them, "饮料" would
    # have deleted 酒水饮料包装 - beverage PACKAGING, which is cardboard - and
    # that exact name is why SAFE_PHRASES exists. liquids.py carries its own
    # exemptions too, so 香水瓶 (empty bottles) survives both lists.
    liquid = liquids.find_liquid_term(name)
    if liquid:
        return BLOCKED, f"liquid_{liquid[0]}", liquid[1]

    for token, reason in REVIEW_TOKENS.items():
        if token in name:
            return REVIEW, reason, token

    return ALLOWED, "", ""


def departments_off(path: str = "") -> dict:
    """
    The departments the client turned OFF, by Chinese name.

    His choice of 2 September lives in data/departments.json as `sell: false`,
    and until 3 September that file was read by exactly one caller - wordlist.py,
    to decide which words to SEARCH for. Nothing enforced it, so a department he
    switched off could still arrive if the pool handed us an offer filed under
    one. A list that selects search words is not a filter; this makes it one.

    It is defence in depth and nothing more, which the measurement says plainly:
    scored over all 241 products assembled so far it catches ZERO. Not choosing
    the words has been enough in practice. It is here so that "enough in
    practice" stops being the only thing standing between his decision and the
    shop.

    In particular it is NOT what caught the thirteen rubber compounds published
    on 3 September. Those are filed under 橡塑, and 橡塑 has `sell: true` -
    he asked for it himself on 2 September along with seventeen other industrial
    departments. They are excluded by the chemicals rule in rules.BANNED_TERMS,
    on the words of the listing, not by this.
    """
    if not path:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "data", "departments.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)
    return {row["name_zh"]: (row.get("why") or "department off")
            for row in rows if not row.get("sell")}


class NodeCache:
    """
    Every category node ever fetched, kept on disk.

    A second run of the builder costs zero API points. That matters more here
    than it looks: the tree has thousands of nodes and the client only has 300
    points a day for the whole system.
    """

    def __init__(self, path: str):
        self.path = path
        self.nodes: dict = {}
        self.hits = self.misses = 0
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                self.nodes = json.load(handle)

    def get(self, category_id, fetch) -> dict:
        key = str(category_id)
        if key in self.nodes:
            self.hits += 1
            return self.nodes[key]
        node = fetch(category_id)
        self.misses += 1
        self.nodes[key] = node
        return node

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.nodes, handle, ensure_ascii=False, indent=1, sort_keys=True)


class BudgetExhausted(RuntimeError):
    """Raised when the walk has spent every call it was allowed."""


def build_tree(fetch, cache: NodeCache, *, max_depth: int = 2,
               max_calls: int | None = None, root_id=0) -> list:
    """
    Walk the tree breadth-first and return one flat row per category.

    `fetch(category_id) -> node` is injected so this runs against a live client,
    a recorded tree, or a stub with no network at all.

    A blocked branch is recorded and NOT descended into. That is the whole point
    of filtering here: the children of 成人用品 cost nothing to exclude.
    """
    rows: list = []
    seen: set = set()
    started_with = cache.misses

    root = cache.get(root_id, fetch)
    queue = [(child, (), 1) for child in root.get("childCategorys", [])]

    while queue:
        node, path, depth = queue.pop(0)
        category_id = node.get("id") or node.get("categoryID")
        if category_id is None or category_id in seen:
            continue
        seen.add(category_id)

        name = node.get("name", "")
        state, reason, token = classify(name)
        here = path + (name,)
        is_leaf = bool(node.get("isLeaf"))

        rows.append({
            "id": category_id,
            "parent_id": None,
            "depth": depth,
            "name_zh": name,
            "path_zh": " > ".join(here),
            "is_leaf": is_leaf,
            "state": state,
            "reason": reason,
            "matched": token,
            "expanded": True,
        })

        if state == BLOCKED or is_leaf or depth >= max_depth:
            continue

        # A cached node is free, so the cap only stops nodes that would cost a
        # call. Stop expanding rather than half-expanding: a branch that is
        # incomplete but looks complete is worse than one openly missing.
        cached = str(category_id) in cache.nodes
        if not cached and max_calls is not None and cache.misses - started_with >= max_calls:
            row = rows[-1]
            row["expanded"] = False
            continue

        detail = cache.get(category_id, fetch)
        for child in detail.get("childCategorys", []):
            child_id = child.get("id") or child.get("categoryID")
            if child_id in seen:
                continue
            queue.append((child, here, depth + 1))

    # Fill parent ids from the paths, which is cheaper than threading them
    # through the queue and cannot disagree with the printed path.
    by_path = {row["path_zh"]: row["id"] for row in rows}
    for row in rows:
        parent_path = " > ".join(row["path_zh"].split(" > ")[:-1])
        row["parent_id"] = by_path.get(parent_path)

    return rows


LATIN_ONLY = re.compile(r"^[\w\s\-/&.,()]+$", re.ASCII)


def translate_rows(rows: list, translator, cache_path: str, batch: int = 40,
                   retry_fallbacks: bool = False) -> dict:
    """
    Translate every distinct category name once, and keep the result.

    Names are translated, not paths: the same word appears in dozens of paths
    and paying for it more than once is waste. Anything the translator omits
    falls back to the original rather than vanishing.

    The fallback is written to the cache as well, marked `fallback`. Without
    that, a name the model keeps omitting is re-sent on every single run - this
    is a nightly job, so "asked again next time" means "paid for forever". Pass
    retry_fallbacks=True to deliberately have another go at just those.
    """
    names = {row["name_zh"] for row in rows if row["name_zh"]}

    known: dict = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as handle:
            known = json.load(handle)

    def needs_work(name: str) -> bool:
        entry = known.get(name)
        if entry is None:
            return True
        return retry_fallbacks and bool(entry.get("fallback"))

    todo = sorted(name for name in names if needs_work(name))
    for index in range(0, len(todo), batch):
        chunk = todo[index:index + batch]
        answered = translator(chunk) or {}
        for name in chunk:
            entry = answered.get(name) or {}
            english, arabic = str(entry.get("en") or "").strip(), str(entry.get("ar") or "").strip()
            if english and arabic:
                known[name] = {"en": english, "ar": arabic}
            else:
                known[name] = {"en": name, "ar": name, "fallback": True}
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as handle:
            # Written after every batch: a run interrupted at batch nine keeps
            # the eight already paid for.
            json.dump(known, handle, ensure_ascii=False, indent=1, sort_keys=True)

    for row in rows:
        entry = known.get(row["name_zh"]) or {}
        row["name_en"] = entry.get("en") or row["name_zh"]
        row["name_ar"] = entry.get("ar") or row["name_zh"]
    return known


class CategoryIndex:
    """
    The built tree, used to answer the two questions a product run asks of it:
    what does KDX show as this product's department, and may we sell it.
    """

    def __init__(self, rows: list):
        self.rows = rows
        self.by_id = {str(row["id"]): row for row in rows}

    @classmethod
    def load(cls, path: str):
        if not os.path.exists(path):
            return cls([])
        with open(path, encoding="utf-8") as handle:
            return cls(json.load(handle))

    def _element(self, row: dict) -> dict:
        """One category in the exact shape the client's schema asks for."""
        return {"id": row["id"], "name_original": row["name_zh"],
                "name_en": row.get("name_en") or row["name_zh"],
                "name_ar": row.get("name_ar") or row["name_zh"]}

    def _ancestors(self, row: dict) -> list:
        chain = [row]
        seen = {str(row["id"])}
        while chain[0].get("parent_id") is not None:
            parent = self.by_id.get(str(chain[0]["parent_id"]))
            if parent is None or str(parent["id"]) in seen:
                break
            seen.add(str(parent["id"]))
            chain.insert(0, parent)
        return chain

    def chain(self, category_id) -> list:
        """
        Root first, leaf last. Empty when the id is not in the built tree.

        The same shape LiveIndex.chain answers with, so a caller that only wants
        the ancestry does not have to know which index it was handed.
        """
        row = self.by_id.get(str(category_id or ""))
        return self._ancestors(row) if row is not None else []

    def resolve(self, category_id) -> tuple:
        """
        Return (main_category, sub_category) for a 1688 category id.

        A category we have never walked returns (None, None) rather than a
        guess. An empty department in KDX is a visible gap; a wrong one is a
        product filed under the wrong menu, which nobody notices.
        """
        row = self.by_id.get(str(category_id or ""))
        if row is None:
            return None, None
        chain = self._ancestors(row)
        main = self._element(chain[0])
        sub = self._element(chain[-1]) if len(chain) > 1 else None
        return main, sub

    def state_of(self, category_id) -> str:
        """
        allowed / blocked / review for a known category, "unknown" otherwise.

        Deliberately not "allowed by default": the children of a blocked branch
        were never walked, so they are absent from the tree for the same reason
        a category we simply have not reached yet is absent. Treating absent as
        allowed would let exactly the branch we excluded back in.
        """
        row = self.by_id.get(str(category_id or ""))
        return row["state"] if row else "unknown"

    def department_of(self, category_id) -> str:
        """The Chinese name of the top-level department, or "" if unknown."""
        chain = self.chain(category_id)
        return chain[0]["name_zh"] if chain else ""

    def department_is_off(self, category_id, off: dict | None = None) -> str:
        """
        The client's reason for switching this product's department off, or "".

        Answers "" for a department we cannot resolve rather than guessing.
        That is NOT the same permissive default as state_of's, and the
        difference is deliberate: state_of guards categories whose children were
        never walked, while this guards a list of 31 names the client wrote
        himself. A product whose department cannot be resolved is caught by the
        product-level rules instead, where the words are read directly.
        """
        name = self.department_of(category_id)
        if not name:
            return ""
        return (departments_off() if off is None else off).get(name, "")


def summarise(rows: list) -> dict:
    counts: dict = {ALLOWED: 0, BLOCKED: 0, REVIEW: 0}
    reasons: dict = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
        if row["state"] != ALLOWED:
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    return {"total": len(rows), "by_state": counts, "by_reason": reasons,
            "leaves": sum(1 for row in rows if row["is_leaf"])}

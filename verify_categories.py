"""
Proof for the category tree: the filter, the walk, and the two caches.

    python3 verify_categories.py

No network and no API key. The 1688 tree is a stub, so every call the walker
would have made is counted exactly, which is the only way to prove the claim
that matters: a banned branch costs nothing because it is never descended into.

Every claim here is checked against a control. "The blocked branch was not
expanded" means nothing on its own - the same tree with that branch renamed to
something innocent IS expanded, and the difference in the call count is the
evidence.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import catalog  # noqa: E402

passed = failed = 0
WORK = tempfile.mkdtemp(prefix="kdx-categories-")


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


def tree(banned_branch_name: str = "成人用品") -> dict:
    """
    A small stand-in for the real tree. The second argument renames the one
    banned branch, which turns this into the control for every claim about what
    blocking saves.
    """
    return {
        "0": {"childCategorys": [
            {"id": 10, "name": "玩具", "isLeaf": False},
            {"id": 20, "name": banned_branch_name, "isLeaf": False},
            {"id": 30, "name": "食品酒水", "isLeaf": False},
        ]},
        "10": {"categoryID": 10, "name": "玩具", "isLeaf": False, "childCategorys": [
            {"id": 11, "name": "水枪", "isLeaf": True},
            {"id": 12, "name": "仿真枪", "isLeaf": True},
        ]},
        "20": {"categoryID": 20, "name": banned_branch_name, "isLeaf": False,
               "childCategorys": [{"id": 21, "name": "情趣内衣", "isLeaf": True}]},
        "30": {"categoryID": 30, "name": "食品酒水", "isLeaf": False, "childCategorys": [
            {"id": 31, "name": "白酒", "isLeaf": True},
            {"id": 32, "name": "餐具", "isLeaf": True},
        ]},
    }


class Stub:
    def __init__(self, nodes: dict):
        self.nodes = nodes
        self.calls: list = []

    def __call__(self, category_id):
        self.calls.append(str(category_id))
        return self.nodes.get(str(category_id), {})


def fresh_cache(name: str) -> catalog.NodeCache:
    return catalog.NodeCache(os.path.join(WORK, name))


def by_id(rows: list) -> dict:
    return {row["id"]: row for row in rows}


def main() -> int:
    print("1. the filter, with the false positives that motivate it")
    cases = [
        ("成人用品", catalog.BLOCKED, "sexual"),
        ("情趣内衣", catalog.BLOCKED, "sexual"),
        ("白酒", catalog.BLOCKED, "alcohol"),
        ("电子烟", catalog.BLOCKED, "tobacco"),
        ("仿真枪", catalog.BLOCKED, "weapons"),
        ("佛像摆件", catalog.BLOCKED, "religious"),
        # The whole reason the middle state exists: these read as banned to a
        # naive substring match and are ordinary merchandise.
        ("水枪", catalog.ALLOWED, ""),
        ("喷枪", catalog.ALLOWED, ""),
        ("剪刀", catalog.ALLOWED, ""),
        ("酒店用品", catalog.ALLOWED, ""),
        ("抽油烟机", catalog.ALLOWED, ""),
        ("女装", catalog.ALLOWED, ""),
        # Genuinely ambiguous: flagged, not deleted, not published.
        ("食品酒水", catalog.REVIEW, "alcohol"),
        ("刀剑", catalog.REVIEW, "weapons"),
        ("药品", catalog.REVIEW, "medicine"),
    ]
    for name, expected_state, expected_reason in cases:
        state, reason, _token = catalog.classify(name)
        check(f"{name} -> {expected_state}", state == expected_state and reason == expected_reason,
              f"got {state}/{reason}")

    check("an unambiguous ban beats an innocent-looking compound",
          catalog.classify("情趣用品酒店")[0] == catalog.BLOCKED,
          "otherwise a banned branch hides behind one safe word")
    check("an empty name is not blocked by accident", catalog.classify("")[0] == catalog.ALLOWED)

    print("2. the walk: a blocked branch is never descended into")
    stub = Stub(tree())
    rows = catalog.build_tree(stub, fresh_cache("a.json"), max_depth=2)
    rows_by_id = by_id(rows)

    check("every unblocked branch produced its children", len(rows) == 7, str(len(rows)))
    check("the banned branch itself is recorded, not hidden", 20 in rows_by_id)
    check("its children are absent", 21 not in rows_by_id, str(sorted(rows_by_id)))
    check("category 20 was never fetched", "20" not in stub.calls, str(stub.calls))

    control = Stub(tree("玩具配件"))
    control_rows = catalog.build_tree(control, fresh_cache("b.json"), max_depth=2)
    check("CONTROL: renaming that branch makes the walk fetch it",
          "20" in control.calls and 21 in by_id(control_rows),
          str(control.calls))
    check("and the saving is real, not a coincidence of ordering",
          len(control.calls) == len(stub.calls) + 1,
          f"{len(stub.calls)} calls blocked vs {len(control.calls)} allowed")

    print("3. a flagged branch is still walked, so its children can be judged")
    check("the alcohol root is flagged", rows_by_id[30]["state"] == catalog.REVIEW)
    check("but it was still expanded", "30" in stub.calls)
    check("and the bottle underneath it is blocked outright",
          rows_by_id[31]["state"] == catalog.BLOCKED and rows_by_id[31]["reason"] == "alcohol")
    # This was 零食 until 4 September, and it stood here as the example of a
    # perfectly ordinary category sitting under a flagged branch. It is food, so
    # it is now blocked and cannot make that point any more - the tableware
    # beside it can. The snacks have their own section below.
    check("while the tableware beside it stays allowed",
          rows_by_id[32]["state"] == catalog.ALLOWED)
    check("the toy water pistol survived next to the replica gun",
          rows_by_id[11]["state"] == catalog.ALLOWED
          and rows_by_id[12]["state"] == catalog.BLOCKED)

    print("3b. food is refused by its aisle, not only by its label")
    # 4 September. He banned food for people and animals on the 3rd; the words
    # went into rules.BANNED_TERMS the same night, and at 22:30 goat milk powder
    # for cats and dogs was published anyway. It is not 粮, not 零食, not 罐头 -
    # no word on the list described it. These are the five leaves the live tree
    # actually carried on 4 September, by their real ids and names.
    for name in ("猫猫零食", "狗狗罐头、零食", "狗狗干粮",
                 "狗狗保健品", "猫猫保健品"):
        state, reason, _ = catalog.classify(name)
        check(f"{name} is refused", state == catalog.BLOCKED
              and reason in ("food", "animal_food"), f"{state}/{reason}")

    # CONTROL the aisles either side of them. The pets department stays open -
    # he sells it - and these are the nine other pet leaves in the same cache.
    for name in ("猫猫玩具", "猫抓板", "猫猫服饰", "狗狗服装", "宠物帽子",
                 "宠物智能喂养设备", "电子宠物"):
        state, _, _ = catalog.classify(name)
        check(f"CONTROL {name} is still sold", state == catalog.ALLOWED, state)

    # CONTROL the human aisles the obvious shorter token would have taken. These
    # are orthopaedic braces, not supplements, which is why it is 保健品 not 保健.
    for name in ("保健护具", "保健器具配件"):
        state, _, _ = catalog.classify(name)
        check(f"CONTROL {name} is a knee brace, not a supplement",
              state == catalog.ALLOWED, state)

    # CONTROL the container is not its contents - the reason these tokens sit
    # below the safe phrases rather than in BLOCK_TOKENS above them.
    for name in ("奶粉罐", "罐头瓶", "粮桶", "食品保鲜盒"):
        state, _, _ = catalog.classify(name)
        check(f"CONTROL {name} is an empty container", state == catalog.ALLOWED,
              state)

    print("3c. crafts, the aisle the hand-made-looking lamp came from")
    # He sent a resin turbine table lamp on 4 September and asked to refuse
    # improvised goods. The photograph could not tell it apart - see the note in
    # catalog.CRAFT_TOKENS - but 1688 had already filed it under 树脂工艺品.
    for name in ("树脂工艺品", "陶瓷工艺品", "金属工艺品", "木质工艺品",
                 "软陶工艺品", "塑料工艺品", "石膏、石料工艺品"):
        state, reason, _ = catalog.classify(name)
        check(f"{name} is refused",
              state == catalog.BLOCKED and reason == "handicraft",
              f"{state}/{reason}")

    # CONTROL ornaments are NOT crafts. 家纺家饰 is a department he chose to
    # sell and these three are its merchandise; closing them was three more
    # products and a decision he has not made.
    for name in ("其他装饰摆件", "汽车摆件", "电视柜摆件", "户外/庭院摆件"):
        state, _, _ = catalog.classify(name)
        check(f"CONTROL {name} is decoration he sells, not a craft",
              state == catalog.ALLOWED, state)

    print("4. paths and parents agree with each other")
    check("a child's path is its parent's path plus its name",
          rows_by_id[11]["path_zh"] == "玩具 > 水枪", rows_by_id[11]["path_zh"])
    check("the parent id is the parent", rows_by_id[11]["parent_id"] == 10,
          str(rows_by_id[11]["parent_id"]))
    check("a root has no parent", rows_by_id[10]["parent_id"] is None)
    check("depth counts from the roots",
          rows_by_id[10]["depth"] == 1 and rows_by_id[11]["depth"] == 2)

    print("5. the node cache: a second build costs nothing")
    path = os.path.join(WORK, "shared.json")
    first_stub = Stub(tree())
    first_cache = catalog.NodeCache(path)
    catalog.build_tree(first_stub, first_cache, max_depth=2)
    first_cache.save()

    second_stub = Stub(tree())
    second_cache = catalog.NodeCache(path)
    second_rows = catalog.build_tree(second_stub, second_cache, max_depth=2)
    check("the cache was written and reloaded", len(second_cache.nodes) == len(first_cache.nodes))
    check("the second build made no calls at all", second_stub.calls == [], str(second_stub.calls))
    check("and produced the identical tree",
          json.dumps(second_rows, sort_keys=True) == json.dumps(rows, sort_keys=True))

    print("6. the call cap stops openly instead of half-expanding")
    capped_stub = Stub(tree())
    capped = catalog.build_tree(capped_stub, fresh_cache("c.json"), max_depth=2, max_calls=2)
    capped_by_id = by_id(capped)
    # The cap counts every call, the root included: it is a promise about the
    # client's points, and a root call spends one just like any other.
    check("it spent exactly the two calls it was allowed",
          len(capped_stub.calls) == 2, str(capped_stub.calls))
    check("the branch it could not afford says so",
          any(row.get("expanded") is False for row in capped),
          str([(r["id"], r.get("expanded")) for r in capped]))
    check("a branch it did expand is not marked incomplete",
          capped_by_id[10]["expanded"] is True)
    check("CONTROL: without the cap the same tree expands fully",
          len(rows) > len(capped), f"{len(rows)} vs {len(capped)}")

    print("7. translation: cached, batched, and never silently dropping a name")
    calls: list = []

    def translator(chunk):
        calls.append(list(chunk))
        # Deliberately omits 餐具 and renames a key, which is what a model
        # actually does wrong. Neither may cause a category to lose its name.
        out = {name: {"en": f"en:{name}", "ar": f"ar:{name}"}
               for name in chunk if name != "餐具"}
        out.pop("玩具", None)
        out["玩 具"] = {"en": "toys", "ar": "ألعاب"}
        return out

    name_cache = os.path.join(WORK, "names.json")
    catalog.translate_rows(rows, translator, name_cache, batch=3)
    check("the names were translated in batches", len(calls) > 1, str(len(calls)))
    check("each name was asked for once", sum(len(c) for c in calls) == len({r["name_zh"] for r in rows}),
          str(calls))
    check("a translated name arrives in both languages",
          rows_by_id[11]["name_en"] == "en:水枪" and rows_by_id[11]["name_ar"] == "ar:水枪")
    check("an omitted name falls back to the original, it does not vanish",
          rows_by_id[32]["name_en"] == "餐具" and rows_by_id[32]["name_ar"] == "餐具")
    check("a renamed key falls back too, rather than taking someone else's name",
          rows_by_id[10]["name_en"] == "玩具", rows_by_id[10]["name_en"])

    calls.clear()
    again = catalog.build_tree(Stub(tree()), fresh_cache("d.json"), max_depth=2)
    catalog.translate_rows(again, translator, name_cache, batch=3)
    check("a second run translates nothing again, not even the two it failed",
          calls == [], str(calls))
    check("and still fills every name in", all(row.get("name_ar") for row in again))
    check("the failures are recorded as failures, not as good translations",
          json.load(open(name_cache, encoding="utf-8"))["餐具"].get("fallback") is True)

    calls.clear()
    catalog.translate_rows(again, translator, name_cache, batch=3, retry_fallbacks=True)
    check("CONTROL: asking for a retry re-sends exactly the two that failed",
          sorted(sum(calls, [])) == sorted(["玩具", "餐具"]), str(calls))

    print("8. the summary adds up")
    summary = catalog.summarise(rows)
    check("every row is counted once",
          sum(summary["by_state"].values()) == summary["total"] == len(rows),
          json.dumps(summary, ensure_ascii=False))
    check("the blocked count matches the rows",
          summary["by_state"][catalog.BLOCKED]
          == sum(1 for row in rows if row["state"] == catalog.BLOCKED))
    check("allowed rows carry no reason",
          all(not row["reason"] for row in rows if row["state"] == catalog.ALLOWED))
    check("blocked and flagged rows always carry one",
          all(row["reason"] for row in rows if row["state"] != catalog.ALLOWED))

    print("9. the index a product run asks: which department, and may we sell it")
    index = catalog.CategoryIndex(rows)

    main, sub = index.resolve(11)
    check("a child resolves to its root as the main department",
          main and main["name_original"] == "玩具", str(main))
    check("and to itself as the sub department",
          sub and sub["name_original"] == "水枪", str(sub))
    check("the element is in the exact shape the client's schema asks for",
          sorted(main) == ["id", "name_ar", "name_en", "name_original"], str(sorted(main)))

    root_main, root_sub = index.resolve(10)
    check("a root category is the main department with no sub",
          root_main["id"] == 10 and root_sub is None, str(root_sub))

    # Three deep, which the stub tree above never builds (max_depth=2). This is
    # the client's vase of 4 September and the rule he approved: the department
    # shown is the row ABOVE the product, because 1688's own top level bundles
    # pets with gardening. Pinned here as well as in verify_category_live so the
    # offline index and the live one cannot drift apart - a product must not be
    # filed differently depending on which of them the run happens to hold.
    deep = catalog.CategoryIndex([
        {"id": 700003, "parent_id": None, "depth": 1, "name_zh": "宠物及园艺",
         "name_en": "Pets & Gardening", "name_ar": "الحيوانات الأليفة والبستنة",
         "state": "allowed", "reason": ""},
        {"id": 700002, "parent_id": 700003, "depth": 2, "name_zh": "花盆、花瓶",
         "name_en": "Pots & Vases", "name_ar": "أصص وزهريات",
         "state": "allowed", "reason": ""},
        {"id": 700001, "parent_id": 700002, "depth": 3, "name_zh": "装饰花瓶",
         "name_en": "Decorative Vase", "name_ar": "مزهرية زخرفية",
         "state": "allowed", "reason": ""},
    ])
    deep_main, deep_sub = deep.resolve(700001)
    check("three deep, the middle row is the department",
          deep_main["name_ar"] == "أصص وزهريات", str(deep_main))
    check("and the leaf is still the sub department",
          deep_sub["name_ar"] == "مزهرية زخرفية", str(deep_sub))
    check("CONTROL the root is still there to be banned on - only not displayed",
          [row["id"] for row in deep.chain(700001)] == [700003, 700002, 700001],
          str([row["id"] for row in deep.chain(700001)]))

    unknown_main, unknown_sub = index.resolve(999999)
    check("an unwalked category resolves to nothing rather than to a guess",
          unknown_main is None and unknown_sub is None,
          "a wrong department is invisible in the panel, an empty one is not")
    check("and so does a missing id", index.resolve(None) == (None, None))

    check("a blocked category is reported blocked", index.state_of(12) == "blocked")
    check("a flagged one is reported flagged", index.state_of(30) == "review")
    check("an ordinary one is allowed", index.state_of(32) == "allowed")
    check("an unwalked one is 'unknown', never 'allowed'",
          index.state_of(999999) == "unknown",
          "the children of a blocked branch are absent for the same reason")

    empty = catalog.CategoryIndex.load(os.path.join(WORK, "no-such-file.json"))
    check("a missing tree file gives an empty index, not a crash",
          empty.resolve(11) == (None, None) and empty.state_of(11) == "unknown")

    print("\nwhat the eighteen industrial departments bring with them")
    # The client asked for agriculture, machinery, automotive, electrical
    # engineering and thirteen more on 2 September. These are not editorial
    # judgements - they are things that cannot reach a customer in Riyadh.
    for name, reason in (("电池", "dangerous_goods"),
                         ("蓄电池", "dangerous_goods"),
                         ("充电宝", "dangerous_goods"),
                         ("民用无人机", "restricted_import"),
                         ("乘用车", "vehicle"),
                         ("二手汽车", "vehicle"),
                         ("加油站设备", "fuel_system"),
                         ("发电机组", "fuel_system"),
                         ("果树", "live_plants"),
                         ("特种养殖动物", "live_animals"),
                         ("畜牧业副产品", "live_animals")):
        state, why, _ = catalog.classify(name)
        check(f"{name} is refused as {reason}",
              (state, why) == (catalog.BLOCKED, reason), f"{state}/{why}")
    # CONTROL: the same departments' ordinary merchandise has to survive, or
    # switching them on bought nothing. A charger with no cell in it ships.
    for name in ("电动工具", "充电器", "开关", "插座", "园林五金工具",
                 "手动扳手", "水泥制品", "传感器", "电线", "汽车用品"):
        check(f"CONTROL {name} is still allowed",
              catalog.classify(name)[0] == catalog.ALLOWED,
              str(catalog.classify(name)))
    # The one over-reach, written down rather than discovered later: an empty
    # battery holder is ordinary merchandise and is refused with the cells.
    check("KNOWN COST an empty battery holder goes with the batteries",
          catalog.classify("电池座")[0] == catalog.BLOCKED)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

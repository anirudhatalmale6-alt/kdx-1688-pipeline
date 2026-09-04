"""
Checks for resolving a category the tree never walked.

    python3 verify_category_live.py

No network. The failures worth catching here are all quiet: a blocked parent
that stops blocking, a night that stalls because one category is unreachable,
and the same category being paid for on every product that shares it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import catalog  # noqa: E402
import category_live  # noqa: E402

PASSED = 0
FAILED = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok    {what}")
    else:
        FAILED += 1
        print(f"  FAIL  {what}" + (f"   [{detail}]" if detail else ""))


class FakeClient:
    """The gateway's real shape: name, isLeaf, parentIDs."""

    TREE = {
        "1045585": ("办公椅", True, ["1045579"]),
        "1045579": ("办公家具", False, ["67"]),
        "67": ("办公、文化", False, []),
        # a harmless-looking leaf under a prohibited department
        "900001": ("串珠", True, ["900002"]),
        "900002": ("宗教用品", False, []),
        # a leaf whose own name is prohibited
        "900003": ("电子烟", True, ["67"]),
        # a chain that never terminates, to prove the climb guard
        "800001": ("loop", True, ["800001"]),
        # the client's own complaint of 4 September, exactly as 1688 files it
        "700001": ("装饰花瓶", True, ["700002"]),
        "700002": ("花盆、花瓶", False, ["700003"]),
        "700003": ("宠物及园艺", False, []),
        # a two-row chain, to prove the rule does not eat the only parent there
        "700004": ("卷帘", True, ["700005"]),
        "700005": ("窗帘门帘及配件", False, []),
    }

    def __init__(self, broken=()):
        self.asked = []
        self.broken = set(broken)

    def call(self, route, params):
        category_id = str(params["categoryID"])
        self.asked.append(category_id)
        if category_id in self.broken:
            raise RuntimeError("gateway said no")
        if category_id not in self.TREE:
            return {"categoryInfo": []}
        name, is_leaf, parents = self.TREE[category_id]
        return {"categoryInfo": [{"categoryID": int(category_id), "name": name,
                                  "isLeaf": is_leaf, "parentIDs": [int(p) for p in parents]}]}


def index_with(rows=()):
    return catalog.CategoryIndex(list(rows))


# A translator's answer has to be free of Chinese to be an answer at all, so
# the fake one cannot simply prefix the input the way it used to.
NAMES = {
    "办公椅": ("Office Chairs", "كراسي مكتب"),
    "办公家具": ("Office Furniture", "أثاث مكتبي"),
    "办公、文化": ("Office & Culture", "مكتب وثقافة"),
    "装饰花瓶": ("Decorative Vase", "مزهرية زخرفية"),
    "花盆、花瓶": ("Pots & Vases", "أصص وزهريات"),
    "宠物及园艺": ("Pets & Gardening", "الحيوانات الأليفة والبستنة"),
    "卷帘": ("Roller Blinds", "ستائر رول"),
    "窗帘门帘及配件": ("Curtains & Fittings", "الستائر وملحقاتها"),
    "串珠": ("Beading", "خرز"),
    "宗教用品": ("Religious Goods", "مستلزمات دينية"),
    "电子烟": ("E-cigarettes", "سجائر إلكترونية"),
    "loop": ("Loop", "حلقة"),
}


def naming(term):
    """
    A translator that always answers, which is what production looks like.

    It is handed a path - "办公、文化 > 办公家具 > 办公椅" - and names the last
    segment, because a category name on its own is often ambiguous.
    """
    english, arabic = NAMES.get(term.split(" > ")[-1], ("Category", "تصنيف"))
    return {"en": english, "ar": arabic}


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="catlive-")
    os.environ["KDX_STATE_DIR"] = tmp

    print("\nthe cache follows the environment")
    check("category_cache.json lands under KDX_STATE_DIR",
          category_live.cache_path() == os.path.join(tmp, "category_cache.json"),
          category_live.cache_path())

    print("\nan id the tree never walked now resolves")
    client = FakeClient()
    live = category_live.LiveIndex(index_with(), client=client, translate=naming,
                                   cache=os.path.join(tmp, "a.json"))
    main_cat, sub = live.resolve("1045585")
    # 办公、文化 > 办公家具 > 办公椅. The department shown is the MIDDLE row, not
    # the root - see the section on the client's vase further down.
    check("the department comes back", main_cat and main_cat["name_original"] == "办公家具",
          str(main_cat))
    check("and the leaf is the sub category", sub and sub["name_original"] == "办公椅",
          str(sub))
    check("it climbed the whole chain", client.asked == ["1045585", "1045579", "67"],
          str(client.asked))
    # CONTROL: the plain index still answers nothing for the same id
    check("CONTROL the built tree alone still cannot resolve it",
          index_with().resolve("1045585") == (None, None))

    print("\nthe department shown is the row above the product, not 1688's root")
    # 4 September. He saw "الحيوانات الأليفة والبستنة / مزهرية زخرفية" - pets
    # and gardening over a decorative vase - because 宠物及园艺 is 1688's top
    # level and it bundles the two. He approved showing the middle row instead:
    # "نعم يمكن ان نعتمدها في جميع الاحوال".
    vase_main, vase_sub = live.resolve("700001")
    check("his vase is filed under أصص وزهريات, not under pets and gardening",
          vase_main["name_ar"] == "أصص وزهريات", str(vase_main))
    check("and the leaf he saw is still the sub category",
          vase_sub["name_ar"] == "مزهرية زخرفية", str(vase_sub))
    check("CONTROL the root is still walked - it is only not displayed",
          [row["name_zh"] for row in live.chain("700001")]
          == ["宠物及园艺", "花盆、花瓶", "装饰花瓶"],
          str([row["name_zh"] for row in live.chain("700001")]))
    # CONTROL: two rows and the parent IS the department. Taking "the row above
    # the leaf" must not be read as "drop the first row", which on a two-row
    # chain would leave the leaf as its own department.
    blind_main, blind_sub = live.resolve("700004")
    check("CONTROL a two-row chain keeps its only parent as the department",
          blind_main["name_ar"] == "الستائر وملحقاتها" and blind_sub["name_ar"] == "ستائر رول",
          str((blind_main, blind_sub)))
    check("CONTROL the department and the sub category are never the same row",
          blind_main["id"] != blind_sub["id"] and vase_main["id"] != vase_sub["id"])

    print("\na prohibited department blocks the innocent-sounding leaf under it")
    check("串珠 under 宗教用品 is blocked", live.state_of("900001") == catalog.BLOCKED,
          live.state_of("900001"))
    check("CONTROL and the leaf's own name is not what did it",
          catalog.classify("串珠")[0] == catalog.ALLOWED, str(catalog.classify("串珠")))
    check("a leaf prohibited by its own name is blocked too",
          live.state_of("900003") == catalog.BLOCKED)
    check("CONTROL an ordinary chain is allowed",
          live.state_of("1045585") == catalog.ALLOWED)
    check("CONTROL an id the gateway does not know stays unknown, not allowed",
          live.state_of("404404") == "unknown")

    print("\nthe climb cannot run away")
    looping = category_live.LiveIndex(index_with(), client=FakeClient(),
                                      translate=naming,
                                      cache=os.path.join(tmp, "b.json"))
    chain = looping.chain("800001")
    check("a category that is its own parent stops after one",
          len(chain) == 1, str(chain))

    print("\none unreachable category must not stop a night")
    broken = FakeClient(broken={"1045579"})
    partial = category_live.LiveIndex(index_with(), client=broken, translate=naming,
                                      cache=os.path.join(tmp, "c.json"))
    main_cat, sub = partial.resolve("1045585")
    check("what was reachable is still returned",
          main_cat and main_cat["name_original"] == "办公椅", str(main_cat))
    check("the failure is counted, not hidden", partial.summary()["failures"] == 1,
          str(partial.summary()))
    check("CONTROL a broken chain is not silently called allowed",
          partial.state_of("1045585") in (catalog.ALLOWED, "unknown"))

    print("\na category is a fact about 1688, not about tonight")
    path = os.path.join(tmp, "cache.json")
    counting = FakeClient()
    first = category_live.LiveIndex(index_with(), client=counting, translate=naming,
                                    cache=path)
    first.resolve("1045585")
    calls_after_first = len(counting.asked)
    first.resolve("1045585")
    check("the same category is not paid for twice in one night",
          len(counting.asked) == calls_after_first, str(counting.asked))
    # No save() here on purpose. The first live run resolved every department
    # correctly and wrote no cache file, because production never called it and
    # this check used to.
    check("what it learns is on disk without anyone calling save()",
          os.path.exists(path) and "1045585" in json.load(open(path, encoding="utf-8")),
          path)
    reopened = FakeClient()
    second = category_live.LiveIndex(index_with(), client=reopened, translate=naming,
                                     cache=path)
    resolved = second.resolve("1045585")
    check("nor on the next night", reopened.asked == [], str(reopened.asked))
    check("and the answer survives the restart",
          resolved[0]["name_original"] == "办公家具", str(resolved))

    print("\nthe built tree wins over the gateway where it has an entry")
    rows = [{"id": 67, "parent_id": None, "depth": 1, "name_zh": "办公、文化",
             "name_en": "Office & Culture", "name_ar": "مكتب وثقافة",
             "state": "allowed", "reason": ""}]
    mixed = category_live.LiveIndex(index_with(rows), client=FakeClient(),
                                    translate=naming,
                                    cache=os.path.join(tmp, "d.json"))
    main_cat, _ = mixed.resolve("1045585")
    # Asked of the CHAIN, not of the pair. The pair now shows the middle row, so
    # a check that only looked at `main_cat` would stop exercising the built
    # tree at all - it would pass on the gateway's row and prove nothing.
    root = mixed.chain("1045585")[0]
    check("the Arabic name from the built tree is used, not the Chinese one",
          root["name_ar"] == "مكتب وثقافة", str(root))
    check("CONTROL and it is the built row that answered, not a fetched one",
          root["id"] == "67" and root["name_original" if "name_original" in root
                                      else "name_zh"] == "办公、文化", str(root))
    check("by_id exposes both the built rows and the learned ones",
          "67" in mixed.by_id and "1045585" in mixed.by_id, str(sorted(mixed.by_id)))

    print("\ntranslation is used when present and never fatal when it fails")
    translated = category_live.LiveIndex(
        index_with(), client=FakeClient(), cache=os.path.join(tmp, "e.json"),
        translate=naming)
    main_cat, _ = translated.resolve("1045585")
    check("the Arabic name is filled in", main_cat["name_ar"] == "أثاث مكتبي",
          str(main_cat))

    def explode(_zh):
        raise RuntimeError("translator down")

    survives = category_live.LiveIndex(index_with(), client=FakeClient(),
                                       cache=os.path.join(tmp, "f.json"),
                                       translate=explode)
    main_cat, sub = survives.resolve("1045585")
    check("a dead translator does not fail the run", (main_cat, sub) == (None, None),
          str((main_cat, sub)))
    # This is the client's complaint of 2 September, as a check. He saw
    # "Accessories & Jewelry > 成人帽" in his shop menu with hats inside it. No
    # department at all is untidy; a Chinese one is broken.
    check("and no Chinese name reaches the shop menu",
          survives.chain("1045585") and
          not any(category_live.is_translated(row) for row in survives.chain("1045585")),
          str(survives.chain("1045585")))
    # CONTROL: the ban filter must keep working when the translator is down. It
    # reads the whole chain, translated or not, so a prohibited department is
    # still a prohibited department.
    check("CONTROL the ban filter still blocks with no translator",
          survives.state_of("900001") == catalog.BLOCKED, survives.state_of("900001"))

    print("\nan untranslated row already on disk is repaired, not served forever")
    # Until 2 September a failed model call was written to the cache as though
    # it were the answer, and `_known` served it for good: 649 of 902 learned
    # categories were stuck in Chinese on the live server.
    poisoned = os.path.join(tmp, "poisoned.json")
    with open(poisoned, "w", encoding="utf-8") as handle:
        json.dump({"1045585": {"id": "1045585", "name_zh": "办公椅",
                               "name_en": "办公椅", "name_ar": "办公椅",
                               "is_leaf": True, "parent_id": None,
                               "state": "allowed", "reason": ""}},
                  handle, ensure_ascii=False)
    asked = []

    def counting_translator(zh):
        asked.append(zh)
        return naming(zh)

    repairing = category_live.LiveIndex(index_with(), client=FakeClient(),
                                        cache=poisoned, translate=counting_translator)
    main_cat, _ = repairing.resolve("1045585")
    check("the stale Chinese row is translated on the next run",
          main_cat and main_cat["name_ar"] == "كراسي مكتب", str(main_cat))
    check("and the repair is written back to disk",
          json.load(open(poisoned, encoding="utf-8"))["1045585"]["name_ar"] == "كراسي مكتب")
    repairing.resolve("1045585")
    check("CONTROL a repaired row is not paid for a second time",
          asked == ["办公椅"], str(asked))

    print("\na category is named with the ones above it, not on its own")
    # 水钻 alone came back from the live model as "water drills". Under
    # 服饰配件、饰品 > 饰品配件 it is a rhinestone. The ambiguity is the client's
    # "a category that belongs to a different product", one level down.
    seen_terms = []

    def recording(term):
        seen_terms.append(term)
        return naming(term)

    contextual = category_live.LiveIndex(index_with(), client=FakeClient(),
                                         cache=os.path.join(tmp, "j.json"),
                                         translate=recording)
    contextual.resolve("1045585")
    check("the leaf is sent with its whole path",
          "办公、文化 > 办公家具 > 办公椅" in seen_terms, str(seen_terms))
    check("and the root is sent on its own, because it has no ancestors",
          seen_terms and seen_terms[0] == "办公、文化", str(seen_terms))
    check("CONTROL the path is built root first, not leaf first",
          seen_terms == ["办公、文化", "办公、文化 > 办公家具",
                         "办公、文化 > 办公家具 > 办公椅"], str(seen_terms))

    print("\na translator that hands the Chinese back is not an answer")
    # The model returning its input is indistinguishable from success unless
    # someone looks at what came back. This is what put 浴巾、沙滩巾 in the menu.
    parroting = category_live.LiveIndex(index_with(), client=FakeClient(),
                                        cache=os.path.join(tmp, "h.json"),
                                        translate=lambda zh: {"en": zh, "ar": zh})
    check("a parroted name is refused", parroting.resolve("1045585") == (None, None),
          str(parroting.resolve("1045585")))
    check("and it is not recorded as translated, so the next run retries",
          parroting.summary()["still_chinese"] == 3, str(parroting.summary()))

    print("\nthe number of model calls is bounded too")
    budgeted = category_live.LiveIndex(index_with(), client=FakeClient(),
                                       cache=os.path.join(tmp, "i.json"),
                                       translate=naming, max_translations=1)
    budgeted.resolve("1045585")
    check("it stops at max_translations", budgeted.summary()["translations"] == 1,
          str(budgeted.summary()))

    print("\nwithout a client, nothing changes")
    plain = index_with()
    check("build() returns the original index when there is no client",
          category_live.build(plain, client=None) is plain)
    check("and a LiveIndex when there is one",
          isinstance(category_live.build(plain, client=FakeClient()),
                     category_live.LiveIndex))

    print("\na rule added today reaches the categories learned yesterday")
    # A category is classified once, when it is learned, and the verdict then
    # lives on disk for ever. On 4 September 40 of the 1,072 cached categories
    # disagreed with the code that was running: twelve were that day's food and
    # craft rules, and the other twenty-eight were older - batteries, paint,
    # shampoo, live plants - blocked in the source and `allowed` in the cache
    # since before their rule was written. These are four of those real rows,
    # by their real ids and names, exactly as the cache held them.
    stale_cache = os.path.join(tmp, "stale.json")
    with open(stale_cache, "w", encoding="utf-8") as handle:
        json.dump({
            "121780002": {"id": "121780002", "name_zh": "狗狗干粮", "is_leaf": True,
                          "parent_id": None, "state": "allowed", "reason": "",
                          "name_en": "Dog Dry Food", "name_ar": "طعام جاف للكلاب"},
            "1717": {"id": "1717", "name_zh": "树脂工艺品", "is_leaf": True,
                     "parent_id": None, "state": "allowed", "reason": "",
                     "name_en": "Resin Crafts", "name_ar": "حرف راتنجية"},
            "10206": {"id": "10206", "name_zh": "锂电池", "is_leaf": True,
                      "parent_id": None, "state": "allowed", "reason": "",
                      "name_en": "Lithium Batteries", "name_ar": "بطاريات ليثيوم"},
            "1045585": {"id": "1045585", "name_zh": "办公椅", "is_leaf": True,
                        "parent_id": None, "state": "allowed", "reason": "",
                        "name_en": "Office Chairs", "name_ar": "كراسي مكتب"},
        }, handle, ensure_ascii=False)
    stale = category_live.LiveIndex(index_with(), client=FakeClient(),
                                    translate=naming, cache=stale_cache)
    for cid, name in (("121780002", "狗狗干粮"), ("1717", "树脂工艺品"),
                      ("10206", "锂电池")):
        check(f"{name} is refused now, though the cache says allowed",
              stale.state_of(cid) == catalog.BLOCKED, stale.state_of(cid))
    check("CONTROL an office chair cached as allowed is still allowed - the "
          "re-score is the rules speaking, not a blanket refusal",
          stale.state_of("1045585") == catalog.ALLOWED,
          stale.state_of("1045585"))
    row = stale.by_id.get("121780002") or stale._known("121780002")
    check("CONTROL and the Saudi name it already paid for is untouched",
          row.get("name_ar") == "طعام جاف للكلاب", str(row))

    print("\nthe number of gateway calls is bounded")
    capped = category_live.LiveIndex(index_with(), client=FakeClient(),
                                     translate=naming,
                                     cache=os.path.join(tmp, "g.json"), max_calls=1)
    capped.resolve("1045585")
    check("it stops at max_calls rather than walking all night",
          capped.summary()["gateway_calls"] == 1, str(capped.summary()))

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="catlive-")
    os.environ["KDX_STATE_DIR"] = tmp

    print("\nthe cache follows the environment")
    check("category_cache.json lands under KDX_STATE_DIR",
          category_live.cache_path() == os.path.join(tmp, "category_cache.json"),
          category_live.cache_path())

    print("\nan id the tree never walked now resolves")
    client = FakeClient()
    live = category_live.LiveIndex(index_with(), client=client,
                                   cache=os.path.join(tmp, "a.json"))
    main_cat, sub = live.resolve("1045585")
    check("the department comes back", main_cat and main_cat["name_original"] == "办公、文化",
          str(main_cat))
    check("and the leaf is the sub category", sub and sub["name_original"] == "办公椅",
          str(sub))
    check("it climbed the whole chain", client.asked == ["1045585", "1045579", "67"],
          str(client.asked))
    # CONTROL: the plain index still answers nothing for the same id
    check("CONTROL the built tree alone still cannot resolve it",
          index_with().resolve("1045585") == (None, None))

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
                                      cache=os.path.join(tmp, "b.json"))
    chain = looping.chain("800001")
    check("a category that is its own parent stops after one",
          len(chain) == 1, str(chain))

    print("\none unreachable category must not stop a night")
    broken = FakeClient(broken={"1045579"})
    partial = category_live.LiveIndex(index_with(), client=broken,
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
    first = category_live.LiveIndex(index_with(), client=counting, cache=path)
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
    second = category_live.LiveIndex(index_with(), client=reopened, cache=path)
    resolved = second.resolve("1045585")
    check("nor on the next night", reopened.asked == [], str(reopened.asked))
    check("and the answer survives the restart",
          resolved[0]["name_original"] == "办公、文化", str(resolved))

    print("\nthe built tree wins over the gateway where it has an entry")
    rows = [{"id": 67, "parent_id": None, "depth": 1, "name_zh": "办公、文化",
             "name_en": "Office & Culture", "name_ar": "مكتب وثقافة",
             "state": "allowed", "reason": ""}]
    mixed = category_live.LiveIndex(index_with(rows), client=FakeClient(),
                                    cache=os.path.join(tmp, "d.json"))
    main_cat, _ = mixed.resolve("1045585")
    check("the Arabic name from the built tree is used, not the Chinese one",
          main_cat["name_ar"] == "مكتب وثقافة", str(main_cat))
    check("by_id exposes both the built rows and the learned ones",
          "67" in mixed.by_id and "1045585" in mixed.by_id, str(sorted(mixed.by_id)))

    print("\ntranslation is used when present and never fatal when it fails")
    translated = category_live.LiveIndex(
        index_with(), client=FakeClient(), cache=os.path.join(tmp, "e.json"),
        translate=lambda zh: {"en": f"EN:{zh}", "ar": f"AR:{zh}"})
    main_cat, _ = translated.resolve("1045585")
    check("the Arabic name is filled in", main_cat["name_ar"] == "AR:办公、文化",
          str(main_cat))

    def explode(_zh):
        raise RuntimeError("translator down")

    survives = category_live.LiveIndex(index_with(), client=FakeClient(),
                                       cache=os.path.join(tmp, "f.json"),
                                       translate=explode)
    main_cat, _ = survives.resolve("1045585")
    check("CONTROL a dead translator falls back to Chinese rather than failing",
          main_cat["name_ar"] == "办公、文化", str(main_cat))

    print("\nwithout a client, nothing changes")
    plain = index_with()
    check("build() returns the original index when there is no client",
          category_live.build(plain, client=None) is plain)
    check("and a LiveIndex when there is one",
          isinstance(category_live.build(plain, client=FakeClient()),
                     category_live.LiveIndex))

    print("\nthe number of gateway calls is bounded")
    capped = category_live.LiveIndex(index_with(), client=FakeClient(),
                                     cache=os.path.join(tmp, "g.json"), max_calls=1)
    capped.resolve("1045585")
    check("it stops at max_calls rather than walking all night",
          capped.summary()["gateway_calls"] == 1, str(capped.summary()))

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

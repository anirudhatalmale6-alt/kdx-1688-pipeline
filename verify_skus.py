"""
Proof for the size and colour table.

    python3 verify_skus.py

Runs against recorded live responses in samples/skuinfo/, captured from the
client's own catalogue on 2026-09-01, plus the cases a live response will not
show you: an offer with no table, an offer whose price is missing, and a
refusal in the middle of a night's run.

The check that matters most is conservation. Grouping 18 SKUs into 2 colours of
9 sizes is easy to get subtly wrong - a lost size shows up as nothing at all on
the shop, just a dropdown one line shorter - so the counts are asserted against
the raw response, not against what the grouper thought it did.

No network.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import skus  # noqa: E402
from aop_client import AopError  # noqa: E402
from mapping import variant_block  # noqa: E402

SAMPLES = os.path.join(HERE, "samples", "skuinfo")
passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def _raises(thunk) -> bool:
    try:
        thunk()
    except skus.SkuError:
        return True
    return False


def fixture(offer_id: str) -> dict:
    with open(os.path.join(SAMPLES, f"{offer_id}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def raw_skus(payload: dict) -> list:
    return payload["result"]["result"]["skuSimpleInfos"]


def product(offer_id: str, price="4.00") -> dict:
    """A product in the shape LinkPlus produces: one variant, no sizes."""
    return {
        "offer_id": offer_id,
        "title_zh": "测试",
        "images": ["https://cbu01.alicdn.com/photo.jpg"],
        "weight_kg": 1.0,
        "variants": [{"original": "", "image": "https://cbu01.alicdn.com/photo.jpg",
                      "images": ["https://cbu01.alicdn.com/photo.jpg"],
                      "sizes": [], "price": Decimal(price)}],
        "source_channel": "linkplus",
    }


class Stub:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error
        self.calls = []

    def call(self, route, params=None, authed=True):
        self.calls.append(params)
        if self.error:
            raise self.error
        return self.payload


# ---------------------------------------------------------------- real data
print("a colour x size offer, from the live response")
payload = fixture("1014470220597")
rows = skus.rows_from(payload)
check("every SKU in the response is parsed",
      len(rows) == len(raw_skus(payload)),
      f"parsed {len(rows)} of {len(raw_skus(payload))}")
check("the variant axis is the colour, not the size",
      skus.variant_axis(rows) == "颜色", f"chose {skus.variant_axis(rows)!r}")

built = skus.apply_to(product("1014470220597"), rows)
colours = {row["axes"]["颜色"] for row in rows}
sizes_per = {}
for row in rows:
    sizes_per.setdefault(row["axes"]["颜色"], set()).add(row["axes"]["尺码"])

check("one variant per colour in the response",
      len(built["variants"]) == len(colours),
      f"{len(built['variants'])} variants for {len(colours)} colours")
check("no colour is invented",
      {v["original"] for v in built["variants"]} == colours)
# Conservation: the sizes that came out must be exactly the sizes that went in,
# colour by colour. A count alone would pass while two colours swapped lists.
check("every size lands under its own colour, none lost, none duplicated",
      all({s["original"] for s in v["sizes"]} == sizes_per[v["original"]]
          for v in built["variants"]),
      json.dumps({v["original"]: sorted(s["original"] for s in v["sizes"])
                  for v in built["variants"]}, ensure_ascii=False))
check("the total number of published options equals the number of SKUs",
      sum(len(v["sizes"]) for v in built["variants"]) == len(rows),
      f"{sum(len(v['sizes']) for v in built['variants'])} vs {len(rows)}")

print("\nwhat the API does not carry must not appear anyway")
every_size = [s for v in built["variants"] for s in v["sizes"]]
check("no size claims a stock figure",
      all("stock" not in size for size in every_size))
check("every size carries the offer price",
      all(size["price"] == Decimal("4.00") for size in every_size))
check("and every size says so, rather than passing as a quoted price",
      all(size["price_from_offer"] is True for size in every_size))
check("every size carries the specId fastCreateOrder needs",
      all(size.get("spec_id") for size in every_size))
check("the specIds are the ones 1688 sent",
      {s["spec_id"] for s in every_size} == {r["spec_id"] for r in rows})
check("no variant invents a second photograph",
      all(len(v["images"]) <= 1 for v in built["variants"]),
      "this channel has one photo per offer")

print("\nan 80-SKU offer whose second axis is 规格, not 尺码")
rows80 = skus.rows_from(fixture("1008796506947"))
built80 = skus.apply_to(product("1008796506947"), rows80)
check("all 80 parsed", len(rows80) == 80, f"got {len(rows80)}")
check("grouped by colour", skus.variant_axis(rows80) == "颜色")
check("every one of the 80 options survives grouping",
      sum(len(v["sizes"]) for v in built80["variants"]) == 80,
      f"{sum(len(v['sizes']) for v in built80['variants'])}")

print("\na single-SKU offer still gets its option published")
rows1 = skus.rows_from(fixture("1000354769581"))
built1 = skus.apply_to(product("1000354769581"), rows1)
check("one variant", len(built1["variants"]) == 1)
check("the colour becomes the variant name",
      built1["variants"][0]["original"] == "透明",
      built1["variants"][0]["original"])
check("the 规格 becomes its one size",
      [s["original"] for s in built1["variants"][0]["sizes"]] == ["一次性脚膜套（100只）"],
      str([s["original"] for s in built1["variants"][0]["sizes"]]))

print("\nan offer with no colour axis keeps one variant rather than N photos")
one_axis = [{"sku_id": "1", "spec_id": "a", "axes": {"规格": "小"}},
            {"sku_id": "2", "spec_id": "b", "axes": {"规格": "大"}}]
flat = skus.apply_to(product("x"), one_axis)
check("still one variant", len(flat["variants"]) == 1,
      f"{len(flat['variants'])} variants would publish the same photo twice")
check("both options are sizes under it",
      [s["original"] for s in flat["variants"][0]["sizes"]] == ["小", "大"])

print("\nthe cases a live response will not show you")
check("an offer with no SKU table is returned untouched, price intact",
      skus.apply_to(product("y"), []) == product("y"))
check("a response with success:false is raised, not read as an empty table",
      _raises(lambda: skus.rows_from({"result": {"success": False,
                                                 "message": "不是精选货源商品"}})))
check("a response with no result at all is an empty table, not a crash",
      skus.rows_from({"result": {"success": True}}) == [])
try:
    skus.apply_to({"offer_id": "z", "variants": [{"original": "", "sizes": []}]},
                  one_axis)
    check("sizes are never published without a price", False, "no exception")
except skus.SkuError:
    check("sizes are never published without a price", True)

print("\na night must not stop on one offer")
gone = skus.enrich(Stub(error=AopError("HTTP 500")), product("1014470220597"))
check("a refusal leaves the product publishable",
      gone["variants"][0]["price"] == Decimal("4.00"))
check("and is recorded, not swallowed silently",
      gone["sku_source"] == "failed" and "500" in gone["sku_error"],
      json.dumps({k: str(v) for k, v in gone.items() if k.startswith("sku")}))
empty = skus.enrich(Stub(payload={"result": {"success": True}}), product("q"))
check("'no table' is told apart from 'never asked'",
      empty["sku_source"] == "empty", empty.get("sku_source"))
live = skus.enrich(Stub(payload=fixture("1014470220597")), product("1014470220597"))
check("a good fetch reports where the sizes came from",
      live["sku_source"] == "product.skuinfo.get" and live["sku_count"] == 18)

print("\nadding sizes must not empty the catalogue")
# The trap this catches: product.skuinfo.get reports no stock, rules.py rejects
# stock <= 0 as out_of_stock, and to_rules_product used to default a size's
# stock to 0 while defaulting a variant's to 1. Enriching every product would
# therefore have turned a published catalogue into 151 out-of-stock rejections,
# with every check above still green.
from pipeline import to_rules_product  # noqa: E402
before = to_rules_product(product("1014470220597"))
after = to_rules_product(built)
check("the product published today is in stock",
      all(v.stock > 0 for v in before.variants))
check("and every size added to it is too",
      all(v.stock > 0 for v in after.variants),
      f"{sum(1 for v in after.variants if v.stock <= 0)} of {len(after.variants)} would reject")
check("adding sizes turns 1 purchasable option into 18",
      (len(before.variants), len(after.variants)) == (1, 18),
      f"{len(before.variants)} -> {len(after.variants)}")
check("each option carries its colour and its size",
      all(v.attributes["color"] and v.attributes["size"] for v in after.variants))
# A real zero from the detail API must still reject, or this fix would have
# published sold-out stock as available.
honest_zero = dict(built)
honest_zero["variants"] = [dict(built["variants"][0],
                                sizes=[dict(built["variants"][0]["sizes"][0], stock=0)])]
check("a stock figure 1688 actually reported as 0 still rejects",
      all(v.stock == 0 for v in to_rules_product(honest_zero).variants))

print("\nand the result has to survive the mapper it is published through")
block = variant_block(built["variants"])
check("mapping accepts the enriched variants", len(block) == len(built["variants"]))
mapped_sizes = sum(len(v["sizes"]) for v in block)
check("the mapper does not drop an option either",
      mapped_sizes == len(rows), f"{mapped_sizes} of {len(rows)}")
check("the mapper carries the sku ids through",
      all(size.get("sku_id") for v in block for size in v["sizes"]))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

"""
The LinkPlus channel, checked against the response the live gateway actually
returned on 30 August 2026 (samples/linkplus/).

Every claim here was measured first and pinned second. In particular the price
unit: oldPrice is an integer, and 10 of the 20 recorded offers carry a price
that is not a whole multiple of 100, so the field carries fractions of a yuan
and cannot be denominated in yuan. That check is in section 2 and it fails
loudly if a future fixture ever contradicts it.

    python3 verify_linkplus.py
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import pipeline  # noqa: E402
import source  # noqa: E402

FIXTURE = os.path.join(HERE, "samples", "linkplus",
                       "cross_similar_offer_search_uniqlo_skirt.json")

PASS = FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"\n         {detail}" if detail else ""))


def rows() -> list:
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)["result"]["result"]


class FakeClient:
    """Replays the recorded response and records what was asked for."""

    def __init__(self, payload=None):
        with open(FIXTURE, encoding="utf-8") as handle:
            self.payload = payload if payload is not None else json.load(handle)
        self.calls = []

    def call(self, route, params=None, authed=True):
        self.calls.append((route, dict(params or {})))
        return self.payload


def env(**values):
    """Set env for one block and put it back, whatever happens."""
    class Ctx:
        def __enter__(self):
            self.old = {k: os.environ.get(k) for k in values}
            for k, v in values.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        def __exit__(self, *exc):
            for k, v in self.old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return Ctx()


print("\n1. the recorded response is what the gateway really sent")
data = json.load(open(FIXTURE, encoding="utf-8"))
check("success is true", data.get("success") is True)
check("20 rows on one page", len(rows()) == 20, f"got {len(rows())}")
check("the gateway reported a larger total than one page",
      int(data["result"]["total"]) > 20, str(data["result"]["total"]))
check("pageSize came back as 20 - the cap, not what we asked",
      int(data["result"]["pageSize"]) == 20)
check("no credential leaked into the fixture",
      "appKey" not in json.dumps(data) and "3823896" not in json.dumps(data))

print("\n2. the price unit is fen, and it is measured not assumed")
prices = [row["oldPrice"] for row in rows()]
fractional = [p for p in prices if p % 100]
check("every price is an integer", all(isinstance(p, int) for p in prices))
check("some prices are not whole multiples of 100 - so they carry cents",
      len(fractional) > 0, f"{len(fractional)} of {len(prices)}")
product = source.normalise_search_row(rows()[0])
check("1010 fen becomes 10.10 CNY",
      product["variants"][0]["price"] == Decimal("10.10"),
      str(product["variants"][0]["price"]))
check("price is a Decimal, not a float", isinstance(product["variants"][0]["price"], Decimal))
# The control: if the divisor were wrong the number would be wrong by 100x.
with env(KDX_LINKPLUS_PRICE_DIVISOR="1"):
    import importlib
    importlib.reload(source)
    check("CONTROL divisor 1 gives 1010, proving the divisor is doing the work",
          source.normalise_search_row(rows()[0])["variants"][0]["price"] == Decimal("1010.00"))
importlib.reload(source)

print("\n3. the eleven fields survive, and the missing ones are admitted")
product = source.normalise_search_row(rows()[0])
check("offer id", product["offer_id"] == "721188620217", product["offer_id"])
check("Chinese title kept verbatim", product["title_zh"].startswith("跨境缎面"))
check("category id", product["category_id"] == "1031912", product["category_id"])
check("one image", len(product["images"]) == 1)
check("description is empty, not invented", product["description_zh"] == "")
check("no attributes are fabricated", product["attributes"] == {})
check("exactly one variant - this channel has no SKU table",
      len(product["variants"]) == 1)
check("that variant has no sizes", product["variants"][0]["sizes"] == [])
check("min order carried through", product["min_order"] == rows()[0]["quantityBegin"])

print("\n4. a row with no price is not a product")
try:
    source.normalise_search_row({"offerId": "1", "subject": "x"})
except source.SourceError as exc:
    check("a priceless row is refused", "no price" in str(exc))
else:
    check("a priceless row is refused", False, "it was accepted")
try:
    source.normalise_search_row({"oldPrice": 100})
except source.SourceError as exc:
    check("a row with no offerId is refused", "offerId" in str(exc))
else:
    check("a row with no offerId is refused", False, "it was accepted")

print("\n5. weight: always flagged as assumed, never silently measured")
with env(KDX_LINKPLUS_WEIGHT_MODE="table", KDX_CATEGORY_WEIGHTS=None,
         KDX_LINKPLUS_DEFAULT_WEIGHT_KG=None):
    importlib.reload(source)
    weight, assumed = source.weight_for_category("1031912")
    check("with no table the fallback is used", weight == 2.5, str(weight))
    check("and it is reported as ASSUMED", assumed is True)
    check("the assumption reaches the product",
          source.normalise_search_row(rows()[0])["weight_assumed"] is True)

with env(KDX_LINKPLUS_WEIGHT_MODE="table",
         KDX_CATEGORY_WEIGHTS='{"1031912": 0.4}'):
    importlib.reload(source)
    weight, assumed = source.weight_for_category("1031912")
    check("the client's table wins for a known category", weight == 0.4, str(weight))
    check("an unknown category still falls back",
          source.weight_for_category("999999")[0] == 2.5)
    check("even a table value is reported as assumed - nobody weighed it",
          assumed is True)

with env(KDX_LINKPLUS_WEIGHT_MODE="light", KDX_CATEGORY_WEIGHTS=None):
    importlib.reload(source)
    check("light mode puts everything under the 2 kg line",
          source.weight_for_category("1031912")[0] <= 2.0)

with env(KDX_CATEGORY_WEIGHTS="{not json"):
    importlib.reload(source)
    try:
        source.weight_for_category("1031912")
    except source.SourceError as exc:
        check("a malformed weight table fails loudly", "KDX_CATEGORY_WEIGHTS" in str(exc))
    else:
        check("a malformed weight table fails loudly", False, "it was ignored")
importlib.reload(source)

print("\n6. the source: a search, and honest about not being a lookup")
client = FakeClient()
src = source.LinkPlusSource(client)
found = src.search_by_image("https://example.invalid/photo.jpg")
check("20 products come back", len(found) == 20, str(len(found)))
route, params = client.calls[0]
check("it called the linkplus route",
      route.namespace == "com.alibaba.linkplus"
      and route.api_name == "alibaba.cross.similar.offer.search")
check("picUrl was sent", params.get("picUrl") == "https://example.invalid/photo.jpg")
check("page was sent as required", "page" in params)
check("pageSize asked for is the measured cap of 20", int(params["pageSize"]) == 20)
check("a seen offer can then be fetched by id",
      src.get_product("721188620217")["offer_id"] == "721188620217")
try:
    src.get_product("000000000000")
except source.SourceError as exc:
    check("an unseen offer says WHY, naming the blocked API",
          "APIACLDecline" in str(exc) or "product.get" in str(exc), str(exc))
else:
    check("an unseen offer says WHY", False, "it returned something")
try:
    src.search_by_image("")
except source.SourceError:
    check("an empty picUrl is refused before spending a call", len(client.calls) == 1)
else:
    check("an empty picUrl is refused before spending a call", False)

print("\n7. build_source routes KDX_SOURCE=linkplus")
with env(KDX_SOURCE="linkplus"):
    importlib.reload(source)
    built = source.build_source(FakeClient())
    check("linkplus is selected", isinstance(built, source.LinkPlusSource))
with env(KDX_SOURCE="linkplus"):
    importlib.reload(source)
    try:
        source.build_source(None)
    except source.SourceError as exc:
        check("CONTROL linkplus without a client is refused, not silently swapped",
              "linkplus" in str(exc))
    else:
        check("CONTROL linkplus without a client is refused", False)
with env(KDX_SOURCE="fixture"):
    importlib.reload(source)
    check("CONTROL a different value still gives a different source",
          isinstance(source.build_source(None), source.FixtureSource))
importlib.reload(source)

print("\n8. the audit tells the truth about an assumed weight")


class Result:
    def __init__(self, code, weight):
        self.audit = type("A", (), {"reason_code": code, "reason_ar": ""})()
        self.variant = type("V", (), {"weight_kg": Decimal(str(weight))})()


results = [Result("heavy_and_unmatched", "2.5")]
pipeline._restate_assumed_weight(results, {"weight_assumed": True, "category_id": "1031912"})
check("the code says the weight was assumed",
      results[0].audit.reason_code == "assumed_heavy_and_unmatched")
check("the Arabic says the weight is unavailable, not that it was weighed",
      "غير متوفر" in results[0].audit.reason_ar and "افترض" in results[0].audit.reason_ar,
      results[0].audit.reason_ar)
check("it names the category so the client knows which row to fill in",
      "1031912" in results[0].audit.reason_ar)

control = [Result("heavy_and_unmatched", "12.4")]
pipeline._restate_assumed_weight(control, {"weight_assumed": False, "category_id": "x"})
check("CONTROL a genuinely weighed product keeps the original reason",
      control[0].audit.reason_code == "heavy_and_unmatched")

both = [Result("heavy_and_unmatched", "2.5")]
pipeline._restate_uncompared(both)
pipeline._restate_assumed_weight(both, {"weight_assumed": True, "category_id": "1"})
check("when nobody searched, THAT stays the explanation",
      both[0].audit.reason_code == "not_compared", both[0].audit.reason_code)

accepted = [Result("published", "2.5")]
pipeline._restate_assumed_weight(accepted, {"weight_assumed": True, "category_id": "1"})
check("CONTROL a published product is left alone",
      accepted[0].audit.reason_code == "published")

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)

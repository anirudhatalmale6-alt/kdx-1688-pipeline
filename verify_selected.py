"""
Proof for the 精选货源 pool channel.

    python3 verify_selected.py            # offline, no credentials needed
    KDX_LIVE=1 python3 verify_selected.py # adds the live controls

The offline half runs against a recorded pool response and is about the four
mistakes that would each ship a broken catalogue while every count looked right:

  1. the photograph paths arrive RELATIVE ("img/ibank/...jpg"). Twenty-one
     products already reached his shop once with no photographs; sending these
     unprefixed would do it again, and the payload would look full.
  2. the weight is absent on most offers, and source._weight_of answers 2.5 kg
     when it cannot find one - above his 2 kg line, so every such product is
     classed heavy, and a heavy product with no price match is never published.
     Two products in three would vanish with a plausible reason attached.
  3. the batch is all-or-nothing: one offer outside the pool refuses the whole
     request, so a naive fetch loses the other forty-nine.
  4. `pageNo` is accepted and serves page one forever. Same row count, same
     HTTP 200 - only diffing ids across pages shows it.

The live half is the positive control that matters most: take a photograph URL
the way this module hands it over, fetch it, and check the bytes really are a
JPEG. "The field was populated" has never been the same claim as "the customer
sees a photograph".
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import selected  # noqa: E402
import source as source_module  # noqa: E402
from aop_client import ApiRoute, AopError  # noqa: E402

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


# A record shaped exactly like the live one measured on 1 September: relative
# photograph paths, a SKU table with per-SKU prices and per-SKU photographs, no
# declared weight.
RECORD = {
    "productID": 1056196375016,
    "subject": "5.5cm拼豆盒正方形PP塑料半透明包装盒小物料盒带盖零件首饰盒子",
    "categoryID": 202248721,
    "categoryName": "桌面收纳盒",
    "status": "published",
    "description": ('<div>看图</div><img src="https://cbu01.alicdn.com/img/ibank/desc-1.jpg">'
                    '<img src="https://cbu01.alicdn.com/img/ibank/desc-2.jpg">'),
    "image": {"images": ["img/ibank/main-1.jpg", "img/ibank/main-2.jpg",
                         "img/ibank/main-3.jpg"]},
    "shippingInfo": {},
    "saleInfo": {"priceRanges": [{"price": 0.06, "startQuantity": 1}]},
    "skuInfos": [
        {"skuId": 1, "price": 0.12, "amountOnSale": 3211380,
         "attributes": [{"attributeName": "颜色", "attributeValue": "白色",
                         "skuImageUrl": "img/ibank/sku-white.jpg"},
                        {"attributeName": "尺寸", "attributeValue": "5.5cm"}]},
        {"skuId": 2, "price": 0.19, "amountOnSale": 900,
         "attributes": [{"attributeName": "颜色", "attributeValue": "白色",
                         "skuImageUrl": "img/ibank/sku-white.jpg"},
                        {"attributeName": "尺寸", "attributeValue": "7cm"}]},
        {"skuId": 3, "price": 0.31, "amountOnSale": 400,
         "attributes": [{"attributeName": "颜色", "attributeValue": "黑色",
                         "skuImageUrl": "img/ibank/sku-black.jpg"},
                        {"attributeName": "尺寸", "attributeValue": "5.5cm"}]},
    ],
}

HEAVY = json.loads(json.dumps(RECORD))
HEAVY["productID"] = 999
HEAVY["shippingInfo"] = {"offerSuttleWeight": 3.4}


class FakeClient:
    """Records what it was asked, answers what the live gateway answered."""

    def __init__(self, pages: int = 3, page_param: str = "pageNum",
                 outside: set | None = None):
        self.pages = pages
        self.page_param = page_param
        self.outside = outside or set()
        self.asked: list = []

    def call(self, route: ApiRoute, params: dict | None = None, authed: bool = True):
        params = params or {}
        self.asked.append((route.api_name, dict(params)))
        if route.api_name == "jxhy.product.getPageList":
            page = int(params.get(self.page_param, 1))
            if page > self.pages:
                return {"result": {"success": True, "result": []}}
            size = int(params.get("pageSize", 50))
            base = (page - 1) * size
            return {"result": {"success": True, "result": [
                {"itemId": 1000 + base + i, "title": f"item {base + i}"}
                for i in range(size)]}}
        if route.api_name == "alibaba.pifatuan.product.detail.list":
            ids = json.loads(params["offerIds"])
            bad = [i for i in ids if str(i) in self.outside]
            if bad:
                return {"result": {"success": False,
                                   "message": f"offerId：{bad[0]} {selected.NOT_IN_POOL}"}}
            out = []
            for ident in ids:
                record = json.loads(json.dumps(RECORD))
                record["productID"] = ident
                out.append({"productInfo": record})
            return {"result": {"success": True, "result": out}}
        raise AopError(f"unexpected api {route.api_name}")


# ----------------------------------------------------------- 1. photographs
print("photograph paths arrive relative and must leave absolute")
pool = selected.SelectedPool(FakeClient())
product = pool.normalise(RECORD)

check("every gallery photograph is an absolute URL",
      product["images"] and all(url.startswith("https://") for url in product["images"]),
      str(product["images"]))
check("and it is the 1688 CDN host, not something invented",
      all(url.startswith("https://cbu01.alicdn.com/img/ibank/") for url in product["images"]),
      str(product["images"]))
check("all three main photographs survived", len(product["images"]) == 3,
      str(product["images"]))
check("per-variant photographs are absolute too",
      all(url.startswith("https://") for variant in product["variants"]
          for url in variant["images"]),
      str([v["images"] for v in product["variants"]]))
check("an already-absolute URL is not prefixed twice",
      selected.absolute_image("https://cbu01.alicdn.com/img/x.jpg")
      == "https://cbu01.alicdn.com/img/x.jpg")
check("a protocol-relative URL gets a scheme",
      selected.absolute_image("//cbu01.alicdn.com/img/x.jpg")
      == "https://cbu01.alicdn.com/img/x.jpg")
check("an empty path stays empty rather than becoming the bare host",
      selected.absolute_image("") == "" and selected.absolute_image(None) == "")

print("\nthe description's own photographs are a decision, not a default")
check("they are left out unless asked for",
      not any("desc-" in url for url in product["images"]),
      str(product["images"]))
with_desc = selected.SelectedPool(FakeClient(),
                                  include_description_images=True).normalise(RECORD)
check("and they are all there when asked for",
      sum(1 for url in with_desc["images"] if "desc-" in url) == 2,
      str(with_desc["images"]))
check("without displacing the main photographs, which stay first",
      with_desc["images"][:3] == product["images"])
check("description_images deduplicates and keeps order",
      selected.description_images(
          '<img src="https://a/1.jpg"><img src="https://a/2.jpg">'
          '<img src="https://a/1.jpg">') == ["https://a/1.jpg", "https://a/2.jpg"])

# ---------------------------------------------------------------- 2. weight
print("\nthe weight, which is where this channel would have emptied itself")
check("an offer with no declared weight is NOT called heavy",
      product["weight_kg"] <= 2.0,
      f"{product['weight_kg']} kg would be heavy, and heavy + unmatched is never published")
check("and it is marked as assumed, not passed off as measured",
      product["weight_assumed"] is True)
heavy = pool.normalise(HEAVY)
check("a declared weight is used exactly as declared", heavy["weight_kg"] == 3.4,
      str(heavy["weight_kg"]))
check("and it is NOT marked assumed", heavy["weight_assumed"] is False)
check("source._weight_of reads the pool's own spelling of the field",
      source_module._weight_of({"shippingInfo": {"offerSuttleWeight": 0.5}}) == 0.5,
      "offerSuttleWeight is the only weight the pool detail API returns")

# ----------------------------------------------------------------- 3. batch
print("\none offer outside the pool must not cost the other forty-nine")
client = FakeClient(outside={"1007"})
pool = selected.SelectedPool(client, batch=50)
ids = [str(1000 + i) for i in range(20)]
got = list(pool.products(offer_ids=ids))
check("nineteen of the twenty still arrive", len(got) == 19, f"got {len(got)}")
check("the missing one is recorded, not silently gone",
      pool.skipped_outside_pool == ["1007"], str(pool.skipped_outside_pool))
check("and it took splitting, not one call per product",
      len([a for a in client.asked if a[0] == "alibaba.pifatuan.product.detail.list"]) < 20,
      "splitting in halves should cost far fewer calls than one each")

clean = FakeClient()
pool = selected.SelectedPool(clean, batch=50)
got = list(pool.products(offer_ids=[str(1000 + i) for i in range(50)]))
check("with nothing refused, fifty products cost ONE detail call",
      len(got) == 50
      and len([a for a in clean.asked
               if a[0] == "alibaba.pifatuan.product.detail.list"]) == 1,
      str([a[0] for a in clean.asked]))

# ------------------------------------------------------------------ 4. paging
print("\nthe page parameter, where a silent failure looks like success")
walker = selected.SelectedPool(FakeClient(pages=3), page_size=50)
ids = walker.offer_ids()
check("three pages of fifty give 150 distinct offers", len(ids) == 150, str(len(ids)))
check("the ids really are distinct", len(set(ids)) == len(ids))
check("it sends pageNum, the name measured to move the window",
      all("pageNum" in params for name, params in walker.client.asked
          if name == "jxhy.product.getPageList"),
      "pageNo returns HTTP 200 and page one forever")

stuck = selected.SelectedPool(FakeClient(pages=3, page_param="pageNo"), page_size=50)
stuck_ids = stuck.offer_ids()
check("a server that ignores our page name stops the walk instead of looping",
      len(stuck_ids) == 50 and stuck.pages_walked <= 2,
      f"{len(stuck_ids)} ids over {stuck.pages_walked} pages - it should notice page 2 was page 1")

limited = selected.SelectedPool(FakeClient(pages=9), page_size=50)
check("a limit is honoured", len(limited.offer_ids(limit=30)) == 30)


# --------------------------------------------------------------- 4b. keywords
print("\nthe keyword, which is what makes the pool a catalogue and not a shelf")


class KeywordClient(FakeClient):
    """
    A listing that really does read the keyword.

    `catalogue` maps a word to the offers it holds. A word that is not in it
    returns nothing at all - which is what the live gateway did for the
    nonsense control, and the only reason we know the parameter is read.
    """

    def __init__(self, catalogue: dict, page_size: int = 50):
        super().__init__()
        self.catalogue = catalogue
        self.words_asked: list = []

    def call(self, route: ApiRoute, params: dict | None = None, authed: bool = True):
        params = params or {}
        if route.api_name == "jxhy.product.getPageList":
            word = params.get("keyword", "")
            self.words_asked.append(word)
            page = int(params.get("pageNum", 1))
            size = int(params.get("pageSize", 50))
            offers = self.catalogue.get(word, [])
            window = offers[(page - 1) * size:page * size]
            return {"result": {"success": True,
                               "result": [{"itemId": o} for o in window]}}
        return super().call(route, params, authed)


CATALOGUE = {"连衣裙": [f"A{n}" for n in range(70)],
             "台灯": [f"B{n}" for n in range(30)],
             "耳机": [f"A{n}" for n in range(10)]}          # overlaps 连衣裙 on purpose

searcher = selected.SelectedPool(KeywordClient(CATALOGUE), page_size=50)
dresses = searcher.offer_ids(keyword="连衣裙")
check("a keyword walks all of its pages, not just the first",
      len(dresses) == 70, str(len(dresses)))
check("the word is actually sent",
      set(searcher.client.words_asked) == {"连衣裙"},
      str(searcher.client.words_asked))

empty = selected.SelectedPool(KeywordClient(CATALOGUE))
check("CONTROL: a word with no offers returns nothing rather than the usual page",
      empty.offer_ids(keyword="qqzzxxyy") == [],
      "if this ever returns rows, the listing is ignoring the keyword again")

many = selected.SelectedPool(KeywordClient(CATALOGUE))
across = many.offer_ids_for(["连衣裙", "台灯", "耳机"])
check("several words are merged without duplicates",
      len(across) == len(set(across)) == 100, str(len(across)))
check("and each word is credited only with what IT added",
      many.keyword_counts == {"连衣裙": 70, "台灯": 30, "耳机": 0},
      str(many.keyword_counts))

# The ledger filter belongs inside the walk. A night whose first word returns
# 2,000 offers the shop already carries must not report "found 2,000" and then
# publish nothing.
carried = {f"A{n}" for n in range(70)}
fresh = selected.SelectedPool(KeywordClient(CATALOGUE))
new_ids = fresh.offer_ids_for(["连衣裙", "台灯"], known=lambda offer: offer in carried)
check("offers the shop already has are dropped as the walk goes",
      set(new_ids) == {f"B{n}" for n in range(30)}, str(len(new_ids)))
check("so a word that is entirely already-published is reported as contributing none",
      fresh.keyword_counts["连衣裙"] == 0, str(fresh.keyword_counts))

capped = selected.SelectedPool(KeywordClient(CATALOGUE))
check("a limit stops the walk early instead of after every word",
      len(capped.offer_ids_for(["连衣裙", "台灯", "耳机"], limit=25)) == 25)

# ------------------------------------------------------- 5. the product itself
print("\nthe product this channel produces is the one the pipeline consumes")
check("two colours became two variants", len(product["variants"]) == 2,
      str([v["original"] for v in product["variants"]]))
check("white carries both of its sizes",
      len([v for v in product["variants"] if v["original"] == "白色"][0]["sizes"]) == 2)
check("each size keeps its own price - what one photograph one price needs",
      sorted(str(size["price"]) for variant in product["variants"]
             for size in variant["sizes"]) == ["0.12", "0.19", "0.31"],
      str([[str(s["price"]) for s in v["sizes"]] for v in product["variants"]]))
check("the two colours have DIFFERENT photographs",
      product["variants"][0]["image"] != product["variants"][1]["image"],
      "a per-colour photograph is the whole point of this channel")
check("the description came through", "看图" in product["description_zh"])
check("the channel is named on the product",
      product["source_channel"] == "selected_pool")
check("the offer id survives as a string",
      product["offer_id"] == "1056196375016", product["offer_id"])

print("\nand the rules engine accepts it")
import pipeline as pipeline_module  # noqa: E402
import rules  # noqa: E402
from decimal import Decimal  # noqa: E402

shaped = pipeline_module.to_rules_product(product)
check("it shapes into a rules.Product with variants",
      len(shaped.variants) >= 3, str(len(shaped.variants)))
engine = rules.Engine(cny_to_sar=Decimal("0.558"))
results = engine.evaluate(shaped, {})
check("a plastic storage box is not rejected out of hand",
      any(r.decision == rules.Decision.PUBLISH for r in results),
      str({r.audit.reason_code for r in results}))
check("nothing was priced from a 2.5 kg default",
      all(r.audit.reason_code != "heavy_and_unmatched" for r in results),
      str({r.audit.reason_code for r in results}))

# ------------------------------------------------------------- live controls
if os.environ.get("KDX_LIVE"):
    print("\nlive: the photographs this module hands over must really be photographs")
    import urllib.request
    from aop_client import build_pool_from_env  # noqa: E402

    live = selected.SelectedPool(build_pool_from_env())
    ids = live.offer_ids(limit=3)
    check("the pool listing answers", len(ids) == 3, str(ids))
    products = list(live.products(offer_ids=ids))
    check("and the detail API answers for them", len(products) == 3, str(len(products)))
    urls = [url for item in products for url in item["images"]][:6]
    check("those products carry more than one photograph each",
          all(len(item["images"]) > 1 for item in products),
          str([len(i["images"]) for i in products]))
    # Fetched exactly the way photos.PhotoChecker fetches, headers included.
    #
    # A first version of this check used a bare urllib request and three of ten
    # photographs answered HTTP 420, on every retry and under every host and
    # size variant - which reads exactly like a dead image. With the headers
    # production sends, those same URLs answer 200 with JPEG bytes, and a
    # 25-product spread came back 124 photographs of 124 reachable.
    #
    # I could not pin the 420 on the User-Agent: a later headerless fetch of a
    # different photograph answered 200 too, so the cause is still open and is
    # deliberately NOT asserted here. What is asserted is the only thing that
    # was reproducible - fetching the way the importer fetches works - and the
    # real safety net is photos.PhotoChecker, which already drops a URL that
    # does not answer and holds a product left with none.
    import photos  # noqa: E402

    fetched = []
    for url in urls:
        try:
            request = urllib.request.Request(url, headers=photos.HEADERS)
            with urllib.request.urlopen(request, timeout=25) as response:
                head = response.read(4)
                fetched.append((response.status, head))
        except Exception as exc:                          # noqa: BLE001
            fetched.append((str(exc), b""))

    try:
        urllib.request.urlopen(urllib.request.Request(urls[0]), timeout=25)
        headerless = "200"
    except Exception as exc:                              # noqa: BLE001
        headerless = str(exc)[:40]
    print(f"  note  the same photograph fetched with no headers at all: {headerless}")
    check("every photograph URL returns 200",
          all(status == 200 for status, _ in fetched), str([s for s, _ in fetched]))
    check("and the bytes are a real JPEG or PNG, not an error page",
          all(head[:2] == b"\xff\xd8" or head[:4] == b"\x89PNG" for _, head in fetched),
          str([head for _, head in fetched]))
else:
    print("\n(live controls skipped: set KDX_LIVE=1 with credentials to run them)")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

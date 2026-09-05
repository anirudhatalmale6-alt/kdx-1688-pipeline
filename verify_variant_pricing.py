#!/usr/bin/env python3
"""
The two faults the client found on 5 September 2026, each turned into a test
that fails on the old code and passes on the new one.

  1. One photograph priced ten different machines. Offer 1078382952230, an
     industrial hydraulic puller: ten SKUs from 78.15 to 641.91 SAR landed, all
     priced from a single Amazon row at 289.00 SAR - five published at 283.22
     and five refused for selling at a loss. His three complaints ("10 options
     became 5", "one price for a product that has several", "this is not even
     on Amazon") are that one fault.

  2. A placeholder weight decided the delivery flag. A 16.22 SAR tracker tag
     went to his shop marked free shipping while the audit file said fast,
     because the payload re-derived the flag from the 10.5 kg stand-in.

Every fixture here is the real thing: the ten prices are read off the client's
own screenshot of the 1688 buy panel (¥140 to ¥1150), and the five rival prices
are the ones stored in /opt/kdx/comparisons.json for that offer.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import mapping                                              # noqa: E402
import rules                                                # noqa: E402

PASS = FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------
# His listing, as 1688 actually sells it
# --------------------------------------------------------------------------

PULLER_CNY = ["140", "230", "410", "550", "835", "250", "340", "540", "765", "1150"]


def puller(prices=None) -> rules.Product:
    prices = prices or PULLER_CNY
    return rules.Product(
        offer_id="1078382952230",
        title_zh="整体液压拉马轴承拔轮器三爪拉马整体拉马拆卸工具工业级二爪拉马",
        description_zh="",
        images=["https://cbu01.alicdn.com/img/ibank/one-photo.jpg"],
        category_path="工业品 > 五金工具",
        variants=[
            rules.Variant(sku_id=f"62929860506{98 + index}",
                          attributes={"size": f"{price}型"},
                          price_cny=Decimal(price), stock=999,
                          weight_kg=Decimal("12"))
            for index, price in enumerate(prices)
        ],
    )


# The five Amazon rows the search really returned for that one photograph.
RIVALS = ["289.00", "336.10", "521.55", "687.90", "1169.99"]


def hits(prices=None, variant: str = "") -> list:
    return [rules.CompetitorHit(platform="Amazon", price_sar=Decimal(price),
                                match_score=Decimal("100"),
                                url=f"https://www.amazon.sa/{price}",
                                matched_variant=variant)
            for price in (prices or RIVALS)]


ENGINE = rules.Engine(cny_to_sar=Decimal("0.558"))


def run(product, hit_list) -> list:
    return ENGINE.evaluate(product, {variant.sku_id: list(hit_list)
                                     for variant in product.variants})


# --------------------------------------------------------------------------
section("1. His report, reproduced end to end")
# --------------------------------------------------------------------------

results = run(puller(), hits())
published = [r for r in results if r.decision in (rules.Decision.PUBLISH,
                                                  rules.Decision.UPDATE)]
prices = sorted({str(r.final_price_sar) for r in published})

check("all ten options are published", len(published), 10)
check("ten options carry ten different prices", len(prices), 10)
check("no option is priced from the 289.00 row",
      any(str(r.final_price_sar) == "283.22" for r in published), False)
check("none is refused for selling at a loss",
      [r.audit.reason_code for r in results if r.audit.reason_code == "would_sell_at_loss"],
      [])
check("every row says why the comparison was dropped",
      {r.audit.reason_code for r in results}, {"margin_photo_not_variant"})
check("the reason is readable in Arabic too",
      all("صورة واحدة" in r.audit.pricing_basis for r in published), True)

# The prices must still be the client's own margin table applied to cost, not
# some new invention: cheapest option, 140 CNY.
cheapest = min(published, key=lambda r: r.final_price_sar)
check("cheapest option is priced by the margin table",
      cheapest.audit.pricing_basis.startswith("التكلفة زائد هامش"), True)

# --------------------------------------------------------------------------
section("2. Positive control - the guard must not eat a real match")
# --------------------------------------------------------------------------

# One product, one price, rivals that agree: the comparison must still work
# exactly as it did before, or this fix has quietly turned the feature off.
single = rules.Product(
    offer_id="900", title_zh="不锈钢保温杯", description_zh="", images=["x.jpg"],
    category_path="家居", variants=[rules.Variant(sku_id="s1", attributes={},
                                                 price_cny=Decimal("30"), stock=10,
                                                 weight_kg=Decimal("0.4"))])
agreed = run(single, hits(["100.00", "110.00", "120.00"]))
check("a lone variant is still priced from the cheapest rival",
      agreed[0].audit.pricing_basis, "سعر Amazon ناقص 3%")
# 100.00 SAR sits in his first band - "≤100 SAR minus 3%" - so 97.00, not 98.
check("and undercuts it by the client's percentage",
      str(agreed[0].final_price_sar), "97.00")

# Several variants that cost the SAME are still one product to a photograph.
same = puller(["140"] * 4)
agreed_multi = run(same, hits(["100.00", "105.00"]))
check("variants at one price still accept a product-scope hit",
      {r.audit.matched_platform for r in agreed_multi}, {"Amazon"})

# --------------------------------------------------------------------------
section("3. The rival prices refuting themselves")
# --------------------------------------------------------------------------

# Two prices, each standing alone: neither can be checked against the other, so
# the set prices nothing.
refuting = run(single, hits(["289.00", "1169.99"]))
check("a rival set where nothing has company is not used",
      refuting[0].audit.matched_platform, "")
check("and says so", refuting[0].audit.reason_code, "margin_rivals_disagree")

# The rule is "cheapest price that another price stands near", NOT "cheapest".
def picked(*prices):
    got = rules.cheapest_supported(
        [rules.CompetitorHit(platform="Amazon", price_sar=Decimal(p),
                             match_score=Decimal("100")) for p in prices])
    return str(got.price_sar) if got else "REFUSED"


check("a lone cheap row under a cluster is skipped for the cluster",
      picked("12.00", "110.00", "115.00"), "110.00")
check("a single rival price is still used, as it always was",
      picked("100.00"), "100.00")
check("two prices that agree take the cheaper", picked("100.00", "120.00"), "100.00")
check("two prices that do not are both refused", picked("100.00", "400.00"), "REFUSED")
check("the puller's own set has company at the bottom",
      picked(*RIVALS), "289.00")
check("hits_disagree answers the same question",
      rules.hits_disagree(hits(["100.00", "400.00"])), True)
check("and is quiet when a price has company",
      rules.hits_disagree(hits(["100.00", "120.00"])), False)

# So the puller is NOT saved by the price rule - it is saved by the listing
# rule, and it matters that the test says which one did the work.
check("variants_disagree reads the listing", rules.variants_disagree(puller()), True)
check("and clears a listing sold at one price",
      rules.variants_disagree(puller(["140", "150"])), False)

# The threshold is a setting, not a belief.
os.environ["KDX_MAX_HIT_SPREAD"] = "99"
import importlib                                            # noqa: E402
importlib.reload(rules)
check("raising KDX_MAX_HIT_SPREAD gives the isolated cheap row company again",
      str(rules.cheapest_supported(
          [rules.CompetitorHit(platform="Amazon", price_sar=Decimal(p),
                               match_score=Decimal("100"))
           for p in ("12.00", "110.00", "115.00")]).price_sar), "12.00")
del os.environ["KDX_MAX_HIT_SPREAD"]
importlib.reload(rules)
check("and removing it restores the shipped bar", str(rules.MAX_HIT_SPREAD), "1.5")

# --------------------------------------------------------------------------
section("4. The delivery flag his screenshots showed")
# --------------------------------------------------------------------------

def card(weight, assumed, requires) -> dict:
    return mapping.to_kdx_product(
        offer_id="1", name_ar="ا", name_en="a", name_original="a",
        price_sar=Decimal("16.22"), weight_kg=Decimal(str(weight)),
        weight_assumed=assumed, images=["a.jpg"], requires_shipping=requires)


tag = card("2.5", True, "yes")
check("a tracker tag the engine calls light is FAST, not free",
      tag["needs_shipment"], True)
check("and his placeholder still fills the weight field", tag[mapping.WEIGHT_FIELD], 10.5)

heavy = card("12", False, "no")
check("a genuinely heavy product is still free shipping",
      heavy["needs_shipment"], False)
check("with its real weight", heavy[mapping.WEIGHT_FIELD], 12.0)

check("no engine verdict falls back to the weight, as before",
      card("0.4", False, "")["needs_shipment"], True)
check("and the same for a heavy one",
      card("12", False, "")["needs_shipment"], False)

# The old behaviour, stated so its return would be noticed: the placeholder
# alone used to answer this question, and the answer was wrong.
check("the placeholder on its own would still say free (this is the bug)",
      mapping.needs_shipment(mapping.VIRTUAL_WEIGHT_KG), False)

# Sizes under the card must not contradict it.
sized = mapping.to_kdx_product(
    offer_id="1", name_ar="ا", name_en="a", name_original="a",
    weight_kg=Decimal("2.5"), weight_assumed=True, images=["a.jpg"],
    requires_shipping="yes",
    variants=[{"original": "أسود", "image": "a.jpg", "images": ["a.jpg"],
               "sizes": [{"original": "M", "price": Decimal("16.22"),
                          "weight": Decimal("2.5")}]}])
check("the size under it agrees with the card",
      sized["variants"][0]["sizes"][0]["needs_shipment"], True)

# --------------------------------------------------------------------------
section("5. The audit column and the payload are one decision")
# --------------------------------------------------------------------------

light = rules.Product(
    offer_id="2", title_zh="蓝牙防丢器", description_zh="", images=["a.jpg"],
    category_path="数码", variants=[rules.Variant(sku_id="s", attributes={},
                                                 price_cny=Decimal("20"), stock=5,
                                                 weight_kg=Decimal("0.05"))])
row = run(light, [])[0]
payload = mapping.to_kdx_product(
    offer_id="2", name_ar="ا", name_en="a", name_original="a",
    price_sar=row.final_price_sar, weight_kg=Decimal("0.05"), weight_assumed=True,
    images=["a.jpg"], requires_shipping=row.audit.requires_shipping)
check("audit says fast", row.audit.shipping_type, "fast")
check("payload says the same thing", payload["needs_shipment"], True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

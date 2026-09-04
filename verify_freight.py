"""
His shipping rate card of 5 September, and the two ways a cubic metre can be
wrong.

    "الابعاد تضرب في هذا السعر 1018 ثم تحصل على سعر الشحن"   (no electricity)
    "الابعاد تضرب في هذا السعر 1244"                          (220V)
    "0.90 ضرب 0.40 ضرب 0.876 ... 0.31536"                     (his own worked
                                                               example)

The arithmetic is four lines. Everything else in here guards the input, because
1688 states a size for almost nobody - 0 of 151 listings measured on 5 September
- and a volume read wrongly does not fail loudly, it just prices a shirt like a
wardrobe or a wardrobe like a shirt.

Run twice; nothing in here keeps state.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import freight  # noqa: E402
import mapping  # noqa: E402
import rules  # noqa: E402

passed = failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f" [{detail}]" if detail else ""))


def close(a, b, tol="0.01") -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal(tol)


print("1. his rates, and his own worked example")
check("the plain rate is 1018 SAR per cubic metre",
      freight.RATE_PLAIN_SAR_PER_M3 == Decimal("1018"),
      str(freight.RATE_PLAIN_SAR_PER_M3))
check("the electrical rate is 1244", freight.RATE_ELECTRIC_SAR_PER_M3 == Decimal("1244"))
check("and the rate is picked by whether it runs on mains, nothing else",
      freight.rate_for(True) == Decimal("1244") and freight.rate_for(False) == Decimal("1018"))

# "0.90 ضرب 0.40 ضرب 0.876 ثم = ثم تتلقى حجم المنتج الفعلي وهو هكذا 0.31536"
his_volume = Decimal("0.90") * Decimal("0.40") * Decimal("0.876")
check("his multiplication is 0.31536 m3, exactly as he wrote it",
      close(his_volume, "0.31536", "0.000001"), str(his_volume))
check("and at his electrical rate that is 392.31 SAR of shipping",
      close(freight.shipping_sar(his_volume, True), "392.31"),
      str(freight.shipping_sar(his_volume, True)))
check("the same box, not electrical, is 321.04",
      close(freight.shipping_sar(his_volume, False), "321.04"),
      str(freight.shipping_sar(his_volume, False)))

print("\n2. reading a size out of a listing")
volume, source, evidence = freight.volume_m3("收纳箱 45x30x15cm 家用", weight_kg=None)
check("45x30x15cm is read as 0.02025 m3",
      source == "declared" and close(volume, "0.02025", "0.0000001"),
      f"{source} {volume}")
check("and the row can say which text produced it", "45x30x15" in evidence, evidence)

for text, expect in (("规格 45*30*15 厘米", "0.02025"),
                     ("尺寸：450×300×150mm", "0.02025"),
                     ("长80宽60高35厘米 大号", "0.168"),
                     ("尺寸 0.9x0.4x0.876m", "0.31536")):
    got, src, _ = freight.volume_m3(text)
    check(f"{text[:20]} -> {expect} m3",
          src == "declared" and close(got, expect, "0.000001"), f"{src} {got}")

print("\n3. CONTROLS: what must NOT be read as a product size")
# The one real carton spec in the 151 measured listings. 0.079 m3 of outer box
# holding an unknown number of squeeze toys; charging one toy for the carton
# multiplies its freight by however many fit inside.
carton = "空心球皮减压DIY软胶玩具 箱规57.5x32.4x42.5 产品单个克重 71-80g"
_v, src, _e = freight.volume_m3(carton)
check("a CARTON spec is refused, not used as the piece size",
      src != "declared", f"{src} {_v}")

_v, src, _e = freight.volume_m3('<img width="350" height="350" src="a.jpg"> 连衣裙')
check("CONTROL numbers inside an HTML tag are not a product size", src != "declared",
      f"{src} {_v}")

_v, src, _e = freight.volume_m3("2024-09-05 新款 型号 A1-B2-C3 连衣裙")
check("CONTROL a date and a part number are not a size", src != "declared",
      f"{src} {_v}")

_v, src, _e = freight.volume_m3("集装箱 240x120x260 米 大型仓库")
check("CONTROL an implausible piece - over 2 m3 - is refused rather than "
      "priced", src != "declared", f"{src} {_v}")

_v, src, _e = freight.volume_m3("裙长 长裙 袖长 无袖 尺码 M L XL")
check("CONTROL clothing OPTIONS are not dimensions - 裙长 is a hem length, "
      "not a package", src != "declared", f"{src} {_v}")

check("CONTROL a listing with no size at all and no weight costs nothing to "
      "ship, which is his own 0 x 0 x 0",
      freight.volume_m3("连衣裙 夏季新款")[1] == "none")

print("\n4. the weight road, and that it never pretends to be the other one")
volume, source, evidence = freight.volume_m3("连衣裙 夏季新款", weight_kg="0.35")
check("a 0.35 kg listing with no size gets a volume from its weight",
      source == "weight" and close(volume, Decimal("0.35") / Decimal("200"), "0.000001"),
      f"{source} {volume}")
check("and the row says so, with the density it used",
      "200" in evidence and "kg" in evidence, evidence)
check("0.35 kg is 1.78 SAR of shipping at the plain rate",
      close(freight.shipping_sar(volume, False), "1.78"),
      str(freight.shipping_sar(volume, False)))
check("50 kg is 254.50 SAR - the weight road is not free for heavy things",
      close(freight.shipping_sar(freight.volume_from_weight("50"), False), "254.50"),
      str(freight.shipping_sar(freight.volume_from_weight("50"), False)))
check("CONTROL a stated size WINS over the weight estimate - the measurement "
      "beats the assumption",
      freight.volume_m3("收纳箱 45x30x15cm", weight_kg="50")[1] == "declared")
check("CONTROL zero weight and no size is zero, not a division by nothing",
      freight.volume_from_weight(0) == Decimal("0"))

print("\n5. through the engine: freight is part of the cost now")
engine = rules.Engine(cny_to_sar=Decimal("0.558"))


def listing(title="连衣裙 夏季新款", weight="0.35", price="10", desc=""):
    return rules.Product(
        offer_id="1", title_zh=title, description_zh=desc, images=["a.jpg"],
        variants=[rules.Variant(sku_id="s1", attributes={}, price_cny=Decimal(price),
                                stock=5, weight_kg=Decimal(weight))])


product = listing()
variant = product.variants[0]
goods = engine.goods_cost_sar(variant)
landed = engine.landed_cost_sar(variant, product)
check("goods alone is still the plain conversion", close(goods, "5.58"), str(goods))
check("landed cost is goods plus freight", landed > goods, f"{goods} -> {landed}")
check("and it is goods plus exactly the quoted freight",
      close(landed, goods + engine.freight_quote(product, variant)["sar"]),
      f"{landed}")
check("CONTROL asked without the listing it falls back to goods only, rather "
      "than inventing a freight it has no text to compute",
      engine.landed_cost_sar(variant) == goods)

electrical = listing(title="电风扇 220V 50Hz 家用", weight="0.35")
check("a 220V product is quoted at the electrical rate",
      engine.freight_quote(electrical, electrical.variants[0])["rate"] == Decimal("1244"))
check("CONTROL a dress is not", engine.freight_quote(product, variant)["rate"] == Decimal("1018"))

results = engine.evaluate(product, {})
row = results[0].audit
check("every audit row carries the freight it used", row.freight_sar not in ("", None),
      str(row.freight_sar))
check("and the volume, and where the volume came from",
      row.volume_source in ("declared", "weight", "none"), row.volume_source)
check("cost_sar in the log is the LANDED cost, not the goods",
      close(row.cost_sar, landed), f"{row.cost_sar} vs {landed}")
check("the price is still above the landed cost, so the margin is real profit "
      "and not a subsidy on the freight",
      Decimal(row.final_price_sar) > Decimal(row.cost_sar),
      f"{row.final_price_sar} vs {row.cost_sar}")

# The band is chosen from the price the shipping is already inside - his order:
# "ثم يلصق سعر الشحن على سعر المنتج" and only then "ثم اجعل هامش الربح".
# Light, so it reaches the margin at all: an unmatched product over 2 kg is
# still refused outright, which is his rule and is untouched by any of this.
banded_product = listing(price="80", weight="1.5")   # 80 CNY = 44.64 SAR goods
banded_row = engine.evaluate(banded_product, {})[0].audit
banded = rules.marked_up_price(Decimal(banded_row.cost_sar))[1]
check("the margin band is picked from the cost WITH freight in it",
      close(Decimal(banded_row.final_price_sar),
            Decimal(banded_row.cost_sar) * (1 + banded)),
      f"{banded_row.final_price_sar} / {banded_row.cost_sar} @ {banded}")
check("CONTROL an unmatched product over 2 kg is still refused, exactly as "
      "before - the rate card did not quietly open that gate",
      engine.evaluate(listing(price="80", weight="30"), {})[0].audit.reason_code
      == "heavy_and_unmatched")

print("\n6. a rival that cannot cover the shipping")
# 20 CNY of goods is 11.16 SAR; 30 kg of freight is another 152.70. A rival at
# 60 SAR looked like a healthy sale until today.
bulky = listing(price="20", weight="30")
hit = rules.CompetitorHit(platform="Amazon", price_sar=Decimal("60"),
                          match_score=Decimal("100"), matched_variant="s1")
row = engine.evaluate(bulky, {"s1": [hit]})[0].audit
check("a heavy product whose rival price is under the LANDED cost is refused, "
      "which the goods-only cost could never see",
      row.reason_code == "would_sell_at_loss", f"{row.reason_code} {row.cost_sar}")
rich = rules.CompetitorHit(platform="Amazon", price_sar=Decimal("400"),
                           match_score=Decimal("100"), matched_variant="s1")
row = engine.evaluate(bulky, {"s1": [rich]})[0].audit
check("CONTROL a rival that DOES cover it still publishes at his discount",
      row.decision == "publish" and row.matched_platform == "Amazon",
      f"{row.decision} {row.reason_code}")

print("\n7. the virtual weight goes in the field, not into the rules")
check("his number is over 10 kg", mapping.VIRTUAL_WEIGHT_KG > Decimal("10"),
      str(mapping.VIRTUAL_WEIGHT_KG))
check("a measured weight is sent unchanged",
      mapping.weight_to_send("0.35", False) == Decimal("0.35"))
check("an unweighed listing is sent as his virtual figure",
      mapping.weight_to_send("2.5", True) == mapping.VIRTUAL_WEIGHT_KG)

payload = mapping.to_kdx_product(
    offer_id="1", name_ar="فستان", name_en="Dress", name_original="连衣裙",
    price_sar=Decimal("50"), weight_kg=Decimal("2.5"), images=["a.jpg"],
    weight_assumed=True)
check("so the payload carries 10.5 kg", payload[mapping.WEIGHT_FIELD] == 10.5,
      str(payload[mapping.WEIGHT_FIELD]))
check("and free shipping with it, which is what he is setting up in his panel",
      payload["needs_shipment"] is False)

payload = mapping.to_kdx_product(
    offer_id="1", name_ar="فستان", name_en="Dress", name_original="连衣裙",
    price_sar=Decimal("50"), weight_kg=Decimal("0.35"), images=["a.jpg"],
    weight_assumed=False)
check("CONTROL a weighed light product is untouched and still ships fast",
      payload[mapping.WEIGHT_FIELD] == 0.35 and payload["needs_shipment"] is True,
      str(payload[mapping.WEIGHT_FIELD]))

# The invariant he cared about on 2 September: the flag and the number his shop
# charges from must be the same number.
deep = mapping.to_kdx_product(
    offer_id="1", name_ar="فستان", name_en="Dress", name_original="连衣裙",
    weight_kg=Decimal("2.5"), images=[], weight_assumed=True,
    variants=[{"original": "أسود", "images": ["a.jpg"], "price": 50,
               "sizes": [{"original": "M", "price": 50, "sku_id": "s1",
                          "weight": 2.5}]}])
size = deep["variants"][0]["sizes"][0]
check("the sizes underneath carry the SAME figure as the card, so his panel "
      "cannot read one weight in one place and another in the other",
      size["weight"] == deep[mapping.WEIGHT_FIELD] == 10.5,
      f"{size['weight']} vs {deep[mapping.WEIGHT_FIELD]}")
check("and the same shipping flag",
      size["needs_shipment"] is deep["needs_shipment"] is False)

print("\n8. the engine still decides from evidence, never from the virtual number")
check("2.5 kg unweighed is what the ENGINE sees, so the 2 kg gate is unchanged",
      rules.shipping_flag(Decimal("2.5")) == ("no", "free"))
check("CONTROL and a category-learned 0.2 kg is still fast, which is how his "
      "own two cart products went out",
      rules.shipping_flag(Decimal("0.2")) == ("yes", "fast"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

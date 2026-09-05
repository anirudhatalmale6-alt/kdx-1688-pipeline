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
volume, source, evidence = freight.volume_m3("收纳箱 45x30x15cm 家用")
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

print("\n3. the three listings out of 151 that DO state a triple, all of which "
      "were read wrongly the first time")
# 830489165036, live in his shop at 204.82 SAR. The unit is written in
# full-width characters, so the first version read 348 x 105 x 38 CENTIMETRES:
# 1.39 m3 and 1,413 SAR of freight on a 178 SAR MIDI keyboard.
volume, source, evidence = freight.volume_m3(
    "midi键盘控制器25键便携智能键盘乐器无线迷你 规格 348X105X38ｍｍ")
check("full-width ｍｍ is millimetres, so the MIDI keyboard is 0.00139 m3",
      source == "declared" and close(volume, "0.0013889", "0.000001"),
      f"{source} {volume}")
check("which is 1.41 SAR of freight, not 1,413",
      close(freight.shipping_sar(volume, False), "1.41"),
      str(freight.shipping_sar(volume, False)))

# 611415954620: six numbers, a LIST of fabric cut options.
_v, src, _e = freight.volume_m3("洗水棉竹节全棉竹节砂洗棉布料 1#*20*20*60*60*120*140cm")
check("a run of SIX numbers is a list of options, not a box - its first three "
      "are not read as one", src != "declared", f"{src} {_v}")

# 1069466826544: the volume the printer can print, not the box it arrives in.
_v, src, _e = freight.volume_m3("Q2高精度开源3D打印机 打印尺寸 270X270X270mm")
check("打印尺寸 is the print bed, not the parcel", src != "declared",
      f"{src} {_v}")

print("\n4. CONTROLS: what must NOT be read as a product size")
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

check("CONTROL a stated size WINS over every default below it",
      freight.volume_m3("收纳箱 45x30x15cm")[1] == "declared")

# 1072111096507, from the live run of 5 September: the seller states the size
# of the OBJECT. 32 x 32 x 9 mm is a true measurement and not a parcel.
volume, source, evidence = freight.volume_m3("挂锁 规格 32*32*9mm")
check("a stated size smaller than any real parcel is lifted to the smallest "
      "parcel, not shipped for one halala",
      source == "declared" and close(volume, "0.00075", "0.0000001"),
      f"{source} {volume}")
check("and the row shows both numbers, so the lift is visible",
      "32*32*9" in evidence and "15x10x5" in evidence, evidence)
check("which is 0.76 SAR instead of 0.01",
      close(freight.shipping_sar(volume, False), "0.76"),
      str(freight.shipping_sar(volume, False)))
check("CONTROL a stated size ABOVE the floor is untouched",
      close(freight.volume_m3("收纳箱 45x30x15cm")[0], "0.02025", "0.0000001"))

import tempfile as _tf                                              # noqa: E402
_h = _tf.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
_h.write("777,4,4,1\n")
_h.close()
_kept_file = freight.DIMS_FILE
freight.DIMS_FILE = _h.name
check("CONTROL a box HE types is never lifted - if he measured it, that is "
      "the box",
      close(freight.volume_m3("挂锁", offer_id="777")[0], "0.000016", "0.0000001"),
      str(freight.volume_m3("挂锁", offer_id="777")[0]))
freight.DIMS_FILE = _kept_file

print("\n5. the default carton - his decision of 5 September, no kilograms in "
      "it anywhere")
volume, source, evidence = freight.volume_m3("连衣裙 夏季新款")
check("a dress with no stated size gets the clothing carton, not zero",
      source == "family" and close(volume, "0.003", "0.000001"),
      f"{source} {volume}")
check("and the row shows the box itself, so a wrong figure is a wrong box",
      "30x25x4cm" in evidence and "clothing" in evidence, evidence)
check("which is 3.05 SAR of freight on a dress",
      close(freight.shipping_sar(volume, False), "3.05"),
      str(freight.shipping_sar(volume, False)))

volume, source, _e = freight.volume_m3("某种没有关键词的东西 现货批发")
check("a product in no family at all falls to the default carton",
      source == "default" and close(volume, "0.006", "0.000001"),
      f"{source} {volume}")

check("CONTROL nothing ever returns zero volume any more - his 0 x 0 x 0 "
      "would have shipped the whole shop for free",
      all(freight.volume_m3(t)[0] > 0 for t in
          ("", "连衣裙", "3D打印机", "锁", "x", "0 x 0 x 0")))
check("CONTROL and the density road is gone, not merely unused",
      not hasattr(freight, "volume_from_weight")
      and not hasattr(freight, "DENSITY_KG_PER_M3"))

# 910007827618, a real listing: a sew-on rhinestone whose title ends with the
# places it can be used - hair, shoes, clothing. The default box must come from
# what the thing IS.
stone = "混彩玻璃爪钻水滴太阳花梨形手缝石水钻diy发饰鞋子服"
_v, src, ev = freight.volume_m3(stone, family_category="饰品配件",
                                family_title=stone)
check("its CATEGORY decides, so a rhinestone goes in a jewellery bag even "
      "though its title says shoes", "jewellery" in ev, ev)
check("CONTROL read the title first and it lands in a shoe box - which is the "
      "mistake the category-first order exists to stop",
      "shoes" in freight.volume_m3(stone, family_title=stone)[2])
check("CONTROL a category naming no family at all hands over to the title",
      "clothing" in freight.volume_m3("连衣裙", family_category="其他",
                                      family_title="连衣裙 夏季新款")[2])
check("the description is not consulted for the family at all",
      "clothing" in freight.volume_m3(
          "连衣裙 夏季新款 附赠收纳盒 玩具赠品",
          family_category="女装", family_title="连衣裙 夏季新款")[2])

# The families are not decoration: a bra and a 3D printer must not share a box.
small = freight.volume_m3("文胸 无痕内衣")[0]
big = freight.volume_m3("3D打印机 高精度")[0]
check("a 3D printer's carton is bigger than a bra's, by a lot",
      big > small * 20, f"{big} vs {small}")

print("\n6. through the engine: freight is part of the cost now")
engine = rules.Engine(cny_to_sar=Decimal("0.558"))


def listing(title="连衣裙 夏季新款", weight="0.35", price="10", desc="",
            offer_id="1"):
    return rules.Product(
        offer_id=offer_id, title_zh=title, description_zh=desc, images=["a.jpg"],
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
      row.volume_source in ("override", "declared", "family", "default"),
      row.volume_source)
check("and the box, spelled out in centimetres", "cm" in row.volume_note,
      row.volume_note)
check("cost_sar in the log is the LANDED cost, not the goods",
      close(row.cost_sar, landed), f"{row.cost_sar} vs {landed}")
check("the price is still above the landed cost, so the margin is real profit "
      "and not a subsidy on the freight",
      Decimal(row.final_price_sar) > Decimal(row.cost_sar),
      f"{row.final_price_sar} vs {row.cost_sar}")

# The band is chosen from the price the shipping is already inside - his order:
# "ثم يلصق سعر الشحن على سعر المنتج" and only then "ثم اجعل هامش الربح".
banded_product = listing(price="80", weight="1.5")   # 80 CNY = 44.64 SAR goods
banded_row = engine.evaluate(banded_product, {})[0].audit
banded = rules.marked_up_price(Decimal(banded_row.cost_sar))[1]
check("the margin band is picked from the cost WITH freight in it",
      close(Decimal(banded_row.final_price_sar),
            Decimal(banded_row.cost_sar) * (1 + banded)),
      f"{banded_row.final_price_sar} / {banded_row.cost_sar} @ {banded}")

print("\n7. the gate he opened on 5 September")
heavy = engine.evaluate(listing(price="80", weight="30"), {})[0]
heavy_row = heavy.audit
check("an unmatched product over 2 kg is now PUBLISHED at cost plus freight "
      "plus margin - his answer of 5 September",
      heavy_row.decision == "publish", f"{heavy_row.decision} {heavy_row.reason_code}")
check("and it is labelled so he can count what the gate let through",
      heavy_row.reason_code == "margin_unmatched_heavy", heavy_row.reason_code)
check("its price is above its landed cost",
      Decimal(heavy_row.final_price_sar) > Decimal(heavy_row.cost_sar),
      f"{heavy_row.final_price_sar} vs {heavy_row.cost_sar}")
check("CONTROL the comparison is untouched: a rival that IS found still sets "
      "the price, which is the half he told me not to stop",
      engine.evaluate(
          listing(price="80", weight="30"),
          {"s1": [rules.CompetitorHit(platform="Noon", price_sar=Decimal("400"),
                                      match_score=Decimal("100"),
                                      matched_variant="s1")]}
      )[0].audit.matched_platform == "Noon")

print("\n8. a rival that cannot cover the shipping")
# A stated 90 x 40 x 87.6 cm box - his own worked example - is 321.04 SAR of
# freight. 20 CNY of goods is 11.16. A rival at 60 SAR looks like a sale and is
# a loss of 272.
bulky = listing(price="20", weight="30", desc="尺寸 90x40x87.6cm 大件")
hit = rules.CompetitorHit(platform="Amazon", price_sar=Decimal("60"),
                          match_score=Decimal("100"), matched_variant="s1")
row = engine.evaluate(bulky, {"s1": [hit]})[0].audit
check("a heavy product whose rival price is under the LANDED cost is refused, "
      "which the goods-only cost could never see",
      row.reason_code == "would_sell_at_loss", f"{row.reason_code} {row.cost_sar}")
rich = rules.CompetitorHit(platform="Amazon", price_sar=Decimal("900"),
                           match_score=Decimal("100"), matched_variant="s1")
row = engine.evaluate(bulky, {"s1": [rich]})[0].audit
check("CONTROL a rival that DOES cover it still publishes at his discount",
      row.decision == "publish" and row.matched_platform == "Amazon",
      f"{row.decision} {row.reason_code}")

print("\n9. a size he types himself beats everything")
import tempfile                                                    # noqa: E402
handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8")
handle.write("# offer_id,length_cm,width_cm,height_cm\n")
handle.write("999,50,40,30\n")
handle.write("this line is broken\n")
handle.close()
freight.DIMS_FILE = handle.name
volume, source, evidence = freight.volume_m3("连衣裙 夏季新款", offer_id="999")
check("his own box wins over the family default",
      source == "override" and close(volume, "0.06", "0.000001"),
      f"{source} {volume}")
check("CONTROL a broken line does not stop the file being read",
      freight.volume_m3("连衣裙", offer_id="998")[1] == "family")
check("CONTROL a missing file is not an error either",
      (setattr(freight, "DIMS_FILE", "/nonexistent/dims.csv") or True)
      and freight.volume_m3("连衣裙", offer_id="999")[1] == "family")

print("\n10. the flag for boxes worth measuring by hand")
check("a 30 kg product on a guessed box is flagged",
      freight.quote("某种东西", weight_kg="30")["wants_measuring"])
check("CONTROL a 30 kg product with a STATED size is not - there is nothing "
      "to measure", not freight.quote("尺寸 45x30x15cm", weight_kg="30")["wants_measuring"])
check("CONTROL a 0.35 kg dress is not flagged either",
      not freight.quote("连衣裙", weight_kg="0.35")["wants_measuring"])
check("and the flag reaches the audit row in words he can read",
      "قياس" in engine.evaluate(listing(price="80", weight="30"), {})[0].audit.volume_note,
      engine.evaluate(listing(price="80", weight="30"), {})[0].audit.volume_note)

print("\n11. the virtual weight goes in the field, not into the rules")
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

print("\n12. the engine still decides from evidence, never from the virtual number")
check("2.5 kg unweighed is what the ENGINE sees, so the 2 kg gate is unchanged",
      rules.shipping_flag(Decimal("2.5")) == ("no", "free"))
check("CONTROL and a category-learned 0.2 kg is still fast, which is how his "
      "own two cart products went out",
      rules.shipping_flag(Decimal("0.2")) == ("yes", "fast"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

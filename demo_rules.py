#!/usr/bin/env python3
"""
Offline demonstration of the KDX decision engine.

No network, no credentials: this runs the client's own rules against a set of
hand-built products that each trigger a different branch, and prints the audit
log that the real pipeline would write for every variant it touches.

    python3 demo_rules.py
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from rules import Engine, Product, Variant, CompetitorHit  # noqa: E402

CNY_TO_SAR = Decimal("0.52")


def variant(sku, price_cny, stock, weight, **attributes):
    return Variant(
        sku_id=sku,
        attributes=attributes,
        price_cny=Decimal(str(price_cny)),
        stock=stock,
        weight_kg=Decimal(str(weight)),
    )


def hit(platform, price, score, sku=""):
    return CompetitorHit(
        platform=platform,
        price_sar=Decimal(str(price)),
        match_score=Decimal(str(score)),
        matched_variant=sku,
    )


CASES = [
    (
        "matched on a comparison platform -> undercut",
        Product(
            offer_id="600001",
            title_zh="无线蓝牙耳机",
            description_zh="便携 蓝牙5.3 耳机",
            images=["a.jpg", "b.jpg"],
            variants=[variant("600001-BLK", 55, 400, 0.35, color="أسود")],
        ),
        {"600001-BLK": [hit("Amazon", 129.00, 97, "600001-BLK"),
                        hit("Noon", 141.00, 96, "600001-BLK")]},
    ),
    (
        "no match, light -> cost plus margin band",
        Product(
            offer_id="600002",
            title_zh="纯棉短袖T恤",
            description_zh="夏季 男士 T恤",
            images=["a.jpg"],
            variants=[variant("600002-L", 38, 120, 0.30, size="L")],
        ),
        {},
    ),
    (
        "no match, over 2 kg -> rejected",
        Product(
            offer_id="600003",
            title_zh="实木折叠餐桌",
            description_zh="家用 餐桌",
            images=["a.jpg"],
            variants=[variant("600003-OAK", 420, 18, 14.5, color="بلوط")],
        ),
        {},
    ),
    (
        "electrical but not 220V 50/60Hz -> rejected",
        Product(
            offer_id="600004",
            title_zh="电热水壶 110V 60Hz",
            description_zh="快速烧水 功率 1500W 电源 110V",
            images=["a.jpg"],
            variants=[variant("600004-STD", 96, 60, 1.1)],
        ),
        {},
    ),
    (
        "electrical and compliant -> accepted",
        Product(
            offer_id="600005",
            title_zh="电动榨汁机 220V 50/60Hz",
            description_zh="家用 榨汁机 额定电压 220V 50/60Hz 功率 300W",
            images=["a.jpg"],
            variants=[variant("600005-WHT", 130, 75, 1.8, color="أبيض")],
        ),
        {},
    ),
    (
        "banned category -> rejected",
        Product(
            offer_id="600006",
            title_zh="电子烟 一次性 vape",
            description_zh="vape pen nicotine",
            images=["a.jpg"],
            variants=[variant("600006-STD", 25, 900, 0.10)],
        ),
        {},
    ),
    (
        "two variants, only one matched -> priced independently",
        Product(
            offer_id="600007",
            title_zh="双肩背包",
            description_zh="学生 背包 防水",
            images=["a.jpg"],
            variants=[
                variant("600007-RED", 62, 200, 0.80, color="أحمر"),
                variant("600007-BLU", 88, 150, 0.95, color="أزرق"),
            ],
        ),
        # The red hit must not be reused for the blue variant.
        {"600007-RED": [hit("AliExpress", 118.00, 96, "600007-RED")]},
    ),
    (
        "match below 95 -> treated as not found",
        Product(
            offer_id="600008",
            title_zh="陶瓷马克杯",
            description_zh="办公室 水杯",
            images=["a.jpg"],
            variants=[variant("600008-STD", 19, 500, 0.45)],
        ),
        {"600008-STD": [hit("Temu", 44.00, 88, "600008-STD")]},
    ),
    (
        "undercut would fall below cost -> rejected",
        Product(
            offer_id="600009",
            title_zh="不锈钢保温杯",
            description_zh="户外 保温瓶",
            images=["a.jpg"],
            variants=[variant("600009-STD", 140, 90, 0.70)],
        ),
        {"600009-STD": [hit("SHEIN", 70.00, 98, "600009-STD")]},
    ),
    (
        "already in KDX -> update, not a new product",
        Product(
            offer_id="600010",
            title_zh="硅胶手机壳",
            description_zh="防摔 手机壳",
            images=["a.jpg"],
            variants=[variant("600010-CLR", 12, 800, 0.06)],
        ),
        {},
    ),
]

COLUMNS = [
    ("sku_id", "SKU", 14),
    ("decision", "decision", 8),
    ("cost_sar", "cost", 8),
    ("matched_platform", "platform", 11),
    ("match_score", "score", 6),
    ("competitor_price_sar", "rival", 8),
    ("final_price_sar", "price", 9),
    ("requires_shipping", "ship?", 6),
    ("shipping_type", "type", 6),
]


def main() -> int:
    engine = Engine(cny_to_sar=CNY_TO_SAR, existing_skus={"600010-CLR"})

    header = "  ".join(label.ljust(width) for _, label, width in COLUMNS)
    print(f"\nCNY -> SAR rate: {CNY_TO_SAR}    match threshold: 95%\n")
    print(header)
    print("-" * len(header))

    published = rejected = updated = 0
    footnotes = []

    for title, product, hits in CASES:
        for result in engine.evaluate(product, hits):
            row = result.audit.as_dict()
            print("  ".join(str(row[key] or "-").ljust(width) for key, _, width in COLUMNS))
            footnotes.append((row["sku_id"], row["pricing_basis"], row["reason_ar"], title))
            if row["decision"] == "publish":
                published += 1
            elif row["decision"] == "update":
                updated += 1
            else:
                rejected += 1

    print(f"\npublish: {published}    update: {updated}    reject: {rejected}\n")
    print("reason recorded for each row (this is what the audit table stores):\n")
    for sku, basis, reason, title in footnotes:
        basis_text = f" [{basis}]" if basis else ""
        print(f"  {sku:<14} {reason}{basis_text}")
        print(f"  {'':<14} ({title})")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

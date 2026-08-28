"""
Repeatable proof that the KDX push layer works against the live endpoint,
in the exact schema the client specified.

Run it twice in a row: the second run must still report one product, not two.

    KDX_API_TOKEN=... python3 verify_kdx.py

Every check is a pair - the thing we expect to work AND a control we expect to
fail - because a lone success proves nothing about what caused it.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from kdx_client import KdxClient, KdxError, MUTABLE  # noqa: E402
from mapping import needs_shipment, to_kdx_product  # noqa: E402

BASE = os.environ.get("KDX_BASE_URL", "https://kdx-sa.com")
TOKEN = os.environ.get("KDX_API_TOKEN", "")

# Real, reachable image URLs on purpose: KDX hot-links whatever it is given, so
# an invented URL shows the customer a broken-image box.
IMAGES = [
    "https://image.uniqlo.com/UQ/ST3/us/imagesgoods/470922/feature/usgoods_470922_feature1.jpg",
    "https://image.uniqlo.com/UQ/ST3/id/imagesgoods/488008/feature/idgoods_488008_feature1.jpg",
]

PRODUCT = to_kdx_product(
    offer_id="TEST-104843239419",
    name_ar="اختبار تنورة كاجوال",
    name_en="Test Pleated Casual Skirt",
    name_original="百褶半身裙",
    price_sar="5.39",
    weight_kg="0.35",
    images=IMAGES,
    sizes=["S", "M", "L"],
    main_category={"id": 100, "name_original": "女装",
                   "name_en": "Women's Clothing", "name_ar": "ملابس نسائية"},
    sub_category={"id": 1031912, "name_original": "半身裙",
                  "name_en": "Skirt", "name_ar": "تنورة"},
    description_ar="تنورة كاجوال بطيات، خامة خفيفة مناسبة للاستخدام اليومي.",
    description_en="Pleated casual skirt in a light fabric for everyday wear.",
)

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


def main() -> int:
    if not TOKEN:
        print("set KDX_API_TOKEN first")
        return 2

    client = KdxClient(BASE, TOKEN)

    print("1. the schema is built exactly as the client specified")
    check("category.main_category is a list", isinstance(PRODUCT["category"]["main_category"], list))
    check("category.sub_category is a list", isinstance(PRODUCT["category"]["sub_category"], list))
    check("sizes are objects, not strings",
          PRODUCT["sizes"][0] == {"original": "S", "en": "S", "ar": "S"}, str(PRODUCT["sizes"][:1]))
    check("needs_shipment is a real boolean", PRODUCT["needs_shipment"] is True)
    check("price_currency is SAR", PRODUCT["price_currency"] == "SAR")
    check("product_url is built from the offer id",
          PRODUCT["product_url"].endswith("TEST-104843239419.html"), PRODUCT["product_url"])

    print("2. shipping flag follows weight, at the boundary too")
    check("0.00 kg -> fast (true)", needs_shipment("0.00") is True)
    check("2.00 kg -> fast (true)", needs_shipment("2") is True)
    check("2.01 kg -> free (false)", needs_shipment("2.01") is False)
    check("14.50 kg -> free (false)", needs_shipment("14.5") is False)

    print("3. the live endpoint accepts it")
    response = client.push([PRODUCT])[0]
    check("import returns success", response.get("success") is True, str(response))
    check("one product imported", response.get("imported_count") == 1, str(response))
    check("none failed", response.get("failed_count") == 0, str(response))

    print("4. control: a wrong token must be refused")
    try:
        KdxClient(BASE, TOKEN + "x").push([PRODUCT])
        check("wrong token rejected", False, "it was accepted")
    except KdxError as exc:
        check("wrong token rejected", "401" in str(exc), str(exc))

    print("5. control: a product with no name_en must be caught before it leaves")
    try:
        client.to_payload({"source_offer_id": "X"})
        check("missing name_en caught", False, "it passed")
    except KdxError:
        check("missing name_en caught", True)

    print("6. control: KDX itself must reject a malformed schema")
    try:
        client._post("/api/v1/products/import",
                     {"products": [{"source_offer_id": "TEST-104843239419",
                                    "name_en": "x", "needs_shipment": "notabool"}]})
        check("bad needs_shipment rejected by KDX", False, "it was accepted")
    except KdxError as exc:
        check("bad needs_shipment rejected by KDX", "must be true or false" in str(exc), str(exc))

    print("7. the update payload carries nothing that must never change")
    payload = client.to_payload(PRODUCT, MUTABLE)
    for forbidden in ("sku", "product_url", "rating", "sales", "stock"):
        check(f"update payload has no '{forbidden}'", forbidden not in payload)

    print("8. same source_offer_id again - must update, never duplicate")
    again = client.update([{**PRODUCT, "price": 5.49}])[0]
    check("second send still reports 1 imported",
          again.get("imported_count") == 1 and again.get("failed_count") == 0, str(again))

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

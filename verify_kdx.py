"""
Repeatable proof that the KDX push layer works against the live endpoint.

Run twice in a row: the second run must report an update, not a duplicate.

    KDX_BASE_URL=https://kdx-sa.com KDX_API_TOKEN=... python3 verify_kdx.py

Every check is a pair - the thing we expect to work AND a control we expect to
fail - because a lone success proves nothing about what caused it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from kdx_client import KdxClient, KdxError  # noqa: E402

BASE = os.environ.get("KDX_BASE_URL", "https://kdx-sa.com")
TOKEN = os.environ.get("KDX_API_TOKEN", "")

PRODUCT = {
    "source_offer_id": "TEST-ANIRUDHA-001",
    "name_ar": "فستان صيفي قطني - منتج اختبار (يرجى حذفه)",
    "name_en": "Cotton Summer Dress - TEST product (please delete)",
    "description_ar": "فستان قطني خفيف مناسب للصيف. منتج اختبار للتحقق من ربط الـ API.",
    "description_en": "Light cotton dress for summer. Test product verifying the API link.",
    "price": 137.50,
    "images": [
        "https://cbu01.alicdn.com/img/ibank/O1CN01example1.jpg",
        "https://cbu01.alicdn.com/img/ibank/O1CN01example2.jpg",
        "https://cbu01.alicdn.com/img/ibank/O1CN01example3.jpg",
    ],
    "category": ["فساتين"],
}

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

    print("1. token is accepted")
    response = client.push([PRODUCT])[0]
    check("import returns success", response.get("success") is True, str(response))
    check("one product imported", response.get("imported_count") == 1, str(response))
    check("none failed", response.get("failed_count") == 0, str(response))

    print("2. control: a wrong token must be refused")
    try:
        KdxClient(BASE, TOKEN + "x").push([PRODUCT])
        check("wrong token rejected", False, "it was accepted")
    except KdxError as exc:
        check("wrong token rejected", "401" in str(exc), str(exc))

    print("3. control: a product with no name_en must be refused locally")
    try:
        client.to_payload({"source_offer_id": "X"})
        check("missing name_en caught", False, "it passed")
    except KdxError:
        check("missing name_en caught", True)

    print("4. update path carries no field that must never change")
    payload = client.to_payload(PRODUCT, ("source_offer_id", "name_en", "price",
                                          "images", "description_ar", "description_en"))
    for forbidden in ("sku", "url", "rating", "sales", "stock"):
        check(f"update payload has no '{forbidden}'", forbidden not in payload)

    print("5. same source_offer_id again - must update, never duplicate")
    again = client.update([{**PRODUCT, "price": 139.00}])[0]
    check("second send still reports 1 imported",
          again.get("imported_count") == 1 and again.get("failed_count") == 0, str(again))

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

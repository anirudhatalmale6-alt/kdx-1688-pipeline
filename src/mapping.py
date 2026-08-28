"""
Turn one priced variant into the exact JSON shape KDX asked for.

The client sent the schema verbatim and asked for it to be matched exactly, so
this module owns the shape and nothing else builds product JSON.

Verified against the live endpoint on 2026-08-28 by type-probing every field.
KDX validates:

    source_offer_id           required, string, and the update key
    name_en                   required, string
    name_ar                   string
    description_ar            string
    description_en            string
    price                     number
    images                    array
    sizes                     array
    needs_shipment            boolean
    category.main_category    array
    category.sub_category     array

KDX accepts but does NOT validate or store: source, product_url, name,
name_original, price_currency, weight, sku, stock. They are still sent because
the client asked for this shape, and because they cost nothing - but nothing may
depend on KDX giving them back.
"""

from __future__ import annotations

from decimal import Decimal

# The client sets delivery type from this one boolean:
#   True  -> 0.00-2 kg  -> fast shipping
#   False -> over 2 kg   -> free shipping
# No shipping fee is charged to the customer in either case.
LIGHT_MAX_KG = Decimal("2")

OFFER_URL = "https://detail.1688.com/offer/{offer_id}.html"


def needs_shipment(weight_kg) -> bool:
    return Decimal(str(weight_kg)) <= LIGHT_MAX_KG


def category_block(main: dict | None, sub: dict | None) -> dict:
    """
    Both halves must be arrays - KDX rejects an object or a string here.
    A missing half becomes an empty array rather than a null.
    """
    return {
        "main_category": [main] if main else [],
        "sub_category": [sub] if sub else [],
    }


def size_block(sizes) -> list:
    """
    Sizes travel as objects, not bare strings. 1688 sizes are usually already
    latin (S/M/L/XL) or numeric, so the three fields normally carry the same
    text; a Chinese size word is translated into the en/ar slots instead.
    """
    block = []
    for size in sizes or []:
        if isinstance(size, dict):
            original = str(size.get("original", "")).strip()
            block.append({"original": original,
                          "en": str(size.get("en") or original).strip(),
                          "ar": str(size.get("ar") or original).strip()})
        else:
            text = str(size).strip()
            block.append({"original": text, "en": text, "ar": text})
    return [entry for entry in block if entry["original"]]


def to_kdx_product(*, offer_id: str, name_ar: str, name_en: str, name_original: str,
                   price_sar, weight_kg, images: list, sizes=None,
                   main_category: dict | None = None, sub_category: dict | None = None,
                   description_ar: str = "", description_en: str = "",
                   product_url: str = "") -> dict:
    """
    Build one product in the client's schema.

    `images` must already be narrowed to the single variant whose price is being
    published - the client's rule is that a photo may never sit next to a price
    that belongs to a different variant.
    """
    if not offer_id:
        raise ValueError("offer_id is required: it is the KDX update key")
    if not name_en:
        raise ValueError("name_en is required by KDX")

    product = {
        "source": "1688",
        "source_offer_id": str(offer_id),
        "product_url": product_url or OFFER_URL.format(offer_id=offer_id),
        "category": category_block(main_category, sub_category),
        "name": name_ar,
        "name_original": name_original,
        "name_en": name_en,
        "name_ar": name_ar,
        "images": list(images or []),
        "price": float(Decimal(str(price_sar))),
        "price_currency": "SAR",
        "sizes": size_block(sizes),
        "needs_shipment": needs_shipment(weight_kg),
    }

    # Not in the client's sample, but KDX validates and stores both, and the
    # description was part of the agreed scope. Flagged to the client.
    if description_ar:
        product["description_ar"] = description_ar
    if description_en:
        product["description_en"] = description_en

    return product


def from_pricing(result, product, enriched: dict, *, main_category=None,
                 sub_category=None, images=None) -> dict:
    """Adapter from the rules engine's PricingResult to the KDX shape."""
    return to_kdx_product(
        offer_id=product.offer_id,
        name_ar=enriched.get("name_ar", ""),
        name_en=enriched.get("name_en", ""),
        name_original=product.title_zh,
        price_sar=result.final_price_sar,
        weight_kg=result.variant.weight_kg,
        images=images if images is not None else product.images,
        sizes=[result.variant.attributes.get("size")] if result.variant.attributes.get("size") else [],
        main_category=main_category,
        sub_category=sub_category,
        description_ar=enriched.get("description_ar", ""),
        description_en=enriched.get("description_en", ""),
    )

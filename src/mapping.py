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


def _money(value) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def variant_block(variants) -> list:
    """
    One entry per photo, because that is the unit KDX will render: an image with
    its own price under it.

    This mirrors how 1688 actually organises an offer, which is not how it is
    usually described. Photos hang off the colour / style axis, never off the
    size axis, while the price and the stock belong to the full SKU
    (colour x size). So two sizes of the same colour legitimately carry the same
    photo and two different prices. Grouping by photo here means KDX never has
    to do that grouping itself, and never has to guess which price goes under
    which picture.

    Every product gets at least one variant even when it has no colour axis, so
    the rendering loop on the KDX side never needs a special case.
    """
    block = []
    for variant in variants or []:
        original = str(variant.get("original", "")).strip()
        sizes = []
        for size in variant.get("sizes") or []:
            entry = {
                "original": str(size.get("original", "")).strip(),
                "en": str(size.get("en") or size.get("original") or "").strip(),
                "ar": str(size.get("ar") or size.get("original") or "").strip(),
                "price": _money(size["price"]),
            }
            if size.get("sku_id"):
                entry["sku_id"] = str(size["sku_id"])
            if size.get("stock") is not None:
                entry["stock"] = int(size["stock"])
            if size.get("weight") is not None:
                entry["weight"] = float(size["weight"])
                entry["needs_shipment"] = needs_shipment(size["weight"])
            sizes.append(entry)

        # A variant with no size axis still has to carry a price of its own,
        # otherwise its photo would be published with nothing under it.
        prices = [size["price"] for size in sizes]
        if not prices:
            if variant.get("price") is None:
                raise ValueError(
                    f"variant {original!r} has neither sizes nor a price of its own")
            prices = [_money(variant["price"])]

        images = [image for image in (variant.get("images") or []) if image]
        if variant.get("image") and variant["image"] not in images:
            images.insert(0, variant["image"])

        block.append({
            "original": original,
            "en": str(variant.get("en") or original).strip(),
            "ar": str(variant.get("ar") or original).strip(),
            "image": images[0] if images else "",
            "images": images,
            "price": min(prices),
            "price_min": min(prices),
            "price_max": max(prices),
            "sizes": sizes,
        })
    return block


def to_kdx_product(*, offer_id: str, name_ar: str, name_en: str, name_original: str,
                   price_sar=None, weight_kg, images: list, sizes=None, variants=None,
                   main_category: dict | None = None, sub_category: dict | None = None,
                   description_ar: str = "", description_en: str = "",
                   product_url: str = "") -> dict:
    """
    Build one product in the client's schema.

    Pass `variants` and the product carries every photo and every price, grouped
    so each photo owns the prices that belong to it. `price`, `images` and
    `sizes` are then derived from them and must not be passed by hand:

        price   the cheapest price in the whole offer, for the product card
        images  every photo, in variant order, deduplicated
        sizes   the union of the size names, kept without prices so the current
                KDX front end keeps working unchanged on the day this switches

    Without `variants` the older single-price shape still builds, and `images`
    must then already be narrowed to the one variant being published - a photo
    may never sit next to a price belonging to a different variant.
    """
    if not offer_id:
        raise ValueError("offer_id is required: it is the KDX update key")
    if not name_en:
        raise ValueError("name_en is required by KDX")

    block = variant_block(variants)
    if block:
        card_price = min(entry["price_min"] for entry in block)
        highest = max(entry["price_max"] for entry in block)
        gallery: list = []
        for entry in block:
            for image in entry["images"]:
                if image not in gallery:
                    gallery.append(image)
        for image in images or []:
            if image not in gallery:
                gallery.append(image)
        flat_sizes: list = []
        for entry in block:
            for size in entry["sizes"]:
                name = {"original": size["original"], "en": size["en"], "ar": size["ar"]}
                if name not in flat_sizes:
                    flat_sizes.append(name)
    else:
        if price_sar is None:
            raise ValueError("price_sar is required when no variants are given")
        card_price = highest = _money(price_sar)
        gallery = list(images or [])
        flat_sizes = size_block(sizes)

    product = {
        "source": "1688",
        "source_offer_id": str(offer_id),
        "product_url": product_url or OFFER_URL.format(offer_id=offer_id),
        "category": category_block(main_category, sub_category),
        "name": name_ar,
        "name_original": name_original,
        "name_en": name_en,
        "name_ar": name_ar,
        "images": gallery,
        "price": card_price,
        "price_currency": "SAR",
        "price_min": card_price,
        "price_max": highest,
        "sizes": flat_sizes,
        "needs_shipment": needs_shipment(weight_kg),
    }
    if block:
        product["variants"] = block

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

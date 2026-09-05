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

WEIGHT, 2 September. The product had no weight of its own at all: the number
lived only inside variants[].sizes[], and a product with no size axis - 95 of
230 in the live catalogue - carried none anywhere. His shop computes the fast
delivery fee from a product weight, so every one of those checked out with no
fee and the word "free shipping" next to a fast delivery he pays for.

The product-level number is now sent. Whether his importer reads it is not
something this end can see: type-probed against the live endpoint on 2
September, `weight`, `weight_kg`, `product_weight` and `shipping_weight` were
all accepted carrying the string "abc", while the same request with price="abc"
was rejected 422 and one with name_en missing was rejected 422. So the
validator is real and none of those four names is in it. WEIGHT_FIELD holds the
key so his answer costs a restart, not a release.
"""

from __future__ import annotations

import os
from decimal import Decimal

# The key his importer reads the weight under. Four guesses were wrong (above),
# so this is his to set rather than mine to assume.
WEIGHT_FIELD = os.environ.get("KDX_WEIGHT_FIELD", "weight")

# The client sets delivery type from this one boolean:
#   True  -> 0.00-2 kg  -> fast shipping
#   False -> over 2 kg   -> free shipping
#
# "No shipping fee is charged in either case" stood here until 2 September and
# was wrong. His own cart, photographed: 65.20 SAR of goods, 28.00 SAR of fast
# delivery, 93.20 SAR to pay. Fast delivery is charged, and charged from a
# weight - which is why a product arriving without one silently became free.
LIGHT_MAX_KG = Decimal("2")

# The number to send when NOBODY weighed the thing. His instruction, 5
# September: "افعل وزن المنتج على الموجود في 1688 او وهمي اكثر من 10 kg حتى
# اكمل اعداد الشحن المجاني من خلال لوحة التحكم" - the real 1688 weight when
# there is one, otherwise a made-up figure over 10 kg, so that his control
# panel can put those products on free shipping.
#
# It fills the FIELD and nothing else. The engine's own decisions - which side
# of the 2 kg line a product falls, whether a heavy unmatched product may be
# published - keep reading the weight the engine resolved, because a number
# invented to drive his panel is not evidence about a box. Sending 10.5 into
# the rules would have re-labelled every unweighed product heavy and stopped
# it publishing, which is the opposite of what he asked for.
VIRTUAL_WEIGHT_KG = Decimal(os.environ.get("KDX_VIRTUAL_WEIGHT_KG", "10.5"))

OFFER_URL = "https://detail.1688.com/offer/{offer_id}.html"


def needs_shipment(weight_kg) -> bool:
    return Decimal(str(weight_kg)) <= LIGHT_MAX_KG


# 🚨 5 September 2026, and the reason this indirection exists at all.
#
# The client sent three screenshots from his own shop: a Bluetooth tracker tag
# at 16.22 SAR, a paper cup carrier at 16.16, a small wall lamp at 243.05 - all
# three showing "توصيل مجاني" with 25-40 working days, the slow free channel.
# His verdict: "انت تسحب منتجات صغيرة وتجعلها شحن مجاني [...] هذا العمل غير
# صحيح وغير مقبول".
#
# He was right, and the audit file said the OPPOSITE. All three rows read
# shipping_type=fast, because the audit records what the ENGINE decided from the
# weight it resolved. The payload did not: it ran the placeholder weight - the
# 10.5 kg he asked for on 5 September so his panel could configure free shipping
# - back through needs_shipment(), and 10.5 kg is over the 2 kg line, so every
# product 1688 never weighed went to his shop flagged heavy.
#
# ⭐⭐ The lesson worth more than the fix: MY OWN LOG WAS NOT EVIDENCE ABOUT
# WHAT I SENT. Two places computed the same flag from two different numbers, and
# the file I would have checked showed the right answer while the shop showed the
# wrong one. The flag is now decided once, by the engine, and the payload is
# handed the result - it is no longer able to reach a different verdict.
#
# The placeholder itself is unchanged and still sent in the weight field, which
# is what he actually asked for. It fills a field. It no longer decides one.
def shipment_flag(requires_shipping: str, weight_kg=None) -> bool:
    """
    The flag KDX renders, taken from the engine's own shipping decision.

    `requires_shipping` is the audit's own column - "yes" fast, "no" free. When
    a caller has no engine decision to offer, the weight is used exactly as
    before, so every existing call site keeps its behaviour.
    """
    flag = (requires_shipping or "").strip().lower()
    if flag in ("yes", "true", "fast"):
        return True
    if flag in ("no", "false", "free"):
        return False
    return needs_shipment(weight_kg if weight_kg is not None else 0)


def weight_to_send(weight_kg, assumed: bool = False) -> Decimal:
    """The figure that goes in the payload: measured if it exists, else his."""
    if assumed:
        return VIRTUAL_WEIGHT_KG
    return Decimal(str(weight_kg))


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


# OPTIONS AS SIZES, 5 September.
#
# Every purchase option lives in variants[], with its own price and its own
# photo. But variants is outside the ten fields his importer validates - it is
# accepted by the HTTP layer and then discarded - and an offer with no size axis
# sends sizes[] empty. So a front end reading only sizes has nowhere to find the
# fifty prices, and shows one.
#
# Mirroring the options into sizes[] with their prices renders them on his
# current site with no change on his side. It is off until he asks for it,
# because turning it on changes what every sizeless product looks like, and he
# is reviewing the current shape right now.
#
# Narrow on purpose: only when there is no real size axis. Where sizes exist
# they keep travelling without prices, exactly as they do today.
MIRROR_OPTIONS_AS_SIZES = os.environ.get(
    "KDX_OPTIONS_AS_SIZES", "").strip().lower() in ("1", "yes", "true", "on")


def options_as_sizes(block: list) -> list:
    """
    One sizes[] entry per purchase option, carrying that option's own price.

    The option's name is its own - the colour/style text 1688 hangs the photo
    off - so the shop lists what the buyer actually chooses between.
    """
    entries = []
    for entry in block:
        if not entry["original"]:
            continue
        item = {"original": entry["original"],
                "en": entry["en"],
                "ar": entry["ar"],
                "price": entry["price_min"]}
        if entry.get("image"):
            item["image"] = entry["image"]
        entries.append(item)
    return entries


def variant_block(variants, weight_assumed: bool = False,
                  requires_shipping: str = "") -> list:
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
                # The same substitution as the product level, or the two
                # disagree: a listing nobody weighed would say 10.5 kg on the
                # card and 2.5 kg on every size under it, and his panel would
                # read one of them.
                sent = weight_to_send(size["weight"], weight_assumed)
                entry["weight"] = float(sent)
                # And the same rule about the FLAG: a real weight decides for
                # itself, a placeholder never does. Without the engine's verdict
                # the size would fall back to 10.5 kg and go free-shipping while
                # the product card above it said fast.
                entry["needs_shipment"] = (
                    shipment_flag(requires_shipping, size["weight"])
                    if weight_assumed else needs_shipment(sent))
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
                   product_url: str = "", weight_assumed: bool = False,
                   requires_shipping: str = "") -> dict:
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

    block = variant_block(variants, weight_assumed, requires_shipping)
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
        # No size axis and more than one thing to buy: see MIRROR_OPTIONS_AS_SIZES.
        if MIRROR_OPTIONS_AS_SIZES and not flat_sizes and len(block) > 1:
            flat_sizes = options_as_sizes(block)
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
        # The weight field carries his placeholder when nobody weighed the
        # thing; the FLAG comes from the engine. See shipment_flag - letting the
        # placeholder decide the flag is the bug he reported on 5 September.
        WEIGHT_FIELD: float(weight_to_send(weight_kg, weight_assumed)),
        "needs_shipment": shipment_flag(requires_shipping, weight_kg),
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
                 sub_category=None, images=None, weight_assumed: bool = False) -> dict:
    """Adapter from the rules engine's PricingResult to the KDX shape."""
    return to_kdx_product(
        offer_id=product.offer_id,
        name_ar=enriched.get("name_ar", ""),
        name_en=enriched.get("name_en", ""),
        name_original=product.title_zh,
        price_sar=result.final_price_sar,
        weight_kg=result.variant.weight_kg,
        weight_assumed=weight_assumed,
        images=images if images is not None else product.images,
        sizes=[result.variant.attributes.get("size")] if result.variant.attributes.get("size") else [],
        main_category=main_category,
        sub_category=sub_category,
        description_ar=enriched.get("description_ar", ""),
        description_en=enriched.get("description_en", ""),
        # The engine's own verdict, the same string the audit column carries,
        # so the row in his log and the flag in his shop are one decision.
        requires_shipping=getattr(result.audit, "requires_shipping", ""),
    )

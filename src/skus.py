"""
The size and colour table, from the one API that answers for any offer.

Every product in the catalogue arrives through the LinkPlus image search, and
that channel returns eleven fields with no SKU table in them - which is why the
151 products prepared on 30 August all publish with an empty size list. The
detail API that would fill it (alibaba.product.get) is still gw.APIACLDecline,
and the new app's own detail API is not a substitute: asked for one of his
offers it answers, verbatim,

    "offerId:1004582496795 不是精选货源商品"

- it only covers 1688's curated 精选货源 pool, not the shop's catalogue.

product.skuinfo.get does cover it. Measured 2026-09-01 against 38 of the 151
prepared offers, spread across the catalogue: 38 answered, every SKU carried a
specId, and 16 had a real size axis. So this module exists to turn that one
response into the variants and sizes the shop has been publishing empty.

WHAT THIS API DOES NOT CARRY, and therefore what this module must not invent:

    price   there is no per-SKU price in the response. Every size here is
            given the offer price the search row already reported. It is the
            same number the product publishes today, so nothing gets worse -
            but a size is NOT priced individually, and `price_from_offer`
            travels with it saying so.
    stock   absent. The key is left out entirely rather than set to 0, because
            mapping.size_block only emits `stock` when it is not None and a
            zero would read on the shop as "sold out".
    image   absent. 1688 hangs SKU photos off the colour axis in the detail
            API, not here, so every variant keeps the single search photo.

specId is the field that matters beyond display: alibaba.trade.fastCreateOrder
buys a specId, not a size name, so carrying it is what makes it possible to
order the size a customer actually chose.
"""

from __future__ import annotations

from decimal import Decimal

from aop_client import ApiRoute, AopError

SKU_ROUTE = ApiRoute(namespace="com.alibaba.product", api_name="product.skuinfo.get")

# What `sku_source` says on a product, so a caller never has to spell it.
SKU_APPLIED = "product.skuinfo.get"   # sizes came back and were applied
SKU_EMPTY = "empty"                   # 1688 has no table: a one-option product
SKU_FAILED = "failed"                 # the call was refused; see sku_error

# The axis photos and variants hang off. Same list source.py uses, kept here so
# this module can be read on its own.
COLOUR_AXES = ("颜色", "颜色分类", "款式", "颜色/款式", "图案", "color", "colour")


class SkuError(RuntimeError):
    pass


def rows_from(payload: dict) -> list:
    """
    [{"sku_id": str, "spec_id": str, "axes": {name: value}}] from one response.

    The measured shape is result.result.skuSimpleInfos[].attributes[], with
    attributeName / attributeValue. An offer with no SKU table answers
    success: true and no skuSimpleInfos at all, which is not an error - it is a
    product with one option - so that returns an empty list.
    """
    body = payload.get("result")
    if not isinstance(body, dict):
        return []
    if body.get("success") is False:
        raise SkuError(str(body.get("message") or "skuinfo refused")[:200])
    inner = body.get("result")
    if not isinstance(inner, dict):
        return []
    rows = []
    for sku in inner.get("skuSimpleInfos") or []:
        if not isinstance(sku, dict):
            continue
        axes = {}
        for attribute in sku.get("attributes") or []:
            if not isinstance(attribute, dict):
                continue
            name = str(attribute.get("attributeName") or "").strip()
            value = str(attribute.get("attributeValue") or "").strip()
            if name and value:
                axes[name] = value
        if not axes:
            continue
        rows.append({"sku_id": str(sku.get("skuId") or ""),
                     "spec_id": str(sku.get("specId") or ""),
                     "axes": axes})
    return rows


def variant_axis(rows: list) -> str:
    """
    Which axis becomes a variant, and which becomes a size.

    No attribute here carries an image, so the axis cannot be decided the way
    source._photo_axis decides it. It is decided by name instead: a colour or
    style axis becomes the variant, everything else becomes the size.

    When there is no colour axis the answer is deliberately "" - one unnamed
    variant holding every option as a size. The alternative, promoting 规格 to
    a variant, would publish six identical photographs side by side, because
    this channel only ever has one photograph to give them.
    """
    names = []
    for row in rows:
        for name in row["axes"]:
            if name not in names:
                names.append(name)
    for name in names:
        if name in COLOUR_AXES or name.lower() in COLOUR_AXES:
            return name
    return ""


def apply_to(product: dict, rows: list) -> dict:
    """
    A copy of `product` with its variants rebuilt from the SKU table.

    The offer price is taken from the product as it stands, so a product that
    was publishing at 4.00 keeps publishing at 4.00 on every size. Returns the
    product unchanged when there is nothing to apply - an offer with one option
    is not a failure, and overwriting its single variant with an empty one
    would lose the price.
    """
    if not rows:
        return product

    price = _offer_price(product)
    if price is None:
        raise SkuError(f"offer {product.get('offer_id')} has no price to put on its sizes")

    axis = variant_axis(rows)
    gallery = list(product.get("images") or [])
    photo = gallery[0] if gallery else ""

    grouped: dict = {}
    for row in rows:
        key = row["axes"].get(axis, "") if axis else ""
        others = [value for name, value in row["axes"].items() if name != axis]
        entry = grouped.setdefault(key, {
            "original": key, "image": photo, "images": gallery[:1] if photo else [],
            "sizes": [], "price": price,
        })
        size = {
            "original": " / ".join(others),
            "price": price,
            # Said out loud rather than left to be inferred: this number is the
            # offer price, not a price 1688 quoted for this size.
            "price_from_offer": True,
        }
        if row["sku_id"]:
            size["sku_id"] = row["sku_id"]
        if row["spec_id"]:
            # What fastCreateOrder needs to buy this exact option.
            size["spec_id"] = row["spec_id"]
        # A one-axis offer has no size text left over. Its options are the
        # variant keys themselves, so there is nothing to add underneath.
        if size["original"]:
            entry["sizes"].append(size)

    variants = list(grouped.values())
    # Deduplicate identical size rows: two SKUs differing only on an axis this
    # product does not publish would otherwise appear twice under one variant.
    for variant in variants:
        seen, unique = set(), []
        for size in variant["sizes"]:
            if size["original"] in seen:
                continue
            seen.add(size["original"])
            unique.append(size)
        variant["sizes"] = unique

    enriched = dict(product)
    enriched["variants"] = variants
    enriched["sku_source"] = SKU_APPLIED
    enriched["sku_count"] = len(rows)
    return enriched


def _offer_price(product: dict) -> Decimal | None:
    for variant in product.get("variants") or []:
        if variant.get("price") is not None:
            return Decimal(str(variant["price"]))
        for size in variant.get("sizes") or []:
            if size.get("price") is not None:
                return Decimal(str(size["price"]))
    return None


def fetch(client, offer_id: str) -> list:
    """The rows for one offer, or [] when 1688 has no table for it."""
    payload = client.call(SKU_ROUTE, {"offerId": str(offer_id)})
    return rows_from(payload)


def enrich(client, product: dict) -> dict:
    """
    fetch + apply, with the failure mode that keeps a night running.

    A product that publishes with no sizes is the status quo and is worth far
    more than a night that stops on one offer, so a refusal here is swallowed
    and recorded on the product rather than raised. `sku_error` is what an
    audit reads to tell "this offer has one option" apart from "this offer was
    never asked".
    """
    offer_id = str(product.get("offer_id") or "")
    if not offer_id:
        return product
    try:
        rows = fetch(client, offer_id)
    except (AopError, SkuError) as exc:
        failed = dict(product)
        failed["sku_source"] = SKU_FAILED
        failed["sku_error"] = str(exc)[:200]
        return failed
    if not rows:
        untouched = dict(product)
        untouched["sku_source"] = SKU_EMPTY
        return untouched
    return apply_to(product, rows)

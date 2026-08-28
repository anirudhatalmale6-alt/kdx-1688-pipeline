"""
Where product data comes from.

The pipeline must not care. Today the client's appKey cannot read a single
product (alibaba.product.get answers gw.APIACLDecline, "AppKey is not
allowed(acl)"), and lifting that needs an Alipay enterprise verification they
cannot complete yet. That is a supply problem, not a design problem, so it is
confined to this one module: everything downstream - cleaning, translation,
pricing, mapping, publishing - is written against the normalised shape below and
never touches 1688 directly.

Three sources implement it:

    AopSource       the official alibaba.product.get. Ready; blocked on the ACL.
    HttpSource      any reseller that already holds the permission, configured
                    entirely by environment variable so no code changes to swap.
    FixtureSource   recorded payloads, so the rest of the system can be built,
                    tested and demonstrated end to end while the ACL is closed.

Swapping source changes one line in the scheduler. Nothing else moves.

The normalised shape is what mapping.to_kdx_product consumes:

    {"offer_id": str, "title_zh": str, "description_zh": str,
     "weight_kg": float, "images": [str], "attributes": {name: value},
     "category_id": str,
     "variants": [{"original": str, "image": str, "images": [str],
                   "sizes": [{"original": str, "price": Decimal,
                              "sku_id": str, "stock": int}]}]}

Prices here are the 1688 price in CNY. Converting, marking up and undercutting
happen later, in rules.py - this module reports what 1688 said and nothing more.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from decimal import Decimal

from aop_client import AopClient, ApiRoute, AopError

PRODUCT_ROUTE = ApiRoute(namespace="com.alibaba.product", api_name="alibaba.product.get")

# 1688 spells the same field several ways depending on API version and package.
# Rather than bet on one spelling, every accessor below tries the known ones in
# order. The alternative is a KeyError on the first live response, at the exact
# moment the permission finally arrives.
_PRODUCT_ROOTS = ("productInfo", "product", "result", "offerDetail")
_IMAGE_KEYS = ("images", "imageURI", "imageUrls", "fullPathImageURIList")
_SKU_KEYS = ("skuInfos", "skuInfo", "skus", "productSkuInfos")
_SKU_ATTR_KEYS = ("attributes", "attributeList", "skuAttributes")
_ATTR_NAME_KEYS = ("attributeName", "attrName", "name", "attributeNameTrans")
_ATTR_VALUE_KEYS = ("attributeValue", "attrValue", "value", "attributeValueTrans")
_ATTR_IMAGE_KEYS = ("skuImageUrl", "skuImageURI", "imageUrl", "image")
_PRICE_KEYS = ("price", "consignPrice", "retailPrice", "skuPrice")
_STOCK_KEYS = ("amountOnSale", "canBookCount", "stock", "quantity")
_SKU_ID_KEYS = ("skuId", "skuID", "specId", "specID")

# Attribute names 1688 uses for the axis that carries photos, checked only when
# no attribute carries an image of its own.
_PHOTO_AXIS_NAMES = ("颜色", "颜色分类", "款式", "颜色/款式", "图案", "color", "colour")


class SourceError(RuntimeError):
    pass


def _first(mapping: dict, keys, default=None):
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) not in (None, "", [], {}):
            return mapping[key]
    return default


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # {"images": {"string": [...]}} appears in some 1688 responses
        for nested in value.values():
            if isinstance(nested, list):
                return nested
        return [value]
    return [value]


def _images_from(node) -> list:
    """Pull an image list out of whatever container 1688 wrapped it in."""
    if node is None:
        return []
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        images = []
        for item in node:
            images.extend(_images_from(item))
        return images
    if isinstance(node, dict):
        for key in _IMAGE_KEYS:
            if key in node:
                return _images_from(node[key])
        return []
    return []


def _attribute_pairs(sku: dict) -> list:
    """Return [(name, value, image)] for one SKU's attributes."""
    pairs = []
    for attribute in _as_list(_first(sku, _SKU_ATTR_KEYS, [])):
        if not isinstance(attribute, dict):
            continue
        name = str(_first(attribute, _ATTR_NAME_KEYS, "") or "").strip()
        value = str(_first(attribute, _ATTR_VALUE_KEYS, "") or "").strip()
        image = str(_first(attribute, _ATTR_IMAGE_KEYS, "") or "").strip()
        if value:
            pairs.append((name, value, image))
    return pairs


def _photo_axis(skus: list) -> str | None:
    """
    Decide which attribute the photos hang off, from the data rather than from
    a hardcoded Chinese word.

    On 1688 the image lives on the colour / style attribute and never on size,
    so the axis is simply "the attribute that carries an image". Only when no
    attribute carries one at all does this fall back to matching the usual
    names, and then to the first attribute present.
    """
    named = []
    for sku in skus:
        for name, _value, image in _attribute_pairs(sku):
            if image:
                return name
            if name and name not in named:
                named.append(name)
    for candidate in named:
        if candidate.lower() in _PHOTO_AXIS_NAMES:
            return candidate
    return named[0] if named else None


def _price_of(sku: dict, fallback: Decimal | None) -> Decimal:
    raw = _first(sku, _PRICE_KEYS)
    if raw is None:
        if fallback is None:
            raise SourceError(f"sku {_first(sku, _SKU_ID_KEYS, '?')} has no price")
        return fallback
    if isinstance(raw, dict):  # {"price": {"value": "12.50"}}
        raw = _first(raw, ("value", "amount", "price"), "0")
    return Decimal(str(raw))


def _base_price(product: dict) -> Decimal | None:
    """The offer-level price, used only when a SKU carries none of its own."""
    sale = _first(product, ("productSaleInfo", "saleInfo", "priceInfo"), {}) or {}
    ranges = _as_list(_first(sale, ("priceRangeList", "priceRanges", "ranges"), []))
    prices = []
    for entry in ranges:
        if isinstance(entry, dict):
            value = _first(entry, _PRICE_KEYS)
            if value is not None:
                prices.append(Decimal(str(value)))
    if prices:
        return min(prices)
    direct = _first(sale, _PRICE_KEYS) or _first(product, _PRICE_KEYS)
    return Decimal(str(direct)) if direct is not None else None


def _weight_of(product: dict) -> float:
    shipping = _first(product, ("productShippingInfo", "shippingInfo", "logisticsInfo"), {}) or {}
    weight = _first(shipping, ("weight", "unitWeight", "grossWeight"))
    if weight is None:
        weight = _first(product, ("weight", "unitWeight"))
    try:
        return float(weight)
    except (TypeError, ValueError):
        # Unknown weight must not silently become "light and fast-shipped".
        # 0 is not a safe default here, so the offer is reported as heavy and
        # the operator sees a free-shipping flag rather than a wrong charge.
        return float(os.environ.get("KDX_DEFAULT_WEIGHT_KG", "2.5"))


def _flat_attributes(product: dict) -> dict:
    """Offer-level attributes, flattened to {name: value} for the 220V filter."""
    flat = {}
    for attribute in _as_list(_first(product, ("productAttribute", "attributes",
                                               "productAttributes"), [])):
        if not isinstance(attribute, dict):
            continue
        name = str(_first(attribute, _ATTR_NAME_KEYS, "") or "").strip()
        value = str(_first(attribute, _ATTR_VALUE_KEYS, "") or "").strip()
        if name:
            flat[name] = value
    return flat


def normalise(payload: dict) -> dict:
    """
    Turn one raw alibaba.product.get response into the normalised shape.

    Written against the documented response and exercised by verify_source.py
    against recorded payloads. The key spellings are tried in groups precisely
    because this has not yet met a live response - the day the ACL opens, the
    first real payload gets recorded as a fixture and this is re-checked against
    it rather than trusted.
    """
    # The product object sits under a different number of wrappers depending on
    # the package - result.productInfo, productInfo, or bare - so descend while
    # a known wrapper is present rather than peeling exactly one layer. Each
    # step goes strictly deeper, and the cap stops a self-referential payload.
    product = payload
    for _depth in range(5):
        nested = next((root for root in _PRODUCT_ROOTS
                       if isinstance(product, dict) and isinstance(product.get(root), dict)), None)
        if nested is None:
            break
        product = product[nested]
    if not isinstance(product, dict) or not product:
        raise SourceError("no product object in response")

    offer_id = str(_first(product, ("productID", "offerId", "offerID", "productId"), "") or "")
    if not offer_id:
        raise SourceError("response carries no offer id")

    gallery = _images_from(_first(product, ("image", "productImage", "images"), {}))
    skus = _as_list(_first(product, _SKU_KEYS, []))
    fallback = _base_price(product)
    axis = _photo_axis(skus)

    # Group the SKUs by the photo axis, keeping first-seen order so the variant
    # order on KDX matches the order 1688 lists them in.
    grouped: dict = {}
    for sku in skus:
        pairs = _attribute_pairs(sku)
        key = ""
        image = ""
        sizes = []
        for name, value, sku_image in pairs:
            if axis is not None and name == axis:
                key = value
                image = image or sku_image
            else:
                sizes.append(value)
        entry = grouped.setdefault(key, {"original": key, "image": "", "images": [], "sizes": []})
        if image and image not in entry["images"]:
            entry["images"].append(image)
            entry["image"] = entry["image"] or image
        size_name = " / ".join(sizes)
        size = {
            "original": size_name,
            "price": _price_of(sku, fallback),
            "stock": int(_first(sku, _STOCK_KEYS, 0) or 0),
        }
        sku_id = _first(sku, _SKU_ID_KEYS)
        if sku_id is not None:
            size["sku_id"] = str(sku_id)
        entry["sizes"].append(size)

    variants = list(grouped.values())
    if not variants:
        # An offer with no SKU table at all: one variant, offer price, gallery
        # photo. Still a valid product, and still has to publish.
        if fallback is None:
            raise SourceError(f"offer {offer_id} has neither skus nor a price")
        variants = [{"original": "", "image": gallery[0] if gallery else "",
                     "images": gallery[:1], "sizes": [],
                     "price": fallback}]

    # Photos that belong to no particular variant still belong in the gallery.
    for variant in variants:
        if not variant["images"] and gallery:
            variant["images"] = gallery[:1]
            variant["image"] = gallery[0]

    return {
        "offer_id": offer_id,
        "title_zh": str(_first(product, ("subject", "title", "subjectTrans"), "") or ""),
        "description_zh": str(_first(product, ("description", "detail", "descUrl"), "") or ""),
        "category_id": str(_first(product, ("categoryID", "categoryId"), "") or ""),
        "weight_kg": _weight_of(product),
        "images": gallery,
        "attributes": _flat_attributes(product),
        "variants": variants,
    }


class AopSource:
    """The official API. Correct, and blocked until the ACL is lifted."""

    def __init__(self, client: AopClient):
        self.client = client

    def get_product(self, offer_id: str) -> dict:
        try:
            payload = self.client.call(PRODUCT_ROUTE,
                                       {"webSite": "1688", "offerId": str(offer_id)})
        except AopError as exc:
            if "acl" in str(exc).lower():
                raise SourceError(
                    "alibaba.product.get is not permitted for this appKey "
                    "(gw.APIACLDecline). Grant the permission, or point "
                    "KDX_SOURCE at a provider that already holds it.") from exc
            raise
        return normalise(payload)


class HttpSource:
    """
    A reseller that already holds the 1688 permission.

    Configured entirely by environment so switching provider - or switching back
    to the official API - needs no code change:

        KDX_SOURCE_URL     required, with {offer_id} where the id goes
        KDX_SOURCE_KEY     optional, substituted as {key}
        KDX_SOURCE_HEADER  optional, "Name: value"
        KDX_SOURCE_ROOT    optional, dotted path to the product object

    Most 1688 resellers pass the platform's own payload straight through, so the
    same normaliser handles them; KDX_SOURCE_ROOT covers the ones that wrap it.
    """

    def __init__(self, url_template: str | None = None, key: str = "", header: str = "",
                 root: str = "", timeout: int = 30):
        self.url_template = url_template or os.environ.get("KDX_SOURCE_URL", "")
        self.key = key or os.environ.get("KDX_SOURCE_KEY", "")
        self.header = header or os.environ.get("KDX_SOURCE_HEADER", "")
        self.root = root or os.environ.get("KDX_SOURCE_ROOT", "")
        self.timeout = timeout
        if not self.url_template:
            raise SourceError("KDX_SOURCE_URL is not set")

    def get_product(self, offer_id: str) -> dict:
        url = self.url_template.format(offer_id=urllib.parse.quote(str(offer_id)), key=self.key)
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        if self.header and ":" in self.header:
            name, _, value = self.header.partition(":")
            request.add_header(name.strip(), value.strip())
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for step in filter(None, self.root.split(".")):
            payload = payload.get(step, {})
        return normalise(payload)


class FixtureSource:
    """
    Recorded payloads. This is what keeps the build moving while the ACL is shut:
    every stage after this one can be written and proved today, and the day a
    real response arrives it is saved here as a fixture and the same tests run
    against it.
    """

    def __init__(self, directory: str | None = None):
        self.directory = directory or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "offers")

    def offer_ids(self) -> list:
        if not os.path.isdir(self.directory):
            return []
        return sorted(name[:-5] for name in os.listdir(self.directory)
                      if name.endswith(".json"))

    def get_product(self, offer_id: str) -> dict:
        path = os.path.join(self.directory, f"{offer_id}.json")
        if not os.path.exists(path):
            raise SourceError(f"no recorded offer {offer_id} in {self.directory}")
        with open(path, encoding="utf-8") as handle:
            return normalise(json.load(handle))


def build_source(client: AopClient | None = None):
    """Pick the source from KDX_SOURCE: aop (default), http, or fixture."""
    choice = os.environ.get("KDX_SOURCE", "aop").strip().lower()
    if choice == "fixture":
        return FixtureSource()
    if choice == "http":
        return HttpSource()
    if client is None:
        raise SourceError("KDX_SOURCE=aop needs a configured AopClient")
    return AopSource(client)

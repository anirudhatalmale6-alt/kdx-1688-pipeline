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


# --------------------------------------------------------------------------
# LinkPlus: the one channel this appKey actually holds.
# --------------------------------------------------------------------------
#
# Measured against the live gateway on 30 August 2026 with the client's own
# credentials, using a control pair - alibaba.product.get answers
# gw.APIACLDecline while this one answers gw.ParamMissing, and a parameter
# complaint can only come from an API we are allowed to call:
#
#     com.alibaba.linkplus / alibaba.cross.similar.offer.search / 1
#     required: picUrl (String), page (Integer)
#     pageSize is accepted but CAPPED AT 20 - asking for 50 or 100 still
#     returns 20, so paging is the only way to go deeper.
#
# It returns eleven fields and no more. There is no weight, no SKU table, no
# description and only one image; every guessed detail-API name in the same
# namespace came back gw.APIUnsupported, so there is nothing to join against.
# That absence is the whole reason for the weight policy below.

LINKPLUS_ROUTE = ApiRoute(namespace="com.alibaba.linkplus",
                          api_name="alibaba.cross.similar.offer.search", version="1")

# oldPrice is an integer in fen. Measured, not assumed: of 20 offers in one
# response, 10 carried prices that are not whole multiples of 100 (1010, 970,
# 1049, ...). An integer field that carries fractions of a yuan cannot itself
# be denominated in yuan.
PRICE_DIVISOR = Decimal(os.environ.get("KDX_LINKPLUS_PRICE_DIVISOR", "100"))


def _category_weights() -> dict:
    """
    {categoryId: kilograms}, from a JSON file or inline JSON.

    The client supplies this because only he knows what he is importing. It is
    deliberately not guessed: see weight_for_category.
    """
    raw = os.environ.get("KDX_CATEGORY_WEIGHTS", "").strip()
    if not raw:
        return {}
    if os.path.exists(raw):
        with open(raw, encoding="utf-8") as handle:
            raw = handle.read()
    try:
        return {str(k): float(v) for k, v in json.loads(raw).items()}
    except (ValueError, AttributeError) as exc:
        raise SourceError(f"KDX_CATEGORY_WEIGHTS is not a JSON object of "
                          f"category -> kilograms: {exc}") from None


def weight_for_category(category_id: str) -> tuple[float, bool]:
    """
    Return (kilograms, is_assumed) for a LinkPlus offer.

    This channel never reports a weight, and the shipping rule turns on 2 kg,
    so something has to fill the gap. What must NOT happen is the gap being
    filled silently: _weight_of's 2.5 kg fallback sits above the 2 kg line, so
    every product would be classed heavy, and by the client's own rule a heavy
    product with no price match is never published. The catalogue would empty
    itself and every audit line would read as though someone had weighed the
    box.

    So the second return value travels with the number, and the audit says the
    weight was assumed rather than measured.

    The client decided on 30 August: products going out by fast shipping may be
    booked at 1 kg. This channel cannot tell fast from slow, so in practice that
    means everything it returns is 1 kg, which is under the 2 kg line, which
    makes it light and fast-shipped. That is his decision and it is recorded
    here rather than buried: KDX_LINKPLUS_LIGHT_WEIGHT_KG holds the number so he
    can move it without a code change.

    Resolution order, in every mode: a category he has given a weight for wins,
    because the only reason to type a number into that table is to state a real
    one. Only where the table is silent does the mode decide.

        KDX_LINKPLUS_WEIGHT_MODE = light (default) | table
        KDX_CATEGORY_WEIGHTS     = {"1031912": 0.5, ...}
        KDX_LINKPLUS_LIGHT_WEIGHT_KG   = light mode's number  (his 1 kg)
        KDX_LINKPLUS_DEFAULT_WEIGHT_KG = table mode's fallback, deliberately
                                         above 2 kg so a silent gap is caught
    """
    table = _category_weights()
    known = table.get(str(category_id))
    if known is not None:
        return float(known), True
    mode = os.environ.get("KDX_LINKPLUS_WEIGHT_MODE", "light").strip().lower()
    if mode == "light":
        return float(os.environ.get("KDX_LINKPLUS_LIGHT_WEIGHT_KG", "1.0")), True
    return float(os.environ.get("KDX_LINKPLUS_DEFAULT_WEIGHT_KG", "2.5")), True


def normalise_search_row(row: dict) -> dict:
    """
    One row of alibaba.cross.similar.offer.search, in the normalised shape.

    Kept separate from normalise() rather than folded into it: that function
    describes a product detail response, and pretending a search row is one
    would hide how much less this channel carries.
    """
    offer_id = str(row.get("offerId") or "")
    if not offer_id:
        raise SourceError("search row carries no offerId")

    raw_price = row.get("oldPrice")
    if raw_price is None:
        raise SourceError(f"offer {offer_id} carries no price")
    price = (Decimal(str(raw_price)) / PRICE_DIVISOR).quantize(Decimal("0.01"))

    image = str(row.get("imageUrl") or "")
    gallery = [image] if image else []
    category_id = str(row.get("categoryId") or "")
    weight, assumed = weight_for_category(category_id)

    return {
        "offer_id": offer_id,
        "title_zh": str(row.get("subject") or ""),
        # This channel has no description at all. An empty string is the
        # truthful answer; inventing one from the title would be worse.
        "description_zh": "",
        "category_id": category_id,
        "weight_kg": weight,
        "weight_assumed": assumed,
        "images": gallery,
        "attributes": {},
        # No SKU table means no colours and no sizes - one variant, one price.
        "variants": [{"original": "", "image": image, "images": gallery,
                      "sizes": [], "price": price}],
        "source_channel": "linkplus",
        "min_order": int(row.get("quantityBegin") or 0),
        "unit": str(row.get("unit") or ""),
        "detail_url": str(row.get("detailUrl") or ""),
    }


def _raise_if_refused(payload: dict, pic_url: str) -> None:
    """
    A photograph Alibaba could not fetch is not a photograph with no matches.

    The gateway is explicit about the difference - success: false with
    SYSTEM_ERROR "handle image error with url ..." - but that lives beside
    `result`, not inside it, so reading only `result.result` turns a dead seed
    into a silent zero. A night that starts on a broken URL would then report
    "no similar products" and nobody would know the URL was the problem.
    """
    if payload.get("success") is False or payload.get("code"):
        message = str(payload.get("message") or payload.get("code") or "refused")
        raise SourceError(f"LinkPlus refused {pic_url}: {message[:200]}")


class LinkPlusSource:
    """
    Discovery by photograph, which is the only shape this channel has.

    It is a search, not a lookup: there is no way to ask for offer 123 by id.
    get_product therefore serves offers this source has already seen, and says
    plainly what it cannot do for anything else, rather than returning an empty
    product that would look like a 1688 outage.
    """

    PAGE_SIZE = 20      # the gateway's cap, measured; asking for more is ignored

    def __init__(self, client: AopClient, country: str = "", language: str = ""):
        self.client = client
        self.country = country or os.environ.get("KDX_LINKPLUS_COUNTRY", "US")
        self.language = language or os.environ.get("KDX_LINKPLUS_LANGUAGE", "en")
        self._seen: dict = {}

    def search_by_image(self, pic_url: str, page: int = 1) -> list:
        if not pic_url:
            raise SourceError("LinkPlus search needs a picUrl")
        payload = self.client.call(LINKPLUS_ROUTE, {
            "picUrl": pic_url,
            "page": int(page),
            "pageSize": self.PAGE_SIZE,
            "country": self.country,
            "language": self.language,
        })
        _raise_if_refused(payload, pic_url)
        rows = ((payload.get("result") or {}).get("result")) or []
        products = []
        for row in rows:
            try:
                product = normalise_search_row(row)
            except SourceError:
                continue          # a row without a price is not a product
            self._seen[product["offer_id"]] = product
            products.append(product)
        return products

    def total_for_image(self, pic_url: str) -> int:
        payload = self.client.call(LINKPLUS_ROUTE, {
            "picUrl": pic_url, "page": 1, "pageSize": self.PAGE_SIZE,
            "country": self.country, "language": self.language,
        })
        return int((payload.get("result") or {}).get("total") or 0)

    def offer_ids(self) -> list:
        return sorted(self._seen)

    def get_product(self, offer_id: str) -> dict:
        product = self._seen.get(str(offer_id))
        if product is None:
            raise SourceError(
                f"offer {offer_id} was not returned by a LinkPlus search. This "
                f"channel only searches by photograph; fetching one offer by id "
                f"needs alibaba.product.get, which is still gw.APIACLDecline.")
        return product


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
    """Pick the source from KDX_SOURCE: aop (default), linkplus, http, fixture."""
    choice = os.environ.get("KDX_SOURCE", "aop").strip().lower()
    if choice == "fixture":
        return FixtureSource()
    if choice == "http":
        return HttpSource()
    if choice == "linkplus":
        if client is None:
            raise SourceError("KDX_SOURCE=linkplus needs a configured AopClient")
        return LinkPlusSource(client)
    if client is None:
        raise SourceError("KDX_SOURCE=aop needs a configured AopClient")
    return AopSource(client)

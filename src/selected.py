"""
The 精选货源 pool: the one channel that hands over a whole product today.

Every product in the shop so far arrived through the LinkPlus image search,
whose row has eleven fields and a single `imageUrl`. That is why the client
wrote "كل المنتجات بصورة واحدة فقط" - one photograph each. It was never a
permission he was missing and never his shop dropping photographs; a SEARCH
channel is not a DETAIL channel.

He was right, though, that the new app can pull complete photographs. Measured
on 1 September 2026 against the live gateway with his own credentials:

    com.alibaba.fenxiao / jxhy.product.getPageList
        walks the pool. 1,950 distinct offers over 40 pages of 50.
    com.alibaba.fenxiao / alibaba.pifatuan.product.detail.list
        the full record for those offers: 4-5 main photographs, the description
        (a further 5-32 photographs inside it), the SKU table WITH per-SKU
        prices and per-SKU photographs, 26 offer attributes, and sometimes a
        real weight.

    30 offers sampled across the whole walk: 30 answered, 30 carried more than
    one photograph, 21 carried a distinct photograph per colour, 12 priced
    their SKUs separately, 8 declared a real weight.

The boundary, and it is the whole reason this is a separate module rather than
a replacement: the detail API answers ONLY for offers inside that pool. Asked
for one of the shop's own offers it says, verbatim,

    "offerId:1004582496795 不是精选货源商品"

so this channel adds 1,950 rich products and takes nothing away from the image
search, which remains the only way to reach the rest of 1688.

Three measured facts are load-bearing here and each is a bug if forgotten:

  pageNum, not pageNo.  `pageNo` is accepted, returns HTTP 200, and serves page
      one forever. The row count is identical, so only diffing the ids across
      pages catches it - a walk that "worked" would have published the same 20
      products every night.

  The batch is all-or-nothing.  Fifty ids in one call return fifty records, but
      one id outside the pool fails the whole request and the other forty-nine
      are lost with it. Hence _details_for splitting on refusal instead of
      giving up on the batch.

  The photograph paths are RELATIVE.  `image.images` and `skuImageUrl` come
      back as "img/ibank/O1CN...jpg" with no host. Twenty-one products reached
      his shop without photographs once already; sending these unprefixed would
      do it again, so absolute_image is applied before anything leaves here and
      verify_selected.py holds a live control that the result really is a JPEG.
"""

from __future__ import annotations

import json
import os
import re

import source as source_module
from aop_client import ApiRoute, AopError

LIST_ROUTE = ApiRoute(namespace="com.alibaba.fenxiao", api_name="jxhy.product.getPageList")
DETAIL_ROUTE = ApiRoute(namespace="com.alibaba.fenxiao",
                        api_name="alibaba.pifatuan.product.detail.list")

# 1688's own CDN host for the relative paths the detail API returns. Every
# absolute URL it returns already points here.
IMAGE_HOST = os.environ.get("KDX_1688_IMAGE_HOST", "https://cbu01.alicdn.com/")

PAGE_SIZE = int(os.environ.get("KDX_POOL_PAGE_SIZE", "50"))
BATCH_SIZE = int(os.environ.get("KDX_POOL_BATCH", "50"))

# The refusal that means "this offer is outside the pool" rather than anything
# having gone wrong. Matched to report it as a skip, never as an error.
NOT_IN_POOL = "不是精选货源商品"

_IMG_IN_HTML = re.compile(r"https?://[^\"'\s<>]+?\.(?:jpg|jpeg|png)", re.IGNORECASE)


class PoolError(RuntimeError):
    pass


def absolute_image(path: str) -> str:
    """
    A usable URL from whatever the detail API gave us.

    Relative in the detail response, absolute in the listing response, and both
    appear in the same product once SKU photographs are merged in.
    """
    path = (path or "").strip()
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("//"):
        return "https:" + path
    return IMAGE_HOST.rstrip("/") + "/" + path.lstrip("/")


def description_images(html: str) -> list:
    """
    The photographs embedded in the description HTML, in order, deduplicated.

    Not included in the gallery by default. On 1688 the description is a sales
    page: size charts, shipping banners and Chinese marketing copy rendered as
    images sit beside the real product shots, and an Arabic shop showing a
    Chinese banner as its second photograph looks broken rather than rich.
    KDX_POOL_DESCRIPTION_IMAGES=1 turns them on for whoever wants them.
    """
    seen, out = set(), []
    for url in _IMG_IN_HTML.findall(html or ""):
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _rows(payload: dict, key: str) -> list:
    body = payload.get("result")
    if not isinstance(body, dict):
        return []
    if body.get("success") is False:
        raise PoolError(str(body.get("message") or "refused")[:200])
    rows = body.get(key)
    return rows if isinstance(rows, list) else []


class SelectedPool:
    """
    Walk the pool, then fetch complete products from it.

    Takes the same client object as everything else, so the two-app router
    decides which credentials com.alibaba.fenxiao belongs to and this module
    never sees a key.
    """

    def __init__(self, client, *, page_size: int = PAGE_SIZE, batch: int = BATCH_SIZE,
                 include_description_images: bool | None = None):
        self.client = client
        self.page_size = page_size
        self.batch = batch
        if include_description_images is None:
            include_description_images = os.environ.get(
                "KDX_POOL_DESCRIPTION_IMAGES", "") in ("1", "true", "yes", "on")
        self.include_description_images = include_description_images
        self.pages_walked = 0
        self.calls = 0
        self.skipped_outside_pool: list = []

    # -- walking ------------------------------------------------------------

    def offer_ids(self, limit: int = 0, max_pages: int = 60) -> list:
        """
        Distinct offer ids from the pool listing, in the order it serves them.

        Stops on an empty page, on a page that adds nothing new - which is what
        a page parameter being ignored looks like, and the only way to see it -
        or once `limit` ids are in hand.
        """
        seen: dict = {}
        for page in range(1, max_pages + 1):
            payload = self.client.call(LIST_ROUTE,
                                       {"pageNum": page, "pageSize": self.page_size})
            self.calls += 1
            rows = _rows(payload, "result")
            if not rows:
                break
            before = len(seen)
            for row in rows:
                ident = row.get("itemId") or row.get("offerId")
                if ident is not None:
                    seen.setdefault(str(ident), row)
            self.pages_walked = page
            if len(seen) == before:
                break
            if limit and len(seen) >= limit:
                break
        ids = list(seen)
        return ids[:limit] if limit else ids

    # -- fetching -----------------------------------------------------------

    def _details_for(self, offer_ids: list) -> list:
        """
        Raw productInfo records for a list of ids, one call where possible.

        A refusal names the offending offer, and the request is all-or-nothing,
        so a batch that fails is split rather than abandoned: the one id outside
        the pool is recorded as skipped and the rest still arrive. Splitting
        halves rather than dropping to singles keeps the call count near one per
        batch in the normal case, where nothing is refused at all.
        """
        if not offer_ids:
            return []
        try:
            payload = self.client.call(
                DETAIL_ROUTE, {"offerIds": json.dumps([int(i) for i in offer_ids])})
            self.calls += 1
            return [entry.get("productInfo") or {} for entry in _rows(payload, "result")]
        except PoolError as refusal:
            if NOT_IN_POOL not in str(refusal):
                raise
            if len(offer_ids) == 1:
                self.skipped_outside_pool.append(str(offer_ids[0]))
                return []
        except AopError:
            raise
        middle = len(offer_ids) // 2
        return (self._details_for(offer_ids[:middle])
                + self._details_for(offer_ids[middle:]))

    def normalise(self, product_info: dict) -> dict:
        """
        One raw record in the shape the rest of the pipeline consumes.

        source.normalise already reads this family of response - the pool detail
        and alibaba.product.get return the same productInfo object - so the work
        here is the two things it cannot know: the host missing from every
        photograph path, and whether the description's own photographs belong in
        the gallery.
        """
        normalised = source_module.normalise({"productInfo": product_info})

        gallery = [absolute_image(url) for url in normalised.get("images") or []]
        gallery = [url for url in gallery if url]
        if self.include_description_images:
            for url in description_images(product_info.get("description") or ""):
                if url not in gallery:
                    gallery.append(url)
        normalised["images"] = gallery

        for variant in normalised.get("variants") or []:
            images = []
            for url in variant.get("images") or []:
                absolute = absolute_image(url)
                if absolute and absolute not in images:
                    images.append(absolute)
            variant["images"] = images or gallery[:1]
            variant["image"] = (images[0] if images
                                else (gallery[0] if gallery else ""))

        normalised["source_channel"] = "selected_pool"

        # The weight, and the trap underneath it. Only 8 of 30 sampled offers
        # declare one; source._weight_of answers 2.5 kg when none is present,
        # which sits ABOVE the client's 2 kg line, and by his own rule a heavy
        # product with no price match is never published. Left alone, this
        # channel would have thrown away roughly two products in three, with
        # every audit line reading as though the box had been weighed.
        #
        # So a declared weight is used as declared, and a missing one falls to
        # the same light-weight policy the image-search channel already runs
        # under - his decision of 30 August, one place, one number - and the
        # flag travels so the audit can tell the two apart.
        shipping = product_info.get("shippingInfo") or {}
        declared = shipping.get("offerSuttleWeight") or shipping.get("unitWeight")
        if declared is None:
            weight, _assumed = source_module.weight_for_category(
                normalised.get("category_id", ""))
            normalised["weight_kg"] = weight
            normalised["weight_assumed"] = True
        else:
            normalised["weight_kg"] = float(declared)
            normalised["weight_assumed"] = False
        return normalised

    def products(self, limit: int = 0, offer_ids: list | None = None):
        """Complete, normalised products from the pool, newest page first."""
        ids = offer_ids if offer_ids is not None else self.offer_ids(limit)
        for start in range(0, len(ids), self.batch):
            for record in self._details_for(ids[start:start + self.batch]):
                if not record:
                    continue
                try:
                    yield self.normalise(record)
                except source_module.SourceError:
                    # A record we cannot shape is reported by its absence in the
                    # count, not by killing the walk mid-batch.
                    continue

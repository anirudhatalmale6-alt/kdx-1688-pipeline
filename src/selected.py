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

so this channel adds rich products and takes nothing away from the image
search, which remains the only way to reach the rest of 1688.

HOW BIG THE POOL REALLY IS - measured 2 September, and it is not 1,950.

The plain walk stops at 2,000 offers because that is the window the listing
serves, not because the catalogue ends there. Two measurements say so:

  * walked again a day later, 1,053 of the 2,000 offers were ones the first
    walk had never shown. The window moves.
  * the listing READS A KEYWORD, which is the part that matters. Asked for
    连衣裙 it returned 50 rows of which 50 titles contained the word; 运动鞋
    returned trainers; and the control that makes this a fact rather than a
    coincidence - the nonsense string "qqzzxxyy" - returned ZERO rows. A list
    that ignored the parameter would have answered its usual fifty.

    Each keyword then pages to exactly 2,000 offers (40 pages of 50, page 41
    empty), and ten offers sampled from each of nine keywords were answered by
    the detail API every time, with hundreds of photograph URLs.

So the reachable catalogue is roughly 2,000 offers PER WORD, all of them with
complete photographs - not one fixed shelf of 1,950. The category tree already
holds 1,481 allowed Chinese category names, which is where the words come from.

Note what this does NOT do: it does not reach offers outside the pool. The
image search stays for those, and it stays the only channel that can look up an
arbitrary offer the client names.

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
import weights as weights_module
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


def _round_robin(lists) -> list:
    """
    One from each list, then the next from each, until they are empty.

    This is what makes a batch of five five DIFFERENT kinds of thing. Handing
    back one word's offers before the next word's means the quota is filled
    from the first word alone.
    """
    rings = [list(rows) for rows in lists]
    out: list = []
    for index in range(max((len(rows) for rows in rings), default=0)):
        for rows in rings:
            if index < len(rows):
                out.append(rows[index])
    return out


class SelectedPool:
    """
    Walk the pool, then fetch complete products from it.

    Takes the same client object as everything else, so the two-app router
    decides which credentials com.alibaba.fenxiao belongs to and this module
    never sees a key.
    """

    def __init__(self, client, *, page_size: int = PAGE_SIZE, batch: int = BATCH_SIZE,
                 include_description_images: bool | None = None,
                 weight_table=None, categories=None):
        self.client = client
        # Only used to walk a leaf's ancestry when the leaf itself has too few
        # declared weights to have an opinion. Optional: without it the table
        # answers on the leaf alone, which is where the data is filed anyway.
        self.categories = categories
        # The learned weights. Loaded once per pool rather than per product:
        # a batch of forty would otherwise read the file forty times and, worse,
        # each read would throw away what the previous product taught it.
        self.weights = (weights_module.WeightTable.load() if weight_table is None
                        else weight_table)
        self.page_size = page_size
        self.batch = batch
        if include_description_images is None:
            include_description_images = os.environ.get(
                "KDX_POOL_DESCRIPTION_IMAGES", "") in ("1", "true", "yes", "on")
        self.include_description_images = include_description_images
        self.pages_walked = 0
        self.calls = 0
        self.skipped_outside_pool: list = []
        # word -> how many distinct offers it produced, so a run can say which
        # words are worth walking again and which have gone dry.
        self.keyword_counts: dict = {}

    # -- walking ------------------------------------------------------------

    def offer_ids(self, limit: int = 0, max_pages: int = 60, keyword: str = "",
                  known=None, need: int = 0) -> list:
        """
        Distinct offer ids from the pool listing, in the order it serves them.

        With `keyword` the listing searches; without it, it serves its default
        window. Either way it stops on an empty page, on a page that adds
        nothing new - which is what a page parameter being ignored looks like,
        and the only way to see it - or once `limit` ids are in hand.

        `known` and `need` together are the stop that matters once the run is a
        batch every twenty minutes instead of one walk a night. `need` is how
        many ids the caller wants that the ledger has NOT seen, and the walk
        ends as soon as it has them. Without it every word was walked to its
        last page - forty calls a word, twelve words a batch, seventy-two
        batches a day - to collect two thousand ids of which the caller kept
        fifty. Counting fresh ids rather than ids is the whole point: a word
        whose first pages the shop already carries must keep walking, not stop
        at a full page of duplicates.

        A keyword with no matches returns an empty first page, and that is a
        legitimate answer rather than a failure: it is exactly what the nonsense
        control returns.
        """
        seen: dict = {}
        fresh = 0
        for page in range(1, max_pages + 1):
            query = {"pageNum": page, "pageSize": self.page_size}
            if keyword:
                query["keyword"] = keyword
            payload = self.client.call(LIST_ROUTE, query)
            self.calls += 1
            rows = _rows(payload, "result")
            if not rows:
                break
            before = len(seen)
            for row in rows:
                ident = row.get("itemId") or row.get("offerId")
                if ident is None:
                    continue
                ident = str(ident)
                if ident in seen:
                    continue
                seen[ident] = row
                if known is None or not known(ident):
                    fresh += 1
            self.pages_walked = page
            if len(seen) == before:
                break
            if limit and len(seen) >= limit:
                break
            if need and fresh >= need:
                break
        ids = list(seen)
        return ids[:limit] if limit else ids

    def offer_ids_for(self, keywords, *, per_keyword: int = 0, limit: int = 0,
                      known=None) -> list:
        """
        Distinct offer ids across several keywords, a share from each and
        returned round-robin.

        `known` is the ledger's membership test. It is applied here rather than
        by the caller for one reason worth spelling out: a word whose 2,000
        offers the shop already carries would otherwise consume the whole
        night's quota with duplicates and the run would publish nothing while
        reporting that it had found plenty. Filtering as we walk means the quota
        is filled from words that still have something new, and keyword_counts
        records what each word actually contributed.

        The share and the round-robin are both about what the shop looks like,
        and both were learned from a live batch. Asking the first word for
        everything the batch needs means the first word answers it: a batch of
        five published five 国际民族服装 costumes, and since a word holds about
        two thousand offers, every batch for days would have come from that one
        word. So each word is asked for its share of what is still missing, and
        the ids are handed back one word at a time round the ring - so five
        products are five different kinds of thing. A word with nothing new
        costs almost nothing and the words behind it take up its share.
        """
        words = [word for word in keywords if word]
        per_word: dict = {}
        spare: dict = {}
        seen: set = set()
        # Which word an id came from, so keyword_counts can be settled from the
        # ids actually returned. Counting the share pass alone under-reported
        # every word that filled in for a neighbour, and that count is what the
        # run prints as "new offers per word".
        owner: dict = {}
        for position, word in enumerate(words):
            gathered = sum(len(rows) for rows in per_word.values())
            if limit and gathered >= limit:
                break
            share = 0
            if limit:
                left_to_find = limit - gathered
                words_left = len(words) - position
                share = max(1, -(-left_to_find // max(words_left, 1)))
            found = self.offer_ids(limit=per_keyword, keyword=word, known=known,
                                   need=share)
            mine: list = []
            for ident in found:
                if ident in seen:
                    continue
                if known is not None and known(ident):
                    continue
                seen.add(ident)
                owner[ident] = word
                mine.append(ident)
            # Everything past this word's share is kept, not thrown away: it is
            # already paid for, and it is what fills the batch when the other
            # words turn out to have nothing new.
            per_word[word] = mine[:share] if share else mine
            spare[word] = mine[share:] if share else []

        ids = _round_robin(per_word.values())
        if limit and len(ids) < limit:
            # Short. A word that ran out gave back less than its share, so the
            # words that had more make up the difference - from what was
            # already fetched first, then by asking again. A batch that comes
            # back under quota publishes under quota, every twenty minutes.
            ids += _round_robin(spare.values())
        if limit and len(ids) < limit:
            for word in words:
                if len(ids) >= limit:
                    break
                for ident in self.offer_ids(limit=per_keyword, keyword=word,
                                            known=known, need=limit - len(ids)):
                    if ident in seen or (known is not None and known(ident)):
                        continue
                    seen.add(ident)
                    owner[ident] = word
                    ids.append(ident)
                    if len(ids) >= limit:
                        break
        ids = ids[:limit] if limit else ids
        for word in words:
            self.keyword_counts[word] = 0
        for ident in ids:
            word = owner.get(ident)
            if word is not None:
                self.keyword_counts[word] = self.keyword_counts.get(word, 0) + 1
        return ids

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

        # The weight, and the trap underneath it. source._weight_of answers
        # 2.5 kg when none is present, which sits ABOVE the client's 2 kg line,
        # and by his own rule a heavy product with no price match is never
        # published. Left alone, this channel would have thrown away every
        # offer that declares nothing, with each audit line reading as though
        # the box had been weighed.
        #
        # Three answers, in this order, and the audit can tell them apart:
        #
        #   1. the supplier's own figure. 153 of 240 offers measured on
        #      3 September across twelve departments - 64%, not the 8 of 30 an
        #      earlier all-clothing sample suggested. It is also what teaches
        #      the table below, so every batch makes the next one better.
        #   2. the leaf category, but only where its own declared weights all
        #      sit on one side of the 2 kg line. This is the client's objection
        #      of 3 September - "the subcategories hold big products and small
        #      ones" - turned into a test the category has to pass.
        #   3. his light-weight policy of 30 August, which is the safe side:
        #      the customer is charged carriage rather than the shop paying it.
        shipping = product_info.get("shippingInfo") or {}
        declared = weights_module.declared_weight(shipping)
        category = normalised.get("category_id", "")
        if declared is not None:
            normalised["weight_kg"] = float(declared)
            normalised["weight_assumed"] = False
            self.weights.observe(category, declared)
            return normalised

        chain = []
        if self.categories is not None:
            try:
                chain = self.categories.chain(category) or []
            except Exception:                               # noqa: BLE001
                # A category index that cannot answer must not cost the product
                # its weight; the leaf alone is still a fair question.
                chain = []
        learned = self.weights.estimate(category, chain)
        if learned is not None:
            normalised["weight_kg"] = float(learned["kg"])
            # Still assumed. A median of other offers in the same leaf is a
            # well-founded policy, not a scale, and an audit that stopped
            # saying so would let a product held back for being heavy read as
            # though it had been weighed.
            normalised["weight_assumed"] = True
            normalised["weight_category_id"] = learned["category_id"]
            normalised["weight_samples"] = learned["samples"]
            return normalised

        weight, _assumed = source_module.weight_for_category(category)
        normalised["weight_kg"] = weight
        normalised["weight_assumed"] = True
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

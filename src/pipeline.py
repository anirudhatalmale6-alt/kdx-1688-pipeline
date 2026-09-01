"""
The whole run, one offer at a time.

    source -> banned/mains filters -> clean + translate -> compare by image
           -> price -> group back into photos -> push to KDX -> audit

Every stage already exists and is tested on its own; this module is only the
wiring, and it is deliberately thin so that a failure is always attributable to
one named stage rather than to "the pipeline".

Three things it is responsible for that no single stage can be:

  - the daily point budget. A run stops when the points are gone, mid-catalogue,
    and says so, rather than failing every remaining call.
  - the reassembly. The rules engine prices one colour-and-size combination at a
    time; KDX renders a photo with prices under it. Grouping the priced results
    back onto their photos happens here.
  - the audit line for every variant, published or not. A rejected variant that
    leaves no trace is indistinguishable from one that was never seen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

import compare
import enrich as enrich_module
import imagetext
import mapping
import photos
import rules
import skus


@dataclass
class OfferOutcome:
    offer_id: str
    product: dict | None          # the KDX payload, or None if nothing survived
    results: list                 # every PricingResult, published or not
    points_spent: int = 0
    error: str = ""
    compared: bool = True         # False when the image search was not run at all
    searches_spent: int = 0       # SerpApi searches this offer cost
    from_cache: bool = False      # the comparison was reused, not bought again
    photos: dict | None = None    # how many photographs survived the check
    kdx_response: dict | None = None   # what his shop said when it took it

    @property
    def published(self) -> int:
        return sum(1 for result in self.results
                   if result.decision == rules.Decision.PUBLISH)


def to_rules_product(normalised: dict) -> rules.Product:
    """
    Flatten the normalised offer into the one-row-per-purchasable-option shape
    the rules engine works in, keeping the colour on each row so the results can
    be grouped back onto their photos afterwards.
    """
    variants = []
    for variant in normalised["variants"]:
        colour = variant.get("original", "")
        images = variant.get("images") or ([variant["image"]] if variant.get("image") else [])
        sizes = variant.get("sizes") or []
        if not sizes:
            # No size axis: the colour itself is the purchasable option.
            sizes = [{"original": "", "price": variant.get("price"),
                      "sku_id": f"{normalised['offer_id']}-{colour or 'default'}",
                      "stock": variant.get("stock", 1)}]
        for size in sizes:
            variants.append(rules.Variant(
                sku_id=str(size.get("sku_id")
                           or f"{normalised['offer_id']}-{colour}-{size.get('original')}"),
                attributes={"color": colour, "size": size.get("original", ""),
                            "images": images},
                price_cny=Decimal(str(size["price"])),
                # A size with no stock figure inherits the variant's, and the
                # variant defaults to 1 - the same "unknown means available"
                # this channel has always published under, now applied to both
                # paths instead of only the one without sizes.
                #
                # The asymmetry that used to be here was not cosmetic. Sizes
                # from product.skuinfo.get carry no stock, because the API does
                # not report any; defaulting them to 0 sent every single one of
                # them into the out_of_stock rejection in rules.py, so adding
                # real sizes to the catalogue would have emptied it. Products
                # from the detail API always set `stock` explicitly, so their
                # genuine zeroes still reject.
                stock=int(size.get("stock", variant.get("stock", 1)) or 0),
                weight_kg=Decimal(str(size.get("weight", normalised["weight_kg"]))),
            ))

    return rules.Product(
        offer_id=normalised["offer_id"],
        title_zh=normalised.get("title_zh", ""),
        description_zh=normalised.get("description_zh", ""),
        images=normalised.get("images", []),
        variants=variants,
        category_path=normalised.get("category_path", ""),
        specifications=normalised.get("attributes", {}),
    )


def to_kdx_variants(results: list, terms: dict) -> list:
    """
    Group the priced results back onto the photo each one belongs to.

    Only published results are included. A colour whose sizes were all rejected
    disappears entirely rather than being published with no price under it, and
    an offer where every colour is rejected produces no product at all.
    """
    grouped: dict = {}
    for result in results:
        if result.decision != rules.Decision.PUBLISH:
            continue
        colour = result.variant.attributes.get("color", "")
        images = result.variant.attributes.get("images") or []
        entry = grouped.setdefault(colour, {
            "original": colour,
            "en": terms.get(colour, {}).get("en", colour),
            "ar": terms.get(colour, {}).get("ar", colour),
            "image": images[0] if images else "",
            "images": list(images),
            "sizes": [],
        })
        size = result.variant.attributes.get("size", "")
        row = {
            "original": size,
            "en": terms.get(size, {}).get("en", size),
            "ar": terms.get(size, {}).get("ar", size),
            "price": result.final_price_sar,
            "sku_id": result.variant.sku_id,
            "stock": result.variant.stock,
            "weight": float(result.variant.weight_kg),
        }
        if not size:
            # A colour with no size axis carries its price directly, so the
            # variant is not left with an empty size row.
            entry["price"] = result.final_price_sar
            continue
        entry["sizes"].append(row)
    return list(grouped.values())


def _restate_uncompared(results: list) -> None:
    """
    Correct the audit reason on a product that was never searched.

    The client's rule is that a heavy product with no match is not published, so
    the engine rejects it as "heavy_and_unmatched" - and that reason says, in
    Arabic, that the product was not found on any comparison platform. When the
    monthly search allowance is gone, or the product was not translated, nobody
    looked, and writing "not found" would be a false statement in the file the
    client reads to understand why his catalogue is short.

    The decision does not change: without a match a heavy product still cannot
    be published. Only the stated reason changes, from a wrong one to a true
    one, and it says the product is waiting rather than refused.
    """
    for result in results:
        if result.audit.reason_code == "heavy_and_unmatched":
            result.audit.reason_code = "not_compared"
            result.audit.reason_ar = (
                "لم تتم المقارنة لهذا المنتج (لم يُبحث عنه). "
                "المنتج ثقيل ولا يُنشر بدون مقارنة - مؤجَّل وليس مرفوضاً")


def _restate_assumed_weight(results: list, normalised: dict) -> None:
    """
    Say "assumed" where the weight was assumed.

    The LinkPlus channel never reports a weight, so source.weight_for_category
    supplies one. The rejection text written by the engine names that number as
    though the box had been on a scale - "the weight 2.5 kg is more than 2 kg" -
    which is a measurement claim we are not entitled to make.

    Same treatment as _restate_uncompared, and for the same reason: the decision
    is untouched, only the sentence the client reads. He needs to be able to
    tell a product that is genuinely too heavy from one that merely fell through
    a gap in his category weight table, because those call for different
    actions - drop the product, or fill in the table.
    """
    if not normalised.get("weight_assumed"):
        return
    category = normalised.get("category_id") or "-"
    for result in results:
        if result.audit.reason_code == "heavy_and_unmatched":
            result.audit.reason_code = "assumed_heavy_and_unmatched"
            result.audit.reason_ar = (
                f"الوزن غير متوفر في هذه القناة، وافترضناه "
                f"{result.variant.weight_kg} كجم للتصنيف {category}. "
                f"بهذا الافتراض يُعد المنتج ثقيلاً، ولم يُعثر له على مطابقة، "
                f"فلم يُنشر. حدِّد وزن هذا التصنيف لتغيير النتيجة")


def _one_response(responses) -> dict:
    """push() batches, and one product is one batch. Keep the shape honest."""
    if not responses:
        return {}
    return responses[0] if isinstance(responses[0], dict) else {}


def _publish_trouble(response: dict) -> str:
    """
    Say what went wrong, or "" if the product really did land.

    Until 2026-08-30 the answer from his shop was thrown away and every push
    was counted as a publication. It is not: the endpoint answers
    `success: true` while reporting `skipped_count: 1` for an offer id it
    already holds, and `failed_count` for one it refused. A run that reports
    twenty-one published when his shop took none of them is worse than a run
    that fails, because nobody goes looking.

    On 2026-08-30 his developer made the same route upsert, and `updated_count`
    joined the reply. Measured with a control pair against the live endpoint: a
    fresh id answered `imported_count: 1, updated_count: 0`; the same id at a
    different price answered `imported_count: 0, updated_count: 1`, and the
    product page then showed the new price, the new name and BOTH photographs.
    So an update is a landing, not trouble - but it is still only a landing if
    a counter says so, which is why the last check below counts both.
    """
    if not response:
        return ""
    if response.get("success") is False:
        return f"KDX refused the product: {str(response.get('message'))[:160]}"
    failed = int(response.get("failed_count") or 0)
    if failed:
        detail = response.get("failed_items") or ""
        return f"KDX rejected the product: {str(detail)[:200]}"
    skipped = int(response.get("skipped_count") or 0)
    if skipped:
        # `skipped` no longer means "already there" - that is now an update. It
        # means his shop declined the product for a reason of its own, and that
        # reason is worth reading rather than counting as published.
        return f"KDX skipped this offer: {str(response.get('message'))[:160]}"
    landed = (int(response.get("imported_count") or 0)
              + int(response.get("updated_count") or 0))
    if landed < 1:
        return f"KDX imported nothing: {str(response)[:200]}"
    return ""


def was_update(response: dict) -> bool:
    """True when his shop updated a product it already held, rather than inserting."""
    return bool(response) and int(response.get("updated_count") or 0) > 0


class Pipeline:
    def __init__(self, *, source, provider, engine, budget=None, audit_log=None,
                 shopping=None,
                 kdx=None, translate: bool = True, dry_run: bool = True,
                 points_per_offer: int = 1, enricher=None, term_translator=None,
                 categories=None, cache=None, meter=None, photo_checker=None,
                 sku_client=None):
        self.source = source
        # The client product.skuinfo.get is called through, or None to publish
        # the way the shop does today - one option per product. It is a
        # separate handle rather than `source` because the sizes do not come
        # from the channel the products come from: LinkPlus has no SKU table at
        # all, and this is the only API measured to answer for an arbitrary
        # offer id (2026-09-01, 38 of the 151 prepared offers, 38 answered).
        self.sku_client = sku_client
        self.provider = provider
        # Prices, when the image search finds the product but not its price.
        self.shopping = shopping
        self.engine = engine
        self.budget = budget
        self.audit_log = audit_log
        self.kdx = kdx
        # Checks each photograph is fetchable before the product is pushed.
        # None disables the check entirely, which is what the unit checks that
        # care about pricing rather than pictures want.
        self.photos = photo_checker
        self.translate = translate
        self.dry_run = dry_run
        self.points_per_offer = points_per_offer
        # Injection points so a run can be driven without an OpenAI key, and so
        # the joins between stages can be tested without the model in the way.
        self.enricher = enricher or enrich_module.enrich
        self.term_translator = term_translator or enrich_module.translate_terms
        # The built category tree, used to name the department KDX files the
        # product under and to refuse a category the client excluded.
        self.categories = categories
        # The SerpApi allowance: what has already been answered (cache) and how
        # much of the month is left to spend (meter).
        self.cache = cache
        self.meter = meter

    def _enrich(self, product: rules.Product) -> dict:
        if not self.translate:
            # Fixture and dry runs must not need an API key. The Chinese title
            # is carried through untouched and clearly marked, so nothing that
            # reaches KDX from a dry run can be mistaken for finished copy.
            return {"name_en": product.title_zh, "name_ar": product.title_zh,
                    "description_ar": "", "description_en": "", "_untranslated": True}
        return self.enricher(product.title_zh, product.description_zh)

    def _add_skus(self, normalised: dict) -> dict:
        """
        The product with its real sizes, or exactly the product it was given.

        Off by default: without a client this returns the input object itself,
        so a run configured the way every run before today was configured
        behaves identically rather than quietly changing what it publishes.

        A product that already carries a size table is left alone. Only the
        LinkPlus shape - one variant, no sizes - has anything to gain, and
        overwriting a detail-API table with this thinner one would lose the
        per-SKU prices that table has and this one does not.
        """
        if self.sku_client is None:
            return normalised
        variants = normalised.get("variants") or []
        if any(variant.get("sizes") for variant in variants):
            return normalised
        return skus.enrich(self.sku_client, normalised)

    def _terms(self, product: rules.Product) -> dict:
        labels = []
        for variant in product.variants:
            for key in ("color", "size"):
                value = variant.attributes.get(key, "")
                if value and value not in labels:
                    labels.append(value)
        if not labels or not self.translate:
            return {}
        return self.term_translator(labels)

    def run_offer(self, offer_id: str) -> OfferOutcome:
        """Fetch one offer by id, then price it. Only the AOP channel can do this."""
        if self.budget is not None and not self.budget.can_spend(self.points_per_offer):
            return OfferOutcome(offer_id=offer_id, product=None, results=[],
                                error="daily point budget exhausted")

        try:
            normalised = self.source.get_product(offer_id)
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            return OfferOutcome(offer_id=offer_id, product=None, results=[], error=str(exc))
        return self.run_product(normalised)

    def run_product(self, normalised: dict) -> OfferOutcome:
        """
        Price and publish a product we already hold.

        Discovery hands over the whole product, not an id. Asking the source to
        fetch it back would be pointless on the AOP channel and impossible on
        this one - there is no lookup, and a product that came out of last
        night's surplus is not in this process's memory at all. That was not
        theoretical: the second nightly run skipped all twelve of its products
        with "was not returned by a LinkPlus search" until this split existed.
        """
        offer_id = normalised["offer_id"]
        if self.budget is not None and not self.budget.can_spend(self.points_per_offer):
            return OfferOutcome(offer_id=offer_id, product=None, results=[],
                                error="daily point budget exhausted")

        spent = 0
        if self.budget is not None:
            # spend() answers with the points LEFT, not the points taken. The
            # audit column wants the cost of this offer, so it is recorded from
            # what was asked for.
            self.budget.spend(self.points_per_offer, note=f"offer {offer_id}")
            spent = self.points_per_offer

        product = to_rules_product(normalised)

        # The banned-category and mains-voltage filters live in the engine and
        # run before anything is translated or searched, so a product that can
        # never be published costs nothing beyond the one read that found it.
        # The category tree is consulted first, because it can reject a product
        # from its department alone - no title, no translation, no search.
        # "unknown" never rejects: most leaf ids sit below the depth we walked,
        # and refusing everything we have not walked would refuse the catalogue.
        category_state = (self.categories.state_of(normalised.get("category_id"))
                          if self.categories is not None else "unknown")

        if category_state in ("blocked", "review"):
            row = self.categories.by_id.get(str(normalised.get("category_id")))
            arabic = ("فئة ممنوعة" if category_state == "blocked"
                      else "فئة موقوفة للمراجعة")
            results = [
                self.engine.reject(product, variant, "banned_category",
                                   f"{arabic} - {row['name_ar'] if row else ''} "
                                   f"({normalised.get('category_id')})")
                for variant in product.variants
            ]
            self._audit(results, spent)
            return OfferOutcome(offer_id=offer_id, product=None, results=results,
                                points_spent=spent)

        # Sizes and colours, fetched only now: a product the category tree has
        # already refused must not cost a call, which is the same reason the
        # translation and the price search sit below this line too. It happens
        # before the banned-term filter on purpose - a variant named in Chinese
        # is text the filter should see, and until this line existed there were
        # no variant names to see.
        normalised = self._add_skus(normalised)
        if normalised.get("sku_source") == skus.SKU_APPLIED:
            product = to_rules_product(normalised)

        rejected_early = rules.find_banned_term(product) or (
            rules.is_electrical(product) and not rules.has_accepted_mains_spec(product))
        if rejected_early:
            results = self.engine.evaluate(product, {})
            self._audit(results, spent)
            return OfferOutcome(offer_id=offer_id, product=None, results=results,
                                points_spent=spent)

        enriched = self._enrich(product)

        # Cache first, then the meter, then the search - see _compare below.
        hits, searches, from_cache, compared = self._compare(
            product, enriched.get("name_en", ""),
            translated=not enriched.get("_untranslated"))

        results = self.engine.evaluate(product, hits)
        if not compared:
            _restate_uncompared(results)
        # After _restate_uncompared, so "nobody searched" still wins as the
        # explanation: an assumed weight only matters once a search happened.
        _restate_assumed_weight(results, normalised)
        self._audit(results, spent)

        variants = to_kdx_variants(results, self._terms(product))
        if not variants:
            return OfferOutcome(offer_id=offer_id, product=None, results=results,
                                points_spent=spent, compared=compared,
                                searches_spent=searches, from_cache=from_cache)

        main_category, sub_category = (
            self.categories.resolve(normalised.get("category_id"))
            if self.categories is not None else (None, None))

        payload = mapping.to_kdx_product(
            offer_id=product.offer_id,
            main_category=main_category,
            sub_category=sub_category,
            name_ar=enriched.get("name_ar", ""),
            name_en=enriched.get("name_en", ""),
            name_original=product.title_zh,
            weight_kg=min(variant.weight_kg for variant in product.variants),
            images=normalised.get("images", []),
            variants=variants,
            description_ar=enriched.get("description_ar", ""),
            description_en=enriched.get("description_en", ""),
        )

        # The photographs are checked here, on the way out, because his shop
        # downloads its own copy at import time and his endpoint has no update
        # path: a dead URL now is a product that stays pictureless for good.
        report = None
        if self.photos is not None:
            report = photos.prune(payload, self.photos)
            if not payload["images"]:
                return OfferOutcome(
                    offer_id=offer_id, product=None, results=results,
                    points_spent=spent, compared=compared,
                    searches_spent=searches, from_cache=from_cache,
                    photos=report,
                    error=f"no reachable photograph ({report['had']} URL(s) "
                          f"offered, none answered with an image)")

            # Then the clean photographs first. The client asked on 30 August
            # about Chinese writing printed inside the picture; it cannot be
            # removed, but it can be ranked behind the plain product shots.
            # Skipped when there is nothing to choose between and no threshold
            # set, because reading one photograph costs about a second and this
            # channel gives only one photograph per offer today.
            if len(payload["images"]) > 1 or imagetext.MAX_TEXT_PERCENT > 0:
                ranked = imagetext.order_gallery(payload["images"], self.photos)
                if ranked["images"]:
                    payload["images"] = ranked["images"]
                    report["text_scores"] = ranked["scores"]
                    report["text_dropped"] = ranked["dropped"]

                # And if even the best photograph is an advertising poster, the
                # product is held rather than published. Ranking cannot help a
                # product that has only one photograph, which is every product
                # this channel returns; the only choice left is whether a poster
                # is worth a listing. Off unless the client sets a threshold.
                worst = imagetext.poster_only(report.get("text_scores") or {})
                if worst is not None:
                    return OfferOutcome(
                        offer_id=offer_id, product=None, results=results,
                        points_spent=spent, compared=compared,
                        searches_spent=searches, from_cache=from_cache,
                        photos=report,
                        error=f"every photograph is an advertising poster "
                              f"(cleanest one is {worst}% Chinese text, limit "
                              f"{imagetext.MAX_TEXT_PERCENT}%)")

        response = None
        if self.kdx is not None and not self.dry_run:
            response = _one_response(self.kdx.push([payload]))
            trouble = _publish_trouble(response)
            if trouble:
                return OfferOutcome(
                    offer_id=offer_id, product=payload, results=results,
                    points_spent=spent, compared=compared,
                    searches_spent=searches, from_cache=from_cache,
                    photos=report, kdx_response=response, error=trouble)

        return OfferOutcome(offer_id=offer_id, product=payload, results=results,
                            points_spent=spent, compared=compared,
                            searches_spent=searches, from_cache=from_cache,
                            photos=report, kdx_response=response)

    def _compare(self, product: rules.Product, title_en: str, translated: bool):
        """
        Return (hits, searches_spent, from_cache, compared).

        `compared` is False whenever no search stood behind the answer, and it
        exists so the audit can tell apart two products that both ended up on
        margin pricing: one that was searched and found no rival, and one that
        was never searched at all. They carry different prices for different
        reasons and must not look alike.

        The comparison platforms are searched with the English name, so an
        untranslated product cannot be compared: every title check would score
        zero against a Chinese title. That search is skipped openly rather than
        run and guaranteed to find nothing.
        """
        if not translated:
            return {}, 0, False, False

        if self.cache is not None:
            cached = self.cache.get(product.offer_id)
            if cached is not None:
                # Answered already, under the thresholds still in force, and not
                # yet stale. This is where the monthly bill is actually saved.
                return cached, 0, True, True

        # The meter is checked before the call, not after: an exception thrown
        # halfway through a product would leave the run without an answer AND
        # having spent the search.
        need = 1 if self.shopping is None else 2
        if self.meter is not None and not self.meter.can_spend(need):
            return {}, 0, False, False

        before = self._calls()
        hits = compare.hits_for_product(self.provider, product, title_en,
                                        shopping=self.shopping)
        searches = self._calls() - before
        if self.cache is not None:
            # The empty answer is stored too. Most products find no qualifying
            # rival, and not storing that is not storing the common case.
            self.cache.put(product.offer_id, hits, searches)
        return hits, searches, False, True

    def _calls(self) -> int:
        """Searches made so far, when the providers are metered ones."""
        return (getattr(self.provider, "calls", 0) or 0) + (getattr(self.shopping, "calls", 0) or 0)

    def _audit(self, results: list, points: int) -> None:
        if self.audit_log is None:
            return
        for index, result in enumerate(results):
            # The read cost is charged once, to the first row of the offer, so
            # the points column across the file adds up to the points actually
            # spent rather than to the number of variants.
            self.audit_log.write(result.audit, points_spent=points if index == 0 else 0)

    def run(self, offer_ids) -> list:
        outcomes = []
        for offer_id in offer_ids:
            outcome = self.run_offer(offer_id)
            outcomes.append(outcome)
            if outcome.error == "daily point budget exhausted":
                break
        return outcomes

    def run_products(self, products) -> list:
        """
        The nightly path: products discovery already has in its hands.

        One product must never cost the night. On 30 August a single SerpApi
        request timed out at product ~150 of 300 and the exception travelled all
        the way out of this loop: the run died, nothing was published, and the
        three hours of gateway calls before it were spent for nothing. At
        midnight, with nobody awake, that is the whole night gone because one
        HTTP response was slow.

        So an unexpected failure is recorded against the offer it belongs to and
        the next product is tried. Two things still stop the night, because
        continuing past them is pointless rather than resilient: the daily point
        budget, and the monthly SerpApi allowance.
        """
        import searches as searches_module

        outcomes = []
        for normalised in products:
            offer_id = str(normalised.get("offer_id") or "")
            try:
                outcome = self.run_product(normalised)
            except searches_module.OutOfSearches as exc:
                outcomes.append(OfferOutcome(offer_id=offer_id, product=None,
                                             results=[], error=str(exc)))
                break
            except Exception as exc:                     # noqa: BLE001
                outcomes.append(OfferOutcome(
                    offer_id=offer_id, product=None, results=[],
                    error=f"{type(exc).__name__}: {str(exc)[:200]}"))
                continue
            outcomes.append(outcome)
            if outcome.error == "daily point budget exhausted":
                break
        return outcomes


def build(*, dry_run: bool = True, translate: bool | None = None, cny_to_sar=None):
    """Assemble a pipeline from the environment, for the scheduler and the CLI."""
    import aop_client
    import audit as audit_module
    import budget as budget_module
    import source as source_module

    if translate is None:
        translate = bool(os.environ.get("OPENAI_API_KEY"))

    if cny_to_sar is None:
        import fx
        cny_to_sar = fx.rate_for_today()

    kdx = None
    if not dry_run and os.environ.get("KDX_API_TOKEN"):
        import kdx_client
        kdx = kdx_client.KdxClient(os.environ.get("KDX_BASE_URL", "https://kdx-sa.com"),
                                   os.environ["KDX_API_TOKEN"])

    import catalog
    categories = catalog.CategoryIndex.load(
        os.environ.get("KDX_CATEGORIES",
                       os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "data", "categories.json")))

    import searches as searches_module
    meter = searches_module.build_meter()
    provider = compare.build_provider()
    shopping = compare.build_shopping_provider()
    if meter is not None:
        # Wrapped, so the month's count is the calls actually made rather than a
        # number kept alongside them.
        provider = searches_module.Metered(provider, meter, note="lens")
        shopping = None if shopping is None else searches_module.Metered(
            shopping, meter, note="shopping")

    # linkplus is the channel we actually hold, and it needs a signed client.
    # build_source raises rather than silently falling back if one is missing,
    # so the client is built whenever credentials exist and left as None
    # otherwise - which keeps fixture and dry runs working without keys.
    client = None
    if os.environ.get(aop_client.ENV_APP_KEY) and os.environ.get(aop_client.ENV_TOKEN):
        client = aop_client.build_pool_from_env()

    # The built tree stops one level below the departments, which was enough
    # while the catalogue was one department. Across the whole market most leaf
    # ids fall outside it, and an unresolved category is not merely a blank
    # department in the shop - it also answers "unknown" to the ban filter,
    # which then cannot reject anything. So resolve the rest on demand, and do
    # it here rather than where the tree is loaded: the client does not exist
    # until this point, and reaching for it earlier is a NameError on every
    # real run.
    import category_live
    if client is not None:
        def _translate_category(name_zh: str) -> dict:
            if not translate:
                return {}
            import enrich
            return enrich.translate_categories([name_zh]).get(name_zh, {})

        categories = category_live.LiveIndex(categories, client=client,
                                             translate=_translate_category)

    return Pipeline(
        source=source_module.build_source(client),
        categories=categories,
        provider=provider,
        shopping=shopping,
        cache=searches_module.build_cache(),
        meter=meter,
        engine=rules.Engine(cny_to_sar=cny_to_sar),
        budget=budget_module.PointBudget(),
        audit_log=audit_module.AuditLog(),
        kdx=kdx,
        photo_checker=photos.PhotoChecker() if photos.ENABLED else None,
        translate=translate,
        dry_run=dry_run,
    )

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
import mapping
import rules


@dataclass
class OfferOutcome:
    offer_id: str
    product: dict | None          # the KDX payload, or None if nothing survived
    results: list                 # every PricingResult, published or not
    points_spent: int = 0
    error: str = ""
    compared: bool = True         # False when the image search was not run at all

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
                stock=int(size.get("stock", 0) or 0),
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


class Pipeline:
    def __init__(self, *, source, provider, engine, budget=None, audit_log=None,
                 kdx=None, translate: bool = True, dry_run: bool = True,
                 points_per_offer: int = 1, enricher=None, term_translator=None,
                 categories=None):
        self.source = source
        self.provider = provider
        self.engine = engine
        self.budget = budget
        self.audit_log = audit_log
        self.kdx = kdx
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

    def _enrich(self, product: rules.Product) -> dict:
        if not self.translate:
            # Fixture and dry runs must not need an API key. The Chinese title
            # is carried through untouched and clearly marked, so nothing that
            # reaches KDX from a dry run can be mistaken for finished copy.
            return {"name_en": product.title_zh, "name_ar": product.title_zh,
                    "description_ar": "", "description_en": "", "_untranslated": True}
        return self.enricher(product.title_zh, product.description_zh)

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
        if self.budget is not None and not self.budget.can_spend(self.points_per_offer):
            return OfferOutcome(offer_id=offer_id, product=None, results=[],
                                error="daily point budget exhausted")

        try:
            normalised = self.source.get_product(offer_id)
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            return OfferOutcome(offer_id=offer_id, product=None, results=[], error=str(exc))

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

        rejected_early = rules.find_banned_term(product) or (
            rules.is_electrical(product) and not rules.has_accepted_mains_spec(product))
        if rejected_early:
            results = self.engine.evaluate(product, {})
            self._audit(results, spent)
            return OfferOutcome(offer_id=offer_id, product=None, results=results,
                                points_spent=spent)

        enriched = self._enrich(product)

        # The comparison platforms are searched with the English name, so an
        # untranslated product cannot be compared: every title check would score
        # zero against a Chinese title and every product would quietly fall
        # through to margin pricing. Quietly is the problem - a product priced
        # by margin because the translation was skipped looks identical to one
        # priced by margin because it genuinely has no rival, and the two carry
        # different prices. So the search is skipped openly and the outcome says
        # it was, rather than running a search that is guaranteed to find
        # nothing.
        compared = not enriched.get("_untranslated")
        hits = (compare.hits_for_product(self.provider, product, enriched.get("name_en", ""))
                if compared else {})

        results = self.engine.evaluate(product, hits)
        self._audit(results, spent)

        variants = to_kdx_variants(results, self._terms(product))
        if not variants:
            return OfferOutcome(offer_id=offer_id, product=None, results=results,
                                points_spent=spent, compared=compared)

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

        if self.kdx is not None and not self.dry_run:
            self.kdx.push([payload])

        return OfferOutcome(offer_id=offer_id, product=payload, results=results,
                            points_spent=spent, compared=compared)

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


def build(*, dry_run: bool = True, translate: bool | None = None, cny_to_sar=None):
    """Assemble a pipeline from the environment, for the scheduler and the CLI."""
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

    return Pipeline(
        source=source_module.build_source(),
        categories=categories,
        provider=compare.build_provider(),
        engine=rules.Engine(cny_to_sar=cny_to_sar),
        budget=budget_module.PointBudget(),
        audit_log=audit_module.AuditLog(),
        kdx=kdx,
        translate=translate,
        dry_run=dry_run,
    )

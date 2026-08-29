"""
Where the day's 300 products come from.

The channel we hold has no lookup. There is no way to say "give me offer 123",
so `run_pipeline.py 104843239419` - the shape everything was built around while
we still expected alibaba.product.get - cannot be the way the night's run
starts. The only entry point is a photograph: give the gateway a picture, it
returns 1688 offers that look like it.

That raises the question this module exists to answer: where do the photographs
come from, every day, for ever? Measured on 29 August against the live gateway:

  * one photograph is worth about 75 offers, not 20. The page size caps at 20,
    but pages 1-4 each returned twenty NEW offers and page 5 came back empty.
  * every offer the search returns carries its own photograph, hosted on
    Alibaba's own CDN - so it is certainly fetchable from China, and can itself
    be searched. Starting from ONE seed and expanding what came back, three
    rounds and seven searches produced 292 distinct offers: about 42 new offers
    per search.
  * which photograph you expand matters enormously. Expanding an offer that sat
    at the top of the results - the one most like the picture just searched -
    returned 0 and 1 new offers. Expanding offers from further down returned 41,
    45 and 77. So the frontier is taken from the TAIL, not the head.

Put together: 300 products a day is roughly seven or eight searches a day, and
the client supplies a small set of starting photographs once rather than a fresh
batch every morning. He can add seeds whenever he wants to steer the catalogue
towards a category; he does not have to.

Two things this module refuses to do:

  * hand the same offer to the pipeline twice. The ledger is on disk, keyed by
    offer id, because the run is a cron job and tomorrow must not republish
    today.
  * spend the day's quota on a product the rules were always going to reject.
    The category gate and the banned-term check are the pipeline's own, imported
    rather than copied, and applied here only to decide whether an offer is
    worth queueing.

Configuration:

    KDX_SEEDS          path to the seed list (one image URL per line, # comments)
    KDX_DISCOVERY_STATE  path to the ledger        (default /opt/kdx/discovered.json)
    KDX_DISCOVERY_PAGES  pages per search          (default 4, measured)
    KDX_DISCOVERY_MAX_SEARCHES  hard ceiling on gateway searches in one run
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal

import catalog
import paths
import rules
import source as source_module


def ledger_path() -> str:
    return paths.state_path("discovered.json", "KDX_DISCOVERY_STATE")


# Measured, not guessed: page 5 was empty for both seeds tried, and the gateway
# reported a total of 71-75 that drifted between calls because the index is live.
PAGES = int(os.environ.get("KDX_DISCOVERY_PAGES", "4"))

# A run that finds nothing must not sit there calling the gateway all night.
# Counted in PAGES, not photographs, because a page is what costs a call. A
# full 300-product night needs roughly fourteen photographs, so about 56 pages;
# 200 leaves room for a night where the offers overlap heavily without letting a
# runaway walk call the gateway indefinitely. The first run at the old value of
# 40 stopped at 223 products, which is exactly the failure this number causes.
MAX_SEARCHES = int(os.environ.get("KDX_DISCOVERY_MAX_SEARCHES", "200"))


class DiscoveryError(RuntimeError):
    pass


def read_seeds(path: str = "") -> list:
    """
    The client's starting photographs, one URL per line.

    The URL has to be fetchable by Alibaba, from China. That is not a detail: on
    29 August the Uniqlo URL that produced August's fixture had become a 404 and
    the gateway answered SYSTEM_ERROR, "handle image error with url ...". Loud,
    at least - a dead seed does not quietly return an empty result.
    """
    path = path or os.environ.get("KDX_SEEDS", "")
    if not path:
        raise DiscoveryError(
            "no seed photographs: set KDX_SEEDS to a file of image URLs. This "
            "channel searches by picture and cannot fetch an offer by id, so "
            "without at least one picture there is nothing to start from.")
    if not os.path.exists(path):
        raise DiscoveryError(f"seed file not found: {path}")
    seeds = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                seeds.append(line)
    if not seeds:
        raise DiscoveryError(f"seed file {path} has no URLs in it")
    return seeds


def _rehydrate(product: dict) -> dict:
    """
    A product read back from the ledger, with its prices Decimal again.

    Written out they became strings. Handing a string to the pricing rules would
    raise; handing a float would round differently from every price that never
    went through the ledger, which is the worse of the two because it would not
    raise at all.
    """
    product = dict(product)
    variants = []
    for variant in product.get("variants", []):
        variant = dict(variant)
        if variant.get("price") is not None:
            variant["price"] = Decimal(str(variant["price"]))
        variant["sizes"] = [
            {**size, "price": Decimal(str(size["price"]))} if size.get("price") is not None
            else size
            for size in variant.get("sizes", [])
        ]
        variants.append(variant)
    product["variants"] = variants
    return product


class Ledger:
    """
    What has already been discovered, and which photographs have been expanded.

    Both halves matter. Without the offer half the same product is republished
    every night. Without the photograph half the walk keeps re-expanding the
    same picture, which the measurements showed returns almost nothing new.
    """

    def __init__(self, path: str = "", clock=time.time):
        self.path = path or ledger_path()
        self.clock = clock
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            state = {}
        state.setdefault("offers", {})     # offer_id -> {"day": ..., "at": ...}
        state.setdefault("expanded", {})   # image url -> {"new": n, "at": ...}
        state.setdefault("pending", {})    # offer_id -> the normalised product
        return state

    def save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            # default=str because a price is a Decimal, which JSON has no
            # opinion about; take_pending puts it back as a Decimal on the way
            # out rather than letting a float loose in the pricing rules.
            json.dump(self.state, handle, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, self.path)         # a killed cron job must not truncate it

    def knows_offer(self, offer_id) -> bool:
        return (str(offer_id) in self.state["offers"]
                or str(offer_id) in self.state["pending"])

    # -- the surplus ----------------------------------------------------------
    #
    # One photograph yields about 75 offers and a night's quota is 300, so the
    # last search of the night overshoots. Those offers have already been paid
    # for with a gateway call, and their photograph is now marked as expanded -
    # so throwing them away would lose them for good. They wait here instead,
    # and the next run takes them before it searches anything.

    def hold(self, product: dict) -> None:
        self.state["pending"][str(product["offer_id"])] = product

    def take_pending(self, limit: int) -> list:
        taken = []
        for offer_id in list(self.state["pending"])[:max(0, limit)]:
            taken.append(_rehydrate(self.state["pending"].pop(offer_id)))
        return taken

    def add_offer(self, offer_id, day: str = "") -> None:
        self.state["offers"][str(offer_id)] = {"day": day, "at": int(self.clock())}

    def expanded(self, image_url: str) -> bool:
        return image_url in self.state["expanded"]

    def mark_expanded(self, image_url: str, fresh: int) -> None:
        self.state["expanded"][image_url] = {"new": fresh, "at": int(self.clock())}

    def summary(self) -> dict:
        yields = [row.get("new", 0) for row in self.state["expanded"].values()]
        return {
            "offers_known": len(self.state["offers"]),
            "photographs_expanded": len(yields),
            "new_per_search": round(sum(yields) / len(yields), 1) if yields else 0.0,
            # Offers already paid for and not yet used. A number that only grows
            # means the walk is running ahead of the quota, which is fine; a
            # number stuck at zero means every night starts by searching.
            "waiting": len(self.state["pending"]),
        }


class Discovery:
    """
    Walks the similarity graph until the day's quota is filled.

    `run` returns normalised products, in the same shape the pipeline already
    consumes, and every offer id it returns is one the source has cached - so
    the pipeline can read them back without spending a second gateway call.
    """

    def __init__(self, source, ledger: Ledger, *, categories=None,
                 pages: int = PAGES, max_searches: int = MAX_SEARCHES,
                 day: str = ""):
        self.source = source
        self.ledger = ledger
        self.categories = categories
        self.pages = pages
        self.max_searches = max_searches
        self.day = day
        self.searches = 0
        self.rejected_early = 0
        self.from_surplus = 0
        self.notes: list = []

    # -- the two filters, borrowed from the pipeline rather than rewritten -----

    def _worth_queueing(self, product: dict) -> bool:
        """
        Would this product be thrown out on sight? Then it must not eat quota.

        Deliberately only the checks that need nothing but the row we already
        have. Everything else - price, weight, comparison, the undercut - is the
        engine's job and stays there.
        """
        state = (self.categories.state_of(product.get("category_id"))
                 if self.categories is not None else "unknown")
        if state in (catalog.BLOCKED, catalog.REVIEW):
            return False
        # find_banned_term wants a rules.Product; the title is the only part of
        # it these terms are ever found in on this channel, which carries no
        # description and no attributes.
        try:
            import pipeline as pipeline_module
            if rules.find_banned_term(pipeline_module.to_rules_product(product)):
                return False
        except Exception:                    # noqa: BLE001
            # A product we cannot even shape is not a product. Let the pipeline
            # report why, rather than dropping it silently here.
            return True
        return True

    # -- the walk -------------------------------------------------------------

    def _search_all_pages(self, image_url: str) -> list:
        """
        Every page for one photograph, with the pages deduplicated against each
        other. The gateway repeats itself across pages - a 300-product run
        harvested 223 products but only put 204 in the ledger until this was
        fixed, which is 19 products that would have been published twice.
        """
        rows: list = []
        seen_here: set = set()
        for page in range(1, self.pages + 1):
            if self.searches >= self.max_searches:
                self.notes.append(f"stopped at the {self.max_searches}-search ceiling")
                break
            self.searches += 1
            try:
                batch = self.source.search_by_image(image_url, page=page)
            except source_module.SourceError as exc:
                self.notes.append(f"seed failed ({str(exc)[:120]}): {image_url}")
                break
            if not batch:
                break                        # measured: page 5 comes back empty
            for product in batch:
                if product["offer_id"] not in seen_here:
                    seen_here.add(product["offer_id"])
                    rows.append(product)
        return rows

    @staticmethod
    def _frontier_from(found: list, want: int) -> list:
        """
        Which of the offers just found should be searched next.

        From the tail. Measured: expanding the top result - the offer most like
        the picture just searched - returned 0 and 1 new offers, while offers
        from further down returned 41, 45 and 77. The results are ordered by
        similarity, so the tail is the part of the graph we have not reached.
        """
        pictures: list = []
        for product in reversed(found):
            images = product.get("images") or []
            if images and images[0] not in pictures:
                pictures.append(images[0])
            if len(pictures) >= want:
                break
        return pictures

    def run(self, seeds: list, quota: int) -> list:
        if quota <= 0:
            return []
        queue = list(seeds)
        harvested: list = []

        # Last night's surplus first. It cost gateway calls that have already
        # been made, so spending a fresh one before using it would be paying
        # twice for the same offers.
        for product in self.ledger.take_pending(quota):
            self.ledger.add_offer(product["offer_id"], self.day)
            if self._worth_queueing(product):
                harvested.append(product)
            else:
                self.rejected_early += 1
        if harvested:
            self.from_surplus = len(harvested)

        while queue and len(harvested) < quota and self.searches < self.max_searches:
            picture = queue.pop(0)
            if self.ledger.expanded(picture):
                continue

            rows = self._search_all_pages(picture)
            fresh = [row for row in rows if not self.ledger.knows_offer(row["offer_id"])]
            self.ledger.mark_expanded(picture, len(fresh))

            for product in fresh:
                if len(harvested) >= quota:
                    # Paid for, and this photograph will never be searched
                    # again. Keep it for tomorrow rather than losing it.
                    self.ledger.hold(product)
                    continue
                self.ledger.add_offer(product["offer_id"], self.day)
                if not self._worth_queueing(product):
                    self.rejected_early += 1
                    continue
                harvested.append(product)

            # Grow the frontier from what this search found, even if the quota
            # is now full: tomorrow starts from a warm queue instead of the same
            # seeds, which the ledger would refuse to expand a second time.
            for candidate in self._frontier_from(fresh, want=3):
                if not self.ledger.expanded(candidate) and candidate not in queue:
                    queue.append(candidate)

        self.ledger.save()
        return harvested


def build(source, *, categories=None, day: str = "") -> Discovery:
    return Discovery(source, Ledger(), categories=categories, day=day)

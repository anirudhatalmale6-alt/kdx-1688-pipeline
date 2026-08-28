"""
Stage: find the same product on the comparison platforms, by image.

Feeds rules.Engine with CompetitorHit objects. The client's rule is that a hit
only counts as the same product at 95% match or better, and that the undercut is
applied to the cheapest such hit.

Two decisions in here are worth reading before trusting the output.

1. A match needs the picture AND the words to agree.

   Image search returns things that look alike, which is not the same as things
   that are alike: the same photo appears on a 20 litre and a 30 litre boiler.
   So the score is min(visual, textual) - a conjunctive rule, where reaching 95
   requires both signals to reach 95 independently. A high-ranked visual match
   with a contradicting title scores low and is discarded.

   The consequence is deliberate and should be expected: most products will find
   no qualifying match and will be priced by margin instead of by undercut. That
   is the safe direction to fail. A loose match would undercut the wrong product
   and set a price against something we are not actually selling.

2. Competitor prices are used only when they are already in SAR.

   Every one of the five platforms shows SAR when the search region is Saudi, so
   the request pins the region and a hit in any other currency is dropped rather
   than converted. A stale conversion rate applied to a rival's price becomes our
   own retail price, and a wrong retail price is worse than no comparison.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation

from rules import COMPARISON_PLATFORMS, CompetitorHit, MATCH_THRESHOLD, money

# The platforms the client named, and the domains they actually appear under in
# search results. Anything outside this list is not a comparison platform and is
# discarded no matter how well it matches.
PLATFORM_DOMAINS = {
    "Temu": ("temu.com",),
    "SHEIN": ("shein.com", "shein.sa", "us.shein.com", "ar.shein.com"),
    "AliExpress": ("aliexpress.com", "aliexpress.us", "ar.aliexpress.com"),
    "Amazon": ("amazon.sa", "amazon.ae", "amazon.com"),
    "Noon": ("noon.com",),
}

# Only the top visual matches can qualify. Rank is the provider's own ordering
# by visual similarity, so this is a decay rather than a hard cut: rank 1 scores
# 100, rank 3 scores 96, rank 4 falls under the 95 threshold on its own.
VISUAL_DECAY_PER_RANK = Decimal("2")

# Marketing filler that inflates title overlap without saying anything about
# which product this is.
STOPWORDS = {
    "new", "hot", "sale", "free", "shipping", "fashion", "style", "quality",
    "high", "best", "top", "for", "with", "and", "the", "a", "of", "in", "pcs",
    "set", "pack", "women", "men", "unisex", "2023", "2024", "2025",
    "جديد", "عرض", "شحن", "مجاني", "جودة", "عالية", "افضل", "أفضل",
}

# A number with a unit is a specification, not a word: 30l is not 20l, 3000w is
# not 2000w. Two titles that disagree on one of these are not the same product,
# however similar the photograph.
SPEC_TOKEN = re.compile(r"^\d+(?:\.\d+)?(?:l|ml|w|kw|kg|g|v|hz|cm|mm|m|inch|gb|tb)$")

PRICE_PATTERN = re.compile(r"(\d[\d,\s]*(?:[.,]\d{1,2})?)")
SAR_MARKERS = ("sar", "﷼", "ر.س", "ريال", "sr")


class CompareError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def tokens(text: str) -> set:
    """Normalise a title down to the words that identify the product."""
    lowered = re.sub(r"[^\w؀-ۿ.]+", " ", (text or "").lower())
    raw = [token.strip(".") for token in lowered.split() if token.strip(".")]
    return {token for token in raw if token not in STOPWORDS and len(token) > 1}


def spec_tokens(words: set) -> set:
    return {word for word in words if SPEC_TOKEN.match(word)}


def text_score(ours: str, theirs: str) -> Decimal:
    """
    How much of the shorter title the two share, 0-100.

    Coverage of the shorter title rather than Jaccard overlap, because
    competitor titles are padded with keywords: a genuine match is one where
    nearly everything the shorter title says also appears in the longer one.

    A disagreement between specification tokens vetoes the match outright.
    """
    mine, yours = tokens(ours), tokens(theirs)
    if not mine or not yours:
        return Decimal("0")

    my_specs, your_specs = spec_tokens(mine), spec_tokens(yours)
    if my_specs and your_specs and not (my_specs & your_specs):
        # Both sides state a specification and they do not share one. Same
        # picture, different product.
        return Decimal("0")

    shared = len(mine & yours)
    return money(Decimal(shared) * 100 / Decimal(min(len(mine), len(yours))))


def visual_score(rank: int) -> Decimal:
    return max(Decimal("0"), Decimal("100") - VISUAL_DECAY_PER_RANK * Decimal(max(rank - 1, 0)))


def match_score(our_title: str, their_title: str, rank: int) -> Decimal:
    """The picture and the words both have to agree, so the weaker one wins."""
    return min(visual_score(rank), text_score(our_title, their_title))


# --------------------------------------------------------------------------
# Parsing a result
# --------------------------------------------------------------------------

def platform_of(link: str, source_name: str = "") -> str | None:
    haystack = f"{urllib.parse.urlparse(link or '').netloc} {source_name}".lower()
    for platform in COMPARISON_PLATFORMS:
        for domain in PLATFORM_DOMAINS.get(platform, ()):
            if domain in haystack:
                return platform
        if platform.lower() in haystack:
            return platform
    return None


def sar_price(raw) -> Decimal | None:
    """
    Read a price only if it is stated in SAR. Anything else returns None and the
    hit is dropped - see the note at the top about not converting rivals' prices.
    """
    if isinstance(raw, dict):
        currency = str(raw.get("currency", "")).lower()
        extracted = raw.get("extracted_value", raw.get("value"))
        text = str(raw.get("value", ""))
        if extracted is not None and (currency in ("sar", "sr")
                                      or any(m in text.lower() for m in SAR_MARKERS)):
            try:
                return money(Decimal(str(extracted)))
            except (InvalidOperation, ValueError):
                return None
        raw = text

    text = str(raw or "").strip()
    if not text or not any(marker in text.lower() for marker in SAR_MARKERS):
        return None
    found = PRICE_PATTERN.search(text.replace(" ", " "))
    if not found:
        return None
    number = found.group(1).replace(" ", "").replace(",", "")
    try:
        value = money(Decimal(number))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


def hits_from_results(results: list, our_title: str, variant_sku: str = "") -> list:
    """Turn one provider response into CompetitorHits, keeping only real matches."""
    hits = []
    for rank, result in enumerate(results or [], start=1):
        if not isinstance(result, dict):
            continue
        link = str(result.get("link") or result.get("url") or "")
        platform = platform_of(link, str(result.get("source") or result.get("seller") or ""))
        if platform is None:
            continue
        price = sar_price(result.get("price"))
        if price is None:
            continue
        score = match_score(our_title, str(result.get("title") or ""), rank)
        if score < MATCH_THRESHOLD:
            continue
        hits.append(CompetitorHit(platform=platform, price_sar=price, match_score=score,
                                  url=link, matched_variant=variant_sku))
    return hits


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

class LensProvider:
    """
    Image search through SerpApi's google_lens engine.

    Pinned to the Saudi region so the five platforms quote SAR; see the note at
    the top of the file about why a non-SAR hit is dropped rather than converted.
    """

    ENDPOINT = "https://serpapi.com/search.json"

    def __init__(self, api_key: str = "", timeout: int = 40):
        self.api_key = api_key or os.environ.get("KDX_SERPAPI_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            raise CompareError("KDX_SERPAPI_KEY is not set")

    def search_by_image(self, image_url: str) -> list:
        query = urllib.parse.urlencode({
            "engine": "google_lens",
            "url": image_url,
            "country": "sa",
            "hl": "ar",
            "api_key": self.api_key,
        })
        with urllib.request.urlopen(f"{self.ENDPOINT}?{query}", timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("error"):
            raise CompareError(str(payload["error"]))
        return payload.get("visual_matches") or []


class FixtureProvider:
    """Recorded search responses, so the scoring can be proved without a key."""

    def __init__(self, directory: str | None = None):
        self.directory = directory or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "lens")

    def search_by_image(self, image_url: str) -> list:
        name = re.sub(r"[^a-z0-9]+", "_", image_url.lower()).strip("_")
        path = os.path.join(self.directory, f"{name}.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload.get("visual_matches") or []


def build_provider():
    if os.environ.get("KDX_COMPARE", "lens").strip().lower() == "fixture":
        return FixtureProvider()
    return LensProvider()


# --------------------------------------------------------------------------
# The stage
# --------------------------------------------------------------------------

def hits_for_product(provider, product, title_en: str, max_images_per_variant: int = 1) -> dict:
    """
    Search each variant by its own photo and return {sku_id: [CompetitorHit]}.

    Per variant, not per product: the whole point of grouping by photo is that
    the black one is compared against black ones. A hit found from the black
    photo is tagged with that variant's sku so rules.best_match cannot apply it
    to another.

    Every image searched costs one provider call, so this is where the daily
    budget is actually spent - hence one image per variant by default.
    """
    hits: dict = {}
    searched: dict = {}
    for variant in getattr(product, "variants", []) or []:
        images = (variant.attributes or {}).get("images") or []
        if isinstance(images, str):
            images = [images]
        for image in images[:max_images_per_variant]:
            if image not in searched:
                searched[image] = provider.search_by_image(image)
            hits.setdefault(variant.sku_id, []).extend(
                hits_from_results(searched[image], title_en, variant.sku_id))
    return hits

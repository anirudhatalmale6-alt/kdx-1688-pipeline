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

# How closely the WORDS have to agree, separately from the picture.
#
# 60, on the client's decision of 29 August, and the number comes from a
# measurement rather than a preference: at his original 95 the comparison never
# fires at all. On a real Saudi shopping response for one boiler the
# best-scoring Amazon listing for the same product reached 75, because
# marketplace titles are not written like ours - "20L/30L/40L Commercial
# Catering Urn" against "Commercial Stainless Steel Electric Water Boiler 30L
# 3000W". At 60 that same product yields three genuine rival prices.
#
# It is a separate number from MATCH_THRESHOLD because the two guard different
# things. The picture establishes identity and stays at 95; the words are only
# here to catch the case where one photo sells two sizes, and the specification
# veto below does most of that work already. Lowering this does not loosen the
# picture. KDX_TEXT_MATCH_MIN puts it back to 95 without a code change.
DEFAULT_TEXT_THRESHOLD = "60"
TEXT_THRESHOLD = Decimal(os.environ.get("KDX_TEXT_MATCH_MIN", DEFAULT_TEXT_THRESHOLD))

# One image search per PRODUCT, or one per colour?
#
# Per colour is more precise - the black photo is compared against black ones -
# but it multiplies the bill by the number of colours, and the client's
# constraint is the monthly SerpApi allowance. "product" searches the main photo
# once and lets the result stand for every colour of that product; the hits it
# produces carry no variant tag, which is exactly how rules.best_match already
# treats a hit that was not matched to one particular colour.
#
# Set KDX_LENS_SCOPE=variant to buy back the precision when the allowance allows
# it. Nothing else in the pipeline changes.
LENS_SCOPE = os.environ.get("KDX_LENS_SCOPE", "product").strip().lower()

# When is the second (price) search allowed to happen?
#
#   no-price      only when not one identified rival quoted a price. The
#                 client's instruction of 29 August, and the cheaper rule.
#   any-unpriced  whenever any identified rival is missing its price. More
#                 accurate - it can still find a cheaper platform that the image
#                 search identified but did not price - and costs more searches.
SHOPPING_WHEN = os.environ.get("KDX_SHOPPING_WHEN", "no-price").strip().lower()

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

def slugify(text: str) -> str:
    """Filename for a recorded response, from the URL or title that produced it."""
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


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


def identity_matches(results: list, our_title: str) -> list:
    """
    The results that are the same product: right platform, picture and words
    agreeing at the threshold. Priced or not.

    Kept separate from pricing because measurement on the client's own SerpApi
    key showed the two are not the same question. Of 60 real visual matches for
    one product, 3 carried a price. Dropping the other 57 at this point would
    throw away the identity we just paid to establish.
    """
    found = []
    for rank, result in enumerate(results or [], start=1):
        if not isinstance(result, dict):
            continue
        link = str(result.get("link") or result.get("url") or "")
        platform = platform_of(link, str(result.get("source") or result.get("seller") or ""))
        if platform is None:
            continue
        # Two separate bars, which is the same conjunctive rule as before: at
        # the default both are 95, so "picture >= 95 and words >= 95" is exactly
        # "min(picture, words) >= 95". Splitting them lets the words bar be
        # lowered - the client's call - without loosening the picture at all.
        if text_score(our_title, str(result.get("title") or "")) < TEXT_THRESHOLD:
            continue
        score = visual_score(rank)
        if score < MATCH_THRESHOLD:
            continue
        found.append({"platform": platform, "score": score, "link": link,
                      "title": str(result.get("title") or ""),
                      "price": sar_price(result.get("price"))})
    return found


def hits_from_results(results: list, our_title: str, variant_sku: str = "") -> list:
    """Turn one provider response into CompetitorHits, keeping only priced matches."""
    return [CompetitorHit(platform=match["platform"], price_sar=match["price"],
                          match_score=match["score"], url=match["link"],
                          matched_variant=variant_sku)
            for match in identity_matches(results, our_title)
            if match["price"] is not None]


def prices_from_shopping(matches: list, rows: list, our_title: str,
                         variant_sku: str = "") -> list:
    """
    Put a price on matches the image search identified but did not price.

    A shopping row is only allowed to price a match when it is on a platform
    the PICTURE already matched and its own title agrees with ours. Both
    conditions, not either: without the first, a shopping row for some other
    shop would price our product; without the second, the cheapest unrelated
    listing on the right platform would.
    """
    unpriced = [match for match in matches if match["price"] is None]
    if not unpriced:
        return []
    identified = {match["platform"] for match in unpriced}
    by_platform = {match["platform"]: match for match in unpriced}

    hits = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        platform = platform_of(str(row.get("product_link") or row.get("link") or ""),
                               str(row.get("source") or ""))
        if platform not in identified:
            continue
        # Only the price STRING is read, because only it states the currency.
        # extracted_price is a bare number: trusting it would quietly turn a
        # 99 dollar rival into a 99 riyal one, which is the single mistake this
        # module exists to prevent.
        price = sar_price(row.get("price"))
        if price is None:
            continue
        words = text_score(our_title, str(row.get("title") or ""))
        if words < TEXT_THRESHOLD:
            continue
        # The score stays the picture's, which is what established identity.
        # The words were a gate, not a contribution.
        score = by_platform[platform]["score"]
        hits.append(CompetitorHit(platform=platform, price_sar=price, match_score=score,
                                  url=str(row.get("product_link") or row.get("link") or ""),
                                  matched_variant=variant_sku))
    return hits


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

def rows_or_empty(payload: dict, key: str) -> list:
    """
    "Nobody else sells this" is an answer. "Your key is invalid" is a fault.

    SerpApi reports both in the same `error` field, and treating the pair alike
    cost a whole night: the first product whose picture Google Lens did not
    recognise raised, and the run died at product 1 of 12 having published
    nothing. Yet a product no competitor carries is not an error at all - it is
    the client's own rule, the one that says price it on margin instead of
    undercutting. It has to arrive as an empty list.

    Anything else still raises. A run that quietly treats an exhausted plan or a
    rejected key as "no competitors found" would publish an entire night at full
    margin and look like it worked.
    """
    error = str(payload.get("error") or "")
    if error:
        if "returned any results" in error.lower():
            return []
        raise CompareError(error)
    return payload.get(key) or []


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
        return rows_or_empty(payload, "visual_matches")


class ShoppingProvider:
    """
    Prices, through SerpApi's google_shopping engine.

    Exists because of a measurement, not a preference. On the client's key,
    google_lens priced 3 of 60 results for one product; google_shopping priced
    40 of 40 for the same product, every one of them in SAR because the region
    is Saudi. Lens answers "who else sells this"; shopping answers "for how
    much". The comparison needs both.
    """

    ENDPOINT = "https://serpapi.com/search.json"

    def __init__(self, api_key: str = "", timeout: int = 40):
        self.api_key = api_key or os.environ.get("KDX_SERPAPI_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            raise CompareError("KDX_SERPAPI_KEY is not set")

    def search_by_title(self, title: str) -> list:
        query = urllib.parse.urlencode({
            "engine": "google_shopping",
            "q": title,
            "gl": "sa",
            "hl": "en",
            "location": "Saudi Arabia",
            "api_key": self.api_key,
        })
        with urllib.request.urlopen(f"{self.ENDPOINT}?{query}", timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return rows_or_empty(payload, "shopping_results")


class FixtureShoppingProvider:
    """Recorded shopping responses, so the join can be tested without a key."""

    def __init__(self, directory: str | None = None):
        self.directory = directory or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "shopping")

    def search_by_title(self, title: str) -> list:
        path = os.path.join(self.directory, f"{slugify(title)}.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload.get("shopping_results", payload) if isinstance(payload, dict) else payload


class FixtureProvider:
    """Recorded search responses, so the scoring can be proved without a key."""

    def __init__(self, directory: str | None = None):
        self.directory = directory or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples", "lens")

    def search_by_image(self, image_url: str) -> list:
        path = os.path.join(self.directory, f"{slugify(image_url)}.json")
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

def needs_price_search(matches: list, when: str = "") -> bool:
    """
    Is the second, paid, price search justified?

    No identified rival means no second search: a shopping row is only ever
    allowed to price a platform the picture already matched, so with nothing
    matched the call could not produce a single usable price. That single guard
    is the difference between one search per product and two.
    """
    when = (when or SHOPPING_WHEN).strip().lower()
    if not matches:
        return False
    if when == "any-unpriced":
        return any(match["price"] is None for match in matches)
    # "no-price": the image search already answered the price question for this
    # product, so do not pay to ask it again.
    return not any(match["price"] is not None for match in matches)


def main_image(product) -> str:
    """The one photo that stands for the whole product, for a product-scope search."""
    for image in getattr(product, "images", []) or []:
        if image:
            return image
    for variant in getattr(product, "variants", []) or []:
        images = (variant.attributes or {}).get("images") or []
        if isinstance(images, str):
            images = [images]
        for image in images:
            if image:
                return image
    return ""


def hits_for_product(provider, product, title_en: str, max_images_per_variant: int = 1,
                     shopping=None, scope: str = "") -> dict:
    """
    Search by photo and return {sku_id: [CompetitorHit]}.

    Two scopes, and which one is in force is a money decision, not a technical
    one - see LENS_SCOPE above.

      product  (default) one search on the main photo, the answer applies to
               every colour. Hits carry no variant tag, so rules.best_match
               accepts them against any variant.
      variant  one search per colour photo. A hit found from the black photo is
               tagged with that variant's sku so it cannot be applied to the
               white one. Precise, and N times the cost.

    The price search, when it runs at all, runs at most once per product in
    either scope.
    """
    scope = (scope or LENS_SCOPE).strip().lower()
    variants = list(getattr(product, "variants", []) or [])
    hits: dict = {}
    searched: dict = {}
    shopping_rows = None            # fetched at most once per product, not per variant

    def resolve(matches: list, sku: str) -> list:
        nonlocal shopping_rows
        found = [CompetitorHit(platform=m["platform"], price_sar=m["price"],
                               match_score=m["score"], url=m["link"],
                               matched_variant=sku)
                 for m in matches if m["price"] is not None]
        if shopping is not None and needs_price_search(matches):
            if shopping_rows is None:
                shopping_rows = shopping.search_by_title(title_en)
            found.extend(prices_from_shopping(matches, shopping_rows, title_en, sku))
        return found

    if scope == "product":
        image = main_image(product)
        if not image:
            return {}
        found = resolve(identity_matches(provider.search_by_image(image), title_en), "")
        # The same list under every sku: one search, one answer, and the empty
        # variant tag is what makes it legitimately usable against all of them.
        return {variant.sku_id: list(found) for variant in variants}

    for variant in variants:
        images = (variant.attributes or {}).get("images") or []
        if isinstance(images, str):
            images = [images]
        for image in images[:max_images_per_variant]:
            if image not in searched:
                searched[image] = provider.search_by_image(image)
            matches = identity_matches(searched[image], title_en)
            hits.setdefault(variant.sku_id, []).extend(resolve(matches, variant.sku_id))
    return hits


def build_shopping_provider():
    """
    KDX_SHOPPING: lens-priced only (off), recorded (fixture), or live (on).

    Defaults to on, because measurement showed image search alone prices about
    one match in twenty - which would leave nearly every product on margin
    pricing while looking like it had been compared.
    """
    choice = os.environ.get("KDX_SHOPPING", "on").strip().lower()
    if choice == "off":
        return None
    if choice == "fixture":
        return FixtureShoppingProvider()
    return ShoppingProvider()

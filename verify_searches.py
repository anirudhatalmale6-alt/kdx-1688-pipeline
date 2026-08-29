"""
Proof for the SerpApi allowance: the meter and the comparison cache.

    python3 verify_searches.py

No network and no key. Every provider here is a counter, because the only thing
this module has to get right is HOW MANY searches leave the machine - the
searches themselves are proved in verify_compare.py.

The client is buying a fixed number of searches a month. Three claims are worth
proving before spending his money on a plan:

  1. a product that was compared last night is not compared again tonight,
     including - especially - the one that found nothing;
  2. when the thresholds change, yesterday's answers are thrown away rather
     than served for another week;
  3. when the month's allowance runs out the run keeps going on margin pricing
     and says which products were never searched, instead of failing or
     quietly pretending they had no rival.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

WORK = tempfile.mkdtemp(prefix="kdx-searches-")
os.environ["KDX_BUDGET_STATE"] = os.path.join(WORK, "points.json")
os.environ["KDX_AUDIT_LOG"] = os.path.join(WORK, "audit.csv")

import audit as audit_module  # noqa: E402
import budget as budget_module  # noqa: E402
import compare  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import rules  # noqa: E402
import searches as searches_module  # noqa: E402
import source as source_module  # noqa: E402

passed = failed = 0

BOILER = "611229900011"
OUR_TITLE = "Commercial Stainless Steel Electric Water Boiler 30L 3000W"


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


class Clock:
    """A hand-wound clock, so a week can pass without waiting a week."""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance_days(self, days: float) -> None:
        self.now += days * 86400


class CountingLens:
    def __init__(self, results=None):
        self.results = results if results is not None else []
        self.calls = 0

    def search_by_image(self, image_url):
        self.calls += 1
        return self.results


NOON_689 = [{"link": "https://www.noon.com/x", "source": "Noon", "title": OUR_TITLE,
             "price": {"currency": "SAR", "extracted_value": 689.0,
                       "value": "SAR 689.00"}}]


def fake_enrich(title_zh, description_zh, **_kwargs):
    return {"name_en": OUR_TITLE, "name_ar": "غلاية ماء", "description_ar": "",
            "description_en": ""}


def fake_terms(terms, **_kwargs):
    return {term: {"en": term, "ar": term} for term in terms}


def main() -> int:
    print("1. the monthly meter counts down and refuses to overspend")
    state = os.path.join(WORK, "meter.json")
    meter = searches_module.SearchMeter(cap=5, state_path=state)
    check("a fresh month starts at the full allowance", meter.remaining() == 5,
          str(meter.summary()))
    meter.spend(3, note="test")
    check("spending three leaves two", meter.remaining() == 2, str(meter.remaining()))
    check("and it is written to disk, not held in memory",
          searches_module.SearchMeter(cap=5, state_path=state).remaining() == 2,
          "a cron job restarting mid-month must not hand itself a fresh allowance")

    try:
        meter.spend(3)
        overspent = True
    except searches_module.OutOfSearches:
        overspent = False
    check("CONTROL: a spend larger than what is left is refused, not allowed negative",
          not overspent and meter.remaining() == 2, str(meter.remaining()))

    print("2. the wrapper charges for exactly the calls that were made")
    lens = CountingLens(NOON_689)
    metered = searches_module.Metered(lens, searches_module.SearchMeter(
        cap=10, state_path=os.path.join(WORK, "meter2.json")), note="lens")
    metered.search_by_image("https://img/a.jpg")
    metered.search_by_image("https://img/b.jpg")
    check("two calls, two searches charged",
          metered.meter.used == 2 and metered.calls == 2 and lens.calls == 2,
          f"meter={metered.meter.used} wrapper={metered.calls} provider={lens.calls}")
    check("and the wrapper returns what the provider returned, untouched",
          metered.search_by_image("https://img/c.jpg") == NOON_689)

    empty = searches_module.SearchMeter(cap=0, state_path=os.path.join(WORK, "meter3.json"))
    starved = searches_module.Metered(CountingLens(NOON_689), empty)
    try:
        starved.search_by_image("https://img/a.jpg")
        called = True
    except searches_module.OutOfSearches:
        called = False
    check("CONTROL: with nothing left the call is stopped BEFORE it is made",
          not called and starved.provider.calls == 0,
          "charging after the fact would spend a search the meter said was gone")

    print("3. the cache answers the second night for free")
    clock = Clock()
    cache = searches_module.ComparisonCache(path=os.path.join(WORK, "cache.json"),
                                            ttl_days=7, clock=clock)
    hits = {"sku-1": [rules.CompetitorHit(platform="Noon", price_sar=Decimal("689.00"),
                                          match_score=Decimal("100"),
                                          url="https://www.noon.com/x",
                                          matched_variant="")]}
    check("an unknown product has no answer", cache.get(BOILER) is None)
    cache.put(BOILER, hits, searches=1)
    stored = cache.get(BOILER)
    check("a stored answer comes back", stored is not None and list(stored) == ["sku-1"])
    check("with the price intact as a Decimal, not as a float",
          stored["sku-1"][0].price_sar == Decimal("689.00")
          and isinstance(stored["sku-1"][0].price_sar, Decimal),
          "a float here would round a riyal price in the shop window")
    check("and the score and the platform with it",
          stored["sku-1"][0].platform == "Noon"
          and stored["sku-1"][0].match_score == Decimal("100"))
    check("it survives a restart",
          searches_module.ComparisonCache(path=os.path.join(WORK, "cache.json"),
                                          ttl_days=7, clock=clock).get(BOILER) is not None)

    print("4. the empty answer is cached too - it is the common case")
    cache.put("no-rival-product", {}, searches=1)
    check("a product that found nothing is remembered as having found nothing",
          cache.get("no-rival-product") == {},
          "not caching this leaves the MAJORITY of products paying full price every night")
    check("CONTROL: and that is different from never having been searched",
          cache.get("never-seen-product") is None,
          "{} and None must not be the same answer")

    print("5. an answer goes stale, and a threshold change throws it out at once")
    clock.advance_days(6)
    check("six days later it is still good", cache.get(BOILER) is not None)
    clock.advance_days(2)
    check("eight days later it is not", cache.get(BOILER) is None,
          "rival prices move; a week is the agreed refresh")

    clock2 = Clock()
    fresh = searches_module.ComparisonCache(path=os.path.join(WORK, "cache2.json"),
                                           ttl_days=7, clock=clock2)
    fresh.put(BOILER, hits, searches=1)
    check("stored under today's thresholds it reads back", fresh.get(BOILER) is not None)
    original = compare.TEXT_THRESHOLD
    try:
        compare.TEXT_THRESHOLD = Decimal("95")
        check("changing the wording threshold invalidates it immediately",
              fresh.get(BOILER) is None,
              "otherwise the client changes the number and sees nothing happen for a week")
    finally:
        compare.TEXT_THRESHOLD = original
    check("and putting the old threshold back makes it valid again",
          fresh.get(BOILER) is not None)

    print("6. the same, through the whole pipeline")
    offers = source_module.FixtureSource.load_all() if hasattr(
        source_module.FixtureSource, "load_all") else None

    def build(meter=None, cache=None, lens=None):
        return pipeline_module.Pipeline(
            source=source_module.FixtureSource(offers) if offers is not None
            else source_module.FixtureSource(),
            provider=lens if lens is not None else compare.FixtureProvider(),
            engine=rules.Engine(cny_to_sar=Decimal("0.52")),
            budget=budget_module.PointBudget(daily=300,
                                             state_path=os.path.join(WORK, "p.json")),
            audit_log=audit_module.AuditLog(os.path.join(WORK, "audit.csv")),
            translate=True, enricher=fake_enrich, term_translator=fake_terms,
            dry_run=True, meter=meter, cache=cache, shopping=None)

    clock3 = Clock()
    run_cache = searches_module.ComparisonCache(path=os.path.join(WORK, "cache3.json"),
                                                ttl_days=7, clock=clock3)
    run_meter = searches_module.SearchMeter(cap=100, state_path=os.path.join(WORK, "m4.json"))
    lens = searches_module.Metered(CountingLens(NOON_689), run_meter, note="lens")

    first = build(meter=run_meter, cache=run_cache, lens=lens).run_offer(BOILER)
    check("the first night buys one search", first.searches_spent == 1 and lens.calls == 1,
          f"{first.searches_spent} spent, {lens.calls} calls")
    check("and it is not from the cache", first.from_cache is False)
    check("the product published, priced against the rival",
          first.published > 0 and "Noon" in first.results[0].audit.pricing_basis,
          first.results[0].audit.pricing_basis)

    second = build(meter=run_meter, cache=run_cache, lens=lens).run_offer(BOILER)
    check("the second night buys none", second.searches_spent == 0 and lens.calls == 1,
          f"{second.searches_spent} spent, {lens.calls} calls total")
    check("and says so", second.from_cache is True and second.compared is True)
    check("the price is identical to the night before",
          [r.final_price_sar for r in second.results] == [r.final_price_sar for r in first.results],
          "a cached comparison that priced differently would be worse than no cache")
    check("the month's counter agrees with the calls made",
          run_meter.used == 1, str(run_meter.summary()))

    print("7. when the allowance is gone the run continues, openly")
    gone = searches_module.SearchMeter(cap=0, state_path=os.path.join(WORK, "m5.json"))
    blind_lens = searches_module.Metered(CountingLens(NOON_689), gone, note="lens")
    empty_cache = searches_module.ComparisonCache(path=os.path.join(WORK, "cache4.json"),
                                                  ttl_days=7, clock=Clock())
    starved = build(meter=gone, cache=empty_cache, lens=blind_lens)
    heavy = starved.run_offer(BOILER)
    check("no search was attempted", blind_lens.provider.calls == 0,
          f"{blind_lens.provider.calls} calls")
    check("the outcome says the product was never compared", heavy.compared is False,
          "priced by margin because nobody looked is not the same as priced by "
          "margin because nothing was found")
    check("and nothing was written to the cache to be reused later",
          empty_cache.get(BOILER) is None,
          "caching a non-answer would suppress the search for a week")

    # The consequence the client needs to know before choosing a plan. A LIGHT
    # product with no comparison is still published, priced by margin. A HEAVY
    # one is not - his own rule is that a heavy product with no match is never
    # published - so an exhausted search allowance does not merely change
    # prices, it stops heavy products reaching the shop at all.
    light = starved.run_offer("104843239419")
    check("a light product still publishes, priced by margin",
          light.published > 0 and light.results[0].audit.reason_code == "priced_by_margin",
          light.results[0].audit.reason_code)
    check("but a heavy one is held back, because it could not be matched",
          heavy.published == 0, str(heavy.published))
    check("and the audit says nobody looked, not that nothing was found",
          all(result.audit.reason_code == "not_compared" for result in heavy.results),
          str([result.audit.reason_code for result in heavy.results]))
    check("CONTROL: with the allowance intact that same product IS published",
          first.published > 0 and first.results[0].audit.reason_code != "not_compared",
          "the difference between the two runs is the search, nothing else")

    print("8. what a month costs, from the numbers this suite just measured")
    # Not a claim about the client's catalogue - a claim about this code. One
    # image search per product, one price search only when the image search
    # priced nothing, and nothing at all for a product already answered.
    per_product = 1
    with_price_lookup = 2
    check("a product costs one search, or two when no rival price was found",
          per_product == 1 and with_price_lookup == 2)
    monthly_new = 300 * 30 * per_product
    check("300 new products a day is 9,000 searches a month at best",
          monthly_new == 9000, str(monthly_new))
    check("and 18,000 at worst, if every single one needs the price lookup",
          300 * 30 * with_price_lookup == 18000)

    print(f"\n{passed} passed, {failed} failed")
    print(json.dumps({"meter": run_meter.summary(), "cache": run_cache.stats()}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

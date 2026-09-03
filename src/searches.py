"""
The SerpApi allowance: how many searches are left this month, and which
products already have an answer that does not need buying again.

The 1688 side is metered in points a day (budget.py). This is the other meter,
and it is the one that costs the client money directly: a SerpApi plan is a
fixed number of searches a month, and at 300 products a day an unmanaged
pipeline walks through a month's allowance in a week.

Three mechanisms, in the order they save the most:

  1. Cache the answer, including the empty one. Most products find no
     qualifying rival - that is the expected outcome, not a failure - and a
     product that found nothing last night will find nothing tonight. Caching
     only the successes would leave the common case paying full price every
     night. The entry expires after KDX_COMPARE_TTL_DAYS so prices stay current.

  2. Fingerprint the policy that produced the answer. If the match thresholds or
     the search scope change, every cached answer was computed under a rule that
     no longer applies and is discarded on the spot. Without this, changing the
     threshold would look like it had done nothing until the cache aged out.

  3. Meter the month. When the allowance is gone the run does not fail and does
     not silently start guessing: it stops comparing, prices by margin, and the
     outcome says the product was not compared.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from decimal import Decimal

import paths
from budget import RIYADH
from rules import CompetitorHit


def meter_state_path() -> str:
    return paths.state_path("searches.json", "KDX_SEARCH_STATE")


def cache_path() -> str:
    return paths.state_path("comparisons.json", "KDX_COMPARE_CACHE")


# The plan the client is on, in searches a month. Wrong-but-low is the safe
# direction: it stops early, it never overspends a plan that was smaller than
# this number claims.
MONTHLY_CAP = int(os.environ.get("KDX_SEARCH_MONTHLY_CAP", "30000"))

# How long a comparison stays good for. A week, because rival prices move but
# not nightly, and nightly re-comparison is what makes the bill impossible.
TTL_DAYS = int(os.environ.get("KDX_COMPARE_TTL_DAYS", "7"))


class OutOfSearches(RuntimeError):
    pass


def billing_month(now: datetime | None = None) -> str:
    """SerpApi bills by the calendar month; the client's calendar, not UTC."""
    now = now or datetime.now(RIYADH)
    return now.astimezone(RIYADH).strftime("%Y-%m")


def policy_fingerprint() -> str:
    """The settings whose change invalidates every stored comparison."""
    import compare
    return "|".join(str(part) for part in (
        compare.MATCH_THRESHOLD, compare.TEXT_THRESHOLD,
        compare.LENS_SCOPE, compare.SHOPPING_WHEN,
        "off" if os.environ.get("KDX_SHOPPING", "on").strip().lower() == "off" else "on",
        # How deep the picture is read, and how far down it still counts. Both
        # changed on 3 September, and a cached answer computed when only the
        # top three rows could match is an answer to a different question.
        compare.VISUAL_DECAY_PER_RANK, compare.VISUAL_RANK_LIMIT,
        # Whether a price may come from a platform the picture did not identify.
        # A cached answer from before this was allowed is missing rows, not
        # merely stale, so it has to be recomputed rather than reused.
        compare.UNBACKED_TEXT_THRESHOLD, compare.CROSS_PLATFORM_PRICING,
    ))


# --------------------------------------------------------------------------
# The monthly meter
# --------------------------------------------------------------------------

class SearchMeter:
    def __init__(self, cap: int | None = None, state_path: str = ""):
        # None means "not told, use the configured plan". Zero means zero, and
        # must not fall through to the default: a guard that turns "spend
        # nothing" into "spend thirty thousand" fails in the direction that
        # costs the client money.
        self.cap = MONTHLY_CAP if cap is None else int(cap)
        self.state_path = state_path or meter_state_path()
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            state = {}
        month = billing_month()
        if state.get("month") != month:
            history = state.get("history", {})
            if state.get("month"):
                history[state["month"]] = state.get("used", 0)
            state = {"month": month, "used": 0, "history": history}
        return state

    def _save(self) -> None:
        directory = os.path.dirname(self.state_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=1)

    def roll_month_if_needed(self) -> None:
        month = billing_month()
        if self.state.get("month") != month:
            history = self.state.setdefault("history", {})
            history[self.state["month"]] = self.state.get("used", 0)
            for old in sorted(history)[:-24]:
                history.pop(old)
            self.state = {"month": month, "used": 0, "history": history}
            self._save()

    @property
    def used(self) -> int:
        self.roll_month_if_needed()
        return int(self.state.get("used", 0))

    def remaining(self) -> int:
        return max(0, self.cap - self.used)

    def can_spend(self, searches: int = 1) -> bool:
        return self.remaining() >= searches

    def spend(self, searches: int = 1, note: str = "") -> int:
        self.roll_month_if_needed()
        if not self.can_spend(searches):
            raise OutOfSearches(
                f"monthly SerpApi allowance exhausted: {self.used}/{self.cap} "
                f"in {self.state['month']}" + (f" ({note})" if note else ""))
        self.state["used"] = self.used + searches
        self._save()
        return self.remaining()

    def summary(self) -> dict:
        return {"month": self.state["month"], "used": self.used,
                "remaining": self.remaining(), "cap": self.cap}


class Metered:
    """
    A provider that charges the meter before every call it forwards.

    Wrapping rather than counting inside compare.py on purpose: the count then
    cannot drift from the calls actually made, because it IS the calls made.
    """

    def __init__(self, provider, meter: SearchMeter, note: str = ""):
        self.provider = provider
        self.meter = meter
        self.note = note
        self.calls = 0

    def _charge(self) -> None:
        self.meter.spend(1, note=self.note)
        self.calls += 1

    def search_by_image(self, image_url: str):
        self._charge()
        return self.provider.search_by_image(image_url)

    def search_by_title(self, title: str):
        self._charge()
        return self.provider.search_by_title(title)


# --------------------------------------------------------------------------
# The per-product cache
# --------------------------------------------------------------------------

def _hit_to_dict(hit: CompetitorHit) -> dict:
    return {"platform": hit.platform, "price_sar": str(hit.price_sar),
            "match_score": str(hit.match_score), "url": hit.url,
            "matched_variant": hit.matched_variant}


def _hit_from_dict(row: dict) -> CompetitorHit:
    return CompetitorHit(platform=row["platform"], price_sar=Decimal(row["price_sar"]),
                         match_score=Decimal(row["match_score"]),
                         url=row.get("url", ""),
                         matched_variant=row.get("matched_variant", ""))


class ComparisonCache:
    def __init__(self, path: str = "", ttl_days: int | None = None, clock=time.time):
        self.path = path or cache_path()
        # Same rule as the cap above: 0 days means every answer is stale on
        # arrival, and must not be read as "unset".
        self.ttl_seconds = (TTL_DAYS if ttl_days is None else int(ttl_days)) * 86400
        self.clock = clock
        self.entries = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return {}
        return stored if isinstance(stored, dict) else {}

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.entries, handle, indent=1)

    def age_of(self, offer_id: str) -> float | None:
        entry = self.entries.get(str(offer_id))
        if not entry:
            return None
        return self.clock() - float(entry.get("at", 0))

    def get(self, offer_id: str):
        """
        The stored answer, or None when there is none worth reusing.

        None is returned for a missing entry, an expired one, and one computed
        under different thresholds - the caller cannot tell the three apart and
        does not need to: all three mean "search again".
        """
        entry = self.entries.get(str(offer_id))
        if not entry:
            return None
        if entry.get("policy") != policy_fingerprint():
            return None
        if self.clock() - float(entry.get("at", 0)) > self.ttl_seconds:
            return None
        return {sku: [_hit_from_dict(row) for row in rows]
                for sku, rows in (entry.get("hits") or {}).items()}

    def put(self, offer_id: str, hits: dict, searches: int = 0) -> None:
        """
        Store the answer - including an empty one, which is the common case and
        the one that saves the most.
        """
        self.entries[str(offer_id)] = {
            "at": self.clock(),
            "policy": policy_fingerprint(),
            "searches": int(searches),
            "hits": {sku: [_hit_to_dict(hit) for hit in rows]
                     for sku, rows in (hits or {}).items()},
        }
        self.save()

    def stats(self) -> dict:
        fresh = sum(1 for offer_id in self.entries if self.get(offer_id) is not None)
        return {"stored": len(self.entries), "fresh": fresh,
                "ttl_days": self.ttl_seconds // 86400}


def build_meter() -> SearchMeter | None:
    """
    KDX_SEARCH_MONTHLY_CAP=0 turns metering OFF - no ceiling, spend whatever
    the plan allows and let SerpApi be the judge. That is not the same as a
    SearchMeter built with cap=0, which is a real ceiling of zero searches.
    """
    if MONTHLY_CAP <= 0:
        return None
    return SearchMeter()


def build_cache() -> ComparisonCache | None:
    if os.environ.get("KDX_COMPARE_CACHE", "").strip().lower() == "off":
        return None
    return ComparisonCache()


if __name__ == "__main__":
    print(json.dumps({"meter": SearchMeter().summary(),
                      "cache": ComparisonCache().stats(),
                      "policy": policy_fingerprint()}, indent=1))

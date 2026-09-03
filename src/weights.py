"""
The weight of a product, worked out by the system instead of typed by hand.

THE CLIENT'S OBJECTION, 3 September 2026, and it is a correct one:

    "I cannot give you a weight per category, because every main category has
     subcategories, and the subcategories hold big products and small ones -
     some need fast shipping and some need free shipping. [...] Let the
     comparison be done by the system, not by hand. We will pass more than
     500,000 subcategories; they cannot be filed by hand."

He is right on both counts, so `KDX_CATEGORY_WEIGHTS` stops being the plan. It
is still honoured - a number he types is a number he means - but nothing waits
for it any more.

WHAT REPLACES IT. Measured 3 September against the live pool, 240 offers drawn
across twelve departments on purpose so the sample was not all clothing:

    declared a weight in shippingInfo   153 / 240   (64%)
        under `unitWeight`                     153
        under `offerSuttleWeight`               18  (always beside unitWeight)
        under plain `weight`                     0
    over the 2 kg line                   35 / 153   (23%)

Two things follow. First, most of the pool states its own weight, so most
products never needed a table at all. Second - and this corrects a claim in the
README written from a 30-offer sample of light goods - **products over 2 kg do
exist**: one leaf held sixteen offers between 13 and 15 kg. The shop has never
had a free-shipping product because the sample that was looked at had none in
it, not because the catalogue has none.

For the offers that declare nothing, this module learns the number from the ones
that do, per leaf category, and it only answers where the category has earned
the right to be asked:

    * at least MIN_SAMPLES declared weights, and
    * every one of them on the SAME side of the 2 kg line.

That second condition is his objection turned into a test. A leaf that really
does mix a screw and a toolbox straddles the line, fails it, and is never asked
- the product falls to the light default instead of being confidently guessed
wrong. Which leaves are like that is measured, not assumed: see the leave-one-
out figures in README.md.

WHICH WAY THE DEFAULT LEANS. `needs_shipment` is true at or below 2 kg, and true
means the customer is charged carriage. So an unknown product treated as light
is one the buyer pays to ship; treated as heavy it ships free and the shop pays.
The unknown case therefore stays light, which is the same policy the client set
on 30 August, and the flag travels with it so the audit never reads as though
the box had been on a scale.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
from collections import defaultdict

import paths

# The line the shipping rule turns on. Kept in step with mapping.LIGHT_MAX_KG,
# which owns it; imported lazily below rather than at module scope so this file
# stays usable on its own.
DEFAULT_LIGHT_MAX_KG = 2.0


def light_max_kg() -> float:
    try:
        import mapping
        return float(mapping.LIGHT_MAX_KG)
    except Exception:                                       # noqa: BLE001
        return float(os.environ.get("KDX_LIGHT_MAX_KG", DEFAULT_LIGHT_MAX_KG))


# Three is the smallest number that can disagree with itself: with two samples a
# single odd supplier makes the category unanimous, and unanimity is the whole
# guarantee being offered here.
MIN_SAMPLES = int(os.environ.get("KDX_WEIGHT_MIN_SAMPLES", "3"))

# A declared weight above this is not a shipping weight, it is a typo, and it
# has to be caught because it fails in the expensive direction: over 2 kg the
# product ships FREE and the shop pays the carriage.
#
# Measured over 851 declared weights on 3 September: 61 sit between 2 and 50 kg
# and read as real (a 15 kg factory inspection robot, a 4 kg stove part), one is
# a 100 kg carbon-steel civil-defence valve which is also believable, and three
# are not - two lots of plastic granulate at 1,000 kg, which is a raw material
# priced by the tonne, and a brass horn at 10,000 kg, which is a supplier who
# typed grams. Three in 851. They are treated as though nothing was declared, so
# the category answers instead; nothing is silently rewritten to a number the
# supplier never gave.
#
# There is no floor beyond "greater than zero". 68 offers declare under 10 g and
# some of them are true - a postage stamp really is a gram - and the ones that
# are wrong fail in the safe direction: the customer is charged carriage.
MAX_CREDIBLE_KG = float(os.environ.get("KDX_MAX_CREDIBLE_WEIGHT_KG", "100"))

# A ceiling on what one category remembers, so a file read at the start of every
# batch cannot grow without limit over months of pulling. The newest are kept:
# a supplier who changes his packaging should not be outvoted forever by what he
# used to declare. Two hundred is far above MIN_SAMPLES, so the unanimity test
# still sees a real spread rather than a recent accident.
MAX_SAMPLES = int(os.environ.get("KDX_WEIGHT_MAX_SAMPLES", "200"))


def table_path() -> str:
    return paths.state_path("weights.json", "KDX_WEIGHT_TABLE")


def seed_path() -> str:
    """
    The measured corpus that ships with the code.

    A machine with an empty state file would answer "no opinion" for every
    category until it had pulled enough of the catalogue to have one, which
    means the first days publish under the default while the table it needs is
    sitting in the repository. So the seed is read once, when there is no state
    file yet, and everything learned afterwards is added on top of it.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("KDX_WEIGHT_SEED",
                          os.path.join(here, "data", "category_weights.json"))


def declared_weight(shipping: dict):
    """
    The weight the supplier states for this offer, in kilograms, or None.

    The order matters: offerSuttleWeight is the net weight and appears only
    beside unitWeight, never alone, so reading it first costs nothing and
    prefers the more specific of the two. A zero or a negative is not a weight;
    it is a field somebody left at its default, and treating 0 as "very light"
    would charge carriage on a pallet. Above MAX_CREDIBLE_KG is not a weight
    either - see the note on that constant.
    """
    if not isinstance(shipping, dict):
        return None
    for key in ("offerSuttleWeight", "unitWeight", "weight", "grossWeight"):
        value = shipping.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if 0 < number <= MAX_CREDIBLE_KG:
            return number
    return None


class WeightTable:
    """
    What the pool has declared so far, per leaf category.

    Holds the samples themselves rather than a running median, because the rule
    asks a question a median cannot answer - do these all sit on one side of the
    line - and because a bad observation can then be taken back out.
    """

    def __init__(self, samples: dict | None = None, path: str = ""):
        self.samples = defaultdict(list)
        for category, weights in (samples or {}).items():
            self.samples[str(category)] = [float(w) for w in weights]
        self.path = path or table_path()
        self._dirty = False

    # --- loading and saving -------------------------------------------------

    @classmethod
    def load(cls, path: str = "") -> "WeightTable":
        path = path or table_path()
        for candidate in (path, seed_path()):
            try:
                with open(candidate, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                continue
            return cls(payload.get("samples") or payload, path=path)
        return cls({}, path=path)

    def save(self) -> None:
        """
        Written whole, through a temporary file, then renamed.

        Batches run every half hour and one can still be finishing when the next
        starts. A half-written table read by the next batch would not fail - it
        would parse as far as it got and answer with a truncated catalogue,
        which is the kind of wrongness nothing reports.
        """
        if not self._dirty:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        payload = {"samples": {k: v for k, v in self.samples.items() if v}}
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory,
                                             delete=False)
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
        self._dirty = False

    # --- learning -----------------------------------------------------------

    def observe(self, category_id, kilograms) -> bool:
        """Record one supplier-declared weight against its leaf category."""
        category = str(category_id or "")
        try:
            weight = float(kilograms)
        except (TypeError, ValueError):
            return False
        # The same credibility test the reader applies. A 10,000 kg trumpet must
        # not get a vote on what its category weighs, or one typo turns a whole
        # leaf into a free-shipping department.
        if not category or not (0 < weight <= MAX_CREDIBLE_KG):
            return False
        kept = self.samples[category]
        kept.append(weight)
        if MAX_SAMPLES > 0 and len(kept) > MAX_SAMPLES:
            del kept[:len(kept) - MAX_SAMPLES]
        self._dirty = True
        return True

    # --- answering ----------------------------------------------------------

    def opinion(self, category_id) -> dict | None:
        """
        What this category has to say about itself, or None if it has no right
        to an opinion yet.

        Returned as a record rather than a number so the caller can put the
        evidence in the audit line: a weight that came from eleven other offers
        in the same leaf is a different claim from one that came from three, and
        the client has to be able to see which he is looking at.
        """
        weights = self.samples.get(str(category_id or "")) or []
        if len(weights) < MIN_SAMPLES:
            return None
        line = light_max_kg()
        if len({w > line for w in weights}) > 1:
            # The client's objection, in this leaf, today. It mixes big and
            # small, so it is not asked.
            return None
        return {"category_id": str(category_id), "samples": len(weights),
                "kg": round(statistics.median(weights), 3),
                "min": min(weights), "max": max(weights)}

    def estimate(self, category_id, chain=None) -> dict | None:
        """
        The best opinion available for this product, leaf first.

        `chain` is the ancestry root-first, the shape catalog.CategoryIndex and
        category_live.LiveIndex both answer with. It is walked backwards because
        the leaf is the most specific thing that can be said, and only if the
        leaf has nothing to say is the department above it asked - a department
        is a blunt unit, but a blunt answer from real measurements beats the
        blanket default.

        A department that straddles the line is refused by `opinion` exactly as
        a leaf is, so walking up cannot smuggle in the mixing the client warned
        about; it just usually finds nothing, and that is the correct outcome.
        """
        direct = self.opinion(category_id)
        if direct is not None:
            return direct
        for row in reversed(list(chain or [])):
            found = self.opinion(row.get("id") if isinstance(row, dict) else row)
            if found is not None:
                return found
        return None

    # --- reporting ----------------------------------------------------------

    def summary(self) -> dict:
        line = light_max_kg()
        confident = heavy = mixed = 0
        for weights in self.samples.values():
            if len(weights) < MIN_SAMPLES:
                continue
            sides = {w > line for w in weights}
            if len(sides) > 1:
                mixed += 1
                continue
            confident += 1
            if True in sides:
                heavy += 1
        return {"categories": len(self.samples),
                "samples": sum(len(w) for w in self.samples.values()),
                "with_an_opinion": confident,
                "of_those_heavy": heavy,
                "straddling_the_line": mixed}

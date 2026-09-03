"""
Checks for the weight the system works out for itself.

    python3 verify_weights.py

No network. The client refused a hand-written kilograms table on 3 September -
"each main category has subcategories, and the subcategories hold big products
and small ones" - so the number now comes from what suppliers declare, and these
are the checks that the learned answer cannot quietly go wrong.

The controls matter more than the happy path here. A table that answers for
everything would look exactly like a table that works, and be wrong on every
category that really does mix a screw and a toolbox.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import pipeline  # noqa: E402
import selected  # noqa: E402
import source  # noqa: E402
import weights  # noqa: E402

PASSED = 0
FAILED = 0


def check(what: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok    {what}")
    else:
        FAILED += 1
        print(f"  FAIL  {what}" + (f"   [{detail}]" if detail else ""))


class Tree:
    """The ancestry shape catalog.CategoryIndex and LiveIndex both answer with."""

    def __init__(self, chains: dict):
        self.chains = chains

    def chain(self, category_id):
        return [{"id": part} for part in self.chains.get(str(category_id or ""), [])]


def table(samples: dict) -> weights.WeightTable:
    return weights.WeightTable(samples, path=os.path.join(tempfile.gettempdir(),
                                                          "kdx-verify-weights.json"))


def offer(shipping: dict, category: str = "77") -> dict:
    """A pool detail record, in the shape alibaba.pifatuan.product.detail.list
    really answers with - measured 3 September: shippingInfo, not
    productShippingInfo, and the weight under unitWeight."""
    return {"productID": "1001", "categoryID": category,
            "subject": "测试", "shippingInfo": dict(shipping),
            "image": {"images": ["img/ibank/a.jpg"]},
            "productSaleInfo": {"priceRangeList": [{"price": "10.00"}]}}


def main() -> int:
    print("\nwhat counts as a weight the supplier declared")

    check("unitWeight is read - it is the spelling 153 of 240 offers used",
          weights.declared_weight({"unitWeight": "0.35"}) == 0.35)
    check("offerSuttleWeight wins where both are present - it is the net weight",
          weights.declared_weight({"unitWeight": 2, "offerSuttleWeight": 1.5}) == 1.5)
    check("CONTROL a zero is not a weight, it is a field left at its default",
          weights.declared_weight({"unitWeight": 0}) is None,
          "0 kg would charge the customer carriage on a pallet")
    check("CONTROL a negative is not a weight either",
          weights.declared_weight({"unitWeight": -3}) is None)
    check("CONTROL text that is not a number is not a weight",
          weights.declared_weight({"unitWeight": "heavy"}) is None)
    check("CONTROL an empty shipping block declares nothing",
          weights.declared_weight({}) is None)
    check("CONTROL something that is not a dict does not raise",
          weights.declared_weight(None) is None)

    print("\nand what is too big to be one")

    check("a 15 kg factory robot is a weight - it was measured, twice over",
          weights.declared_weight({"unitWeight": 15}) == 15)
    check("a 10,000 kg brass horn is not - that is a supplier who typed grams, "
          "and over 2 kg it would ship FREE at the shop's expense",
          weights.declared_weight({"unitWeight": 10000}) is None)
    check("the credible one wins when both are present, rather than the whole "
          "offer losing its weight",
          weights.declared_weight({"offerSuttleWeight": 10000,
                                   "unitWeight": 12}) == 12)
    check("CONTROL an incredible weight gets no vote on its category either",
          table({}).observe("x", 10000) is False)
    check("CONTROL the ceiling is where the measurement put it, not rounder",
          weights.MAX_CREDIBLE_KG == 100.0, str(weights.MAX_CREDIBLE_KG))

    print("\na category only answers when it has earned the right to")

    line = weights.light_max_kg()
    check("CONTROL the line these tests are written against is the "
          "shipping rule's own", line == 2.0, f"light_max_kg()={line}")

    tight = table({"light": [0.5, 0.6, 0.55], "heavy": [10, 12, 15],
                   "mixed": [0.5, 10, 0.6], "thin": [1.0, 1.1]})
    check("a leaf whose declared weights all sit under the line answers light",
          tight.opinion("light")["kg"] == 0.55)
    check("a leaf whose declared weights all sit over the line answers heavy",
          tight.opinion("heavy")["kg"] == 12.0)
    check("CONTROL a leaf that straddles the line REFUSES to answer - this is "
          "the client's objection, and it is the whole guarantee",
          tight.opinion("mixed") is None,
          "a leaf holding a screw and a toolbox must not be asked")
    check("CONTROL two samples are not enough: with two, one odd supplier makes "
          "a category unanimous",
          tight.opinion("thin") is None)
    check("CONTROL a category nobody has measured has no opinion",
          tight.opinion("never-seen") is None)
    check("CONTROL and neither has an empty table",
          table({}).opinion("light") is None)

    check("the opinion carries how many offers stood behind it, so an audit "
          "line can say three rather than eleven",
          tight.opinion("heavy")["samples"] == 3)

    print("\nthe median, not the mean - one mislabelled pallet must not move a "
          "whole category")

    skewed = table({"x": [0.5, 0.5, 0.5, 0.5, 1000.0]})
    check("four half-kilo offers and one 1000 kg outlier still answer light",
          skewed.opinion("x") is None or skewed.opinion("x")["kg"] == 0.5,
          str(skewed.opinion("x")))
    check("CONTROL and because that outlier straddles the line, the category "
          "declines to answer at all rather than answering 0.5",
          skewed.opinion("x") is None)

    print("\nwalking up to the department, and refusing to smuggle mixing in")

    tree = Tree({"leaf": ["dept", "sub", "leaf"], "lonely": ["messy", "lonely"]})
    up = table({"dept": [0.2, 0.3, 0.25]})
    found = up.estimate("leaf", tree.chain("leaf"))
    check("a leaf with nothing of its own takes the department above it",
          found is not None and found["category_id"] == "dept")
    check("and the audit is told which category answered, not which one the "
          "product is filed under",
          found["category_id"] == "dept" and found["category_id"] != "leaf")

    messy = table({"messy": [0.2, 15.0, 0.3]})
    check("CONTROL a department that straddles the line is refused exactly as a "
          "leaf is - walking up cannot smuggle the mixing back in",
          messy.estimate("lonely", tree.chain("lonely")) is None)

    specific = table({"leaf": [5.0, 5.0, 5.0], "dept": [0.2, 0.3, 0.25]})
    found = specific.estimate("leaf", tree.chain("leaf"))
    check("the leaf wins over the department - the more specific measurement is "
          "the better one",
          found["kg"] == 5.0 and found["category_id"] == "leaf")

    check("CONTROL with no ancestry at all the leaf is still asked",
          table({"leaf": [5.0, 5.0, 5.0]}).estimate("leaf")["kg"] == 5.0)
    check("CONTROL and a leaf with nothing and no ancestry answers nothing",
          table({"dept": [1, 1, 1]}).estimate("leaf") is None)

    print("\nlearning from the pool, and never overwriting a real measurement")

    learned = table({})
    pool = selected.SelectedPool(client=None, weight_table=learned)

    row = pool.normalise(offer({"unitWeight": "0.9"}, category="77"))
    check("a declared weight is used as declared",
          row["weight_kg"] == 0.9)
    check("and it is NOT marked assumed - it was measured by the supplier",
          row["weight_assumed"] is False)
    check("and it teaches the table",
          learned.samples["77"] == [0.9])

    for _ in range(2):
        pool.normalise(offer({"unitWeight": "1.1"}, category="77"))
    row = pool.normalise(offer({}, category="77"))
    check("an offer declaring nothing takes its category's median once the "
          "category has three measurements",
          row["weight_kg"] == 1.1, str(row.get("weight_kg")))
    check("but it stays flagged assumed - a median is a policy, not a scale",
          row["weight_assumed"] is True)
    check("and it says how many offers it came from",
          row["weight_samples"] == 3)

    row = pool.normalise(offer({}, category="never-measured"))
    check("CONTROL a category with nothing behind it falls to the light "
          "default, which is the side that charges the customer rather than "
          "the shop",
          row["weight_assumed"] is True and row["weight_kg"] <= 2.0,
          str(row.get("weight_kg")))
    check("CONTROL and that fallback claims no category",
          not row.get("weight_samples"))

    heavy_pool = selected.SelectedPool(
        client=None, weight_table=table({"hv": [10.0, 11.0, 12.0]}))
    row = heavy_pool.normalise(offer({"unitWeight": "0.4"}, category="hv"))
    check("CONTROL a real 0.4 kg is never overwritten by a category that "
          "usually holds 11 kg boxes",
          row["weight_kg"] == 0.4 and row["weight_assumed"] is False)

    print("\nthe same table answers for the image-search channel, which never "
          "reports a weight at all")

    normalised = {"category_id": "leaf", "weight_kg": 1.0, "weight_assumed": True}
    out = pipeline._weigh_by_category(dict(normalised), tree,
                                      table({"dept": [8.0, 9.0, 8.5]}))
    check("a search row with no weight takes what the pool measured for its "
          "department", out["weight_kg"] == 8.5)
    check("CONTROL with no learned table nothing changes",
          pipeline._weigh_by_category(dict(normalised), tree, None) == normalised)
    check("CONTROL a measured weight is left alone even here",
          pipeline._weigh_by_category(
              {"category_id": "leaf", "weight_kg": 1.0, "weight_assumed": False},
              tree, table({"dept": [8.0, 9.0, 8.5]}))["weight_kg"] == 1.0)

    keep = os.environ.get("KDX_CATEGORY_WEIGHTS")
    os.environ["KDX_CATEGORY_WEIGHTS"] = json.dumps({"dept": 3.0})
    try:
        out = pipeline._weigh_by_category(dict(normalised), tree,
                                          table({"dept": [8.0, 9.0, 8.5]}))
        check("a number the client types himself still beats the learned one - "
              "the only reason to type one is to state a real one",
              out["weight_kg"] == 3.0)
        check("and the learned sample count does not travel with his number",
              not out.get("weight_samples"))
    finally:
        if keep is None:
            os.environ.pop("KDX_CATEGORY_WEIGHTS", None)
        else:
            os.environ["KDX_CATEGORY_WEIGHTS"] = keep

    print("\nsource.normalise must not report an assumption as a measurement")

    weighed = source.normalise({"productInfo": {
        "productID": "9", "categoryID": "1", "subject": "s",
        "productSaleInfo": {"priceRangeList": [{"price": "10.00"}]},
        "shippingInfo": {"unitWeight": 0.75}}})
    check("a declared weight arrives measured", weighed["weight_assumed"] is False)
    silent = source.normalise({"productInfo": {
        "productID": "9", "categoryID": "1", "subject": "s",
        "productSaleInfo": {"priceRangeList": [{"price": "10.00"}]},
        "shippingInfo": {}}})
    check("CONTROL a product nobody weighed arrives ASSUMED, not measured - the "
          "2.5 kg default sits above the 2 kg line, so it would otherwise ship "
          "free with the audit saying it had been on a scale",
          silent["weight_assumed"] is True,
          f"weight_kg={silent['weight_kg']}")

    print("\nthe table survives being written")

    path = os.path.join(tempfile.mkdtemp(), "weights.json")
    saved = weights.WeightTable({}, path=path)
    saved.observe("a", 1.0)
    saved.observe("a", 1.2)
    saved.save()
    check("what was written comes back",
          weights.WeightTable.load(path).samples["a"] == [1.0, 1.2])
    check("CONTROL a save with nothing new does not rewrite the file",
          (lambda before: (weights.WeightTable.load(path).save(),
                           os.path.getmtime(path) == before)[1])(
              os.path.getmtime(path)))
    check("CONTROL an observation that is not a number is refused rather than "
          "stored as one", saved.observe("a", "heavy") is False)
    check("CONTROL and so is one with no category to file it under",
          saved.observe("", 1.0) is False)

    keep_max = os.environ.get("KDX_WEIGHT_MAX_SAMPLES")
    os.environ["KDX_WEIGHT_MAX_SAMPLES"] = "4"
    try:
        import importlib
        importlib.reload(weights)
        capped = weights.WeightTable({}, path=path)
        for value in (1, 2, 3, 4, 5, 6):
            capped.observe("c", value)
        check("a category remembers only its newest measurements, so a file "
              "read at the start of every batch cannot grow without limit",
              capped.samples["c"] == [3.0, 4.0, 5.0, 6.0],
              str(capped.samples["c"]))
    finally:
        if keep_max is None:
            os.environ.pop("KDX_WEIGHT_MAX_SAMPLES", None)
        else:
            os.environ["KDX_WEIGHT_MAX_SAMPLES"] = keep_max
        import importlib
        importlib.reload(weights)

    print("\nthe two batches he asked for: fast shipping, and free shipping")

    import daily_run

    class FakePool:
        """Stands in for the live pool. The offers below straddle the 2 kg line
        on purpose - a fixture where everything is light would let the filter
        pass without ever having to choose."""

        def __init__(self, *args, **kwargs):
            self.weights = table({})
            self.skipped_outside_pool = []
            self.keyword_counts = {}

        def offer_ids_for(self, keywords, **kwargs):
            return ["1", "2", "3", "4"]

        def products(self, offer_ids=None):
            for offer, kilograms in (("1", 0.5), ("2", 9.0), ("3", 1.0), ("4", 4.0)):
                yield {"offer_id": offer, "category_id": "7", "weight_kg": kilograms,
                       "title_zh": "x", "description_zh": "", "images": [],
                       "attributes": {}, "weight_assumed": False,
                       "variants": [{"original": "", "image": "", "images": [],
                                     "sizes": [], "price": 1}]}

    class FakeLedger:
        def __init__(self):
            self.added = []

        def knows_offer(self, offer):
            return False

        def add_offer(self, offer):
            self.added.append(offer)

        def save(self):
            pass

    class FakeRunner:
        categories = None

    keep_pool = selected.SelectedPool
    daily_run.selected.SelectedPool = FakePool
    try:
        book = FakeLedger()
        got, notes = daily_run.harvest_selected(FakeRunner(), None, 10, book,
                                                ["w"], shipping="fast")
        check("the fast batch takes only what the customer pays carriage on",
              [p["offer_id"] for p in got] == ["1", "3"],
              str([p["offer_id"] for p in got]))

        book = FakeLedger()
        got, notes = daily_run.harvest_selected(FakeRunner(), None, 10, book,
                                                ["w"], shipping="free")
        check("the free batch takes only what ships free",
              [p["offer_id"] for p in got] == ["2", "4"],
              str([p["offer_id"] for p in got]))
        check("CONTROL a product passed over is NOT written to the ledger - it "
              "was never published, and marking it known would hide it from the "
              "other batch that is coming for exactly this product",
              book.added == ["2", "4"], str(book.added))
        check("and the run says how many it passed over, rather than looking "
              "like the pool ran dry",
              any("passed over" in note for note in notes), str(notes))

        book = FakeLedger()
        got, _ = daily_run.harvest_selected(FakeRunner(), None, 10, book, ["w"])
        check("CONTROL with no side asked for, nothing is filtered at all",
              len(got) == 4, str(len(got)))
    finally:
        daily_run.selected.SelectedPool = keep_pool

    print("\nthe table that actually ships with the code")

    keep_table = os.environ.get("KDX_WEIGHT_TABLE")
    os.environ["KDX_WEIGHT_TABLE"] = os.path.join(tempfile.mkdtemp(), "absent.json")
    try:
        shipped = weights.WeightTable.load()
        figures = shipped.summary()
        check("with no state file yet, the measured seed is loaded rather than "
              "an empty table - otherwise the first days publish under the "
              "default while the answer sits in the repository",
              figures["samples"] > 500, str(figures))
        check("and it holds categories that can actually answer",
              figures["with_an_opinion"] > 50, str(figures))
        check("CONTROL including heavy ones - a table that could only ever say "
              "'light' would pass every other check here and never once put a "
              "product on free shipping",
              figures["of_those_heavy"] > 0, str(figures))
        check("CONTROL and it refuses on the leaves that really do mix big and "
              "small, which is what the client said would happen",
              figures["straddling_the_line"] > 0, str(figures))
        heaviest = shipped.opinion("125294001")
        check("a spot check: the 15 kg factory inspection robots are heavy",
              heaviest is not None and heaviest["kg"] > 2.0, str(heaviest))
    finally:
        if keep_table is None:
            os.environ.pop("KDX_WEIGHT_TABLE", None)
        else:
            os.environ["KDX_WEIGHT_TABLE"] = keep_table

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

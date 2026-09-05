"""
The completeness gate: what "unclear product information" means, exactly.

Client, 3 September: "any product, fast shipping or free shipping, if the
product information is unclear the product is excluded. No problem, there are
products in the billions."

That reverses a default this pipeline has had since the first night - when a
fact was missing, invent a safe one - so these assertions are mostly about the
INVENTED values no longer surviving. The one that matters is the weight: a
blanket "call it light" decided the shipping type of about a quarter of the
catalogue, and it is not a measurement.

    python3 verify_completeness.py
"""

import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import completeness  # noqa: E402
import pipeline as pipeline_module  # noqa: E402
import rules  # noqa: E402

CHECKS = 0
FAILURES = []


def check(label, condition):
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


def section(title):
    print(f"\n{title}")


def offer(**overrides) -> dict:
    """A listing with nothing missing, which every test then breaks one way."""
    base = {
        "offer_id": "900001",
        "title_zh": "不锈钢保温杯 500ml",
        "description_zh": "家用 保温杯",
        "category_id": "1031910",
        "images": ["https://cbu01.alicdn.com/img/a.jpg"],
        "attributes": {},
        "weight_kg": 0.4,
        "weight_assumed": False,
        "variants": [{
            "original": "银色", "image": "https://cbu01.alicdn.com/img/a.jpg",
            "images": ["https://cbu01.alicdn.com/img/a.jpg"],
            "price": 12.5, "stock": 30,
            "sizes": [{"original": "500ml", "price": 12.5, "sku_id": "900001-1",
                       "stock": 30}],
        }],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
section("a listing with everything present is not touched")

check("the complete offer passes", completeness.missing_before_translation(offer()) is None)
check("nothing missing after translation either",
      completeness.missing_after_translation(
          {"name_en": "Vacuum Flask 500ml", "name_ar": "ترمس 500 مل"}) is None)


# --------------------------------------------------------------------------
section("each missing field is named, so he can see which check fired")

check("no title", completeness.missing_before_translation(offer(title_zh="")) == "no_title")
check("whitespace is not a title",
      completeness.missing_before_translation(offer(title_zh="   ")) == "no_title")
check("no photograph", completeness.missing_before_translation(offer(images=[])) == "no_photo")
check("no category", completeness.missing_before_translation(offer(category_id="")) == "no_category")
check("no purchasable option",
      completeness.missing_before_translation(offer(variants=[])) == "no_options")
check("untranslated - the Arabic name still carries Chinese",
      completeness.missing_after_translation(
          {"name_en": "Vacuum Flask", "name_ar": "ترمس 保温杯"}) == "untranslated")
check("untranslated - the English name is the Chinese title, which is what a "
      "run with no translator carries through",
      completeness.missing_after_translation(
          {"name_en": "不锈钢保温杯", "name_ar": "不锈钢保温杯"}) == "untranslated")
check("untranslated - an empty Arabic name",
      completeness.missing_after_translation(
          {"name_en": "Vacuum Flask", "name_ar": ""}) == "untranslated")
check("CONTROL it reads the name that reaches the shop, not the flag: a run "
      "marked untranslated whose names are clean is not refused",
      completeness.missing_after_translation(
          {"name_en": "Vacuum Flask", "name_ar": "ترمس", "_untranslated": True}) is None)
check("every code has Arabic to go with it",
      all(completeness.reason_ar(code) and code not in completeness.reason_ar(code)
          for code in completeness.REASONS))
check("an unknown code still gets a sentence rather than an empty cell",
      bool(completeness.reason_ar("something_new")))


# --------------------------------------------------------------------------
section("the weight: which sources count as real, and which do not")

check("supplier declared it - real",
      completeness.has_usable_weight(offer(weight_assumed=False)))
check("a category answered with its own measurements - real",
      completeness.has_usable_weight(
          offer(weight_assumed=True, weight_category_id="1031910", weight_samples=7)))
check("a number the client typed for the department - real",
      completeness.has_usable_weight(
          offer(weight_assumed=True, weight_category_id="6")))
# 5 September. The blanket default was the guess he refused on the 3rd,
# because it decided what the customer pays for carriage. He then supplied the
# guess himself - "او وهمي اكثر من 10 kg" - which lands on the free-shipping
# side, where the customer pays nothing, and the carriage is in the price from
# the carton. So it now publishes, and setting his number to 0 puts the
# refusal back.
check("with his virtual weight in force, an unweighed product is publishable",
      completeness.has_usable_weight(offer(weight_assumed=True)))
check("and nothing rejects it before the translator is even asked",
      completeness.missing_before_translation(
          offer(weight_assumed=True, weight_kg=1.0)) is None)

import mapping as mapping_module                                  # noqa: E402
_kept = mapping_module.VIRTUAL_WEIGHT_KG
mapping_module.VIRTUAL_WEIGHT_KG = Decimal("0")
check("CONTROL his number set to zero restores the refusal he asked for on "
      "3 September, in one setting",
      not completeness.has_usable_weight(offer(weight_assumed=True))
      and completeness.missing_before_translation(
          offer(weight_assumed=True, weight_kg=1.0)) == "no_weight")
check("CONTROL and an empty category id still does not count as a category "
      "having answered",
      not completeness.has_usable_weight(offer(weight_assumed=True,
                                               weight_category_id="")))
mapping_module.VIRTUAL_WEIGHT_KG = _kept
check("CONTROL back with his number, it publishes again - so it is the "
      "setting doing the work, not the fixture",
      completeness.has_usable_weight(offer(weight_assumed=True)))
check("the Arabic for no_weight explains BOTH sources failed, not just the supplier",
      "المورّد" in completeness.reason_ar("no_weight")
      and "تصنيف" in completeness.reason_ar("no_weight"))


# --------------------------------------------------------------------------
section("CONTROL: the gate can be turned off, and off means off")

os.environ["KDX_REQUIRE_COMPLETE"] = "off"
check("nothing is rejected with the gate off",
      completeness.missing_before_translation(offer(title_zh="", images=[])) is None)
check("and the translation check is off too",
      completeness.missing_after_translation({"name_en": "", "name_ar": ""}) is None)
del os.environ["KDX_REQUIRE_COMPLETE"]
check("CONTROL back on, the same offer rejects again",
      completeness.missing_before_translation(offer(title_zh="")) == "no_title")


# --------------------------------------------------------------------------
section("one check can be dropped by name without dropping the rest")

os.environ["KDX_COMPLETENESS_SKIP"] = "no_weight"
check("the weight check is skipped",
      completeness.missing_before_translation(offer(weight_assumed=True)) is None)
check("but a missing photograph still rejects",
      completeness.missing_before_translation(
          offer(weight_assumed=True, images=[])) == "no_photo")
os.environ["KDX_COMPLETENESS_SKIP"] = "no_weight, no_photo"
check("two names, spaces and all",
      completeness.missing_before_translation(
          offer(weight_assumed=True, images=[])) is None)
del os.environ["KDX_COMPLETENESS_SKIP"]
check("CONTROL the skip list really was what let the PHOTO through",
      completeness.missing_before_translation(
          offer(weight_assumed=True, images=[])) == "no_photo")


# --------------------------------------------------------------------------
section("a rejection produces an audit row even when there are no options")

engine = rules.Engine(cny_to_sar=Decimal("0.52"))
product = pipeline_module.to_rules_product(offer())
rows = [engine.reject(product, variant, "no_weight",
                      completeness.reason_ar("no_weight")).audit
        for variant in product.variants]
check("one row per option", len(rows) == 1)
check("the row says reject", rows[0].decision == "reject")
check("the row carries the code he can grep for", rows[0].reason_code == "no_weight")
check("and the Arabic sentence", "لا يوجد وزن" in rows[0].reason_ar)

blank = pipeline_module._placeholder_variant()
row = engine.reject(product, blank, "no_options",
                    completeness.reason_ar("no_options")).audit
check("a listing with no options still produces a row", row.decision == "reject")
check("the placeholder carries no sku rather than a made-up one", row.sku_id == "")
check("and no price is claimed for it", row.final_price_sar == "")

empty = pipeline_module.to_rules_product(offer(variants=[]))
check("CONTROL an offer with no variants really does flatten to nothing - "
      "which is why the placeholder has to exist", len(empty.variants) == 0)


# --------------------------------------------------------------------------
section("the gate runs before anything that costs money")

source_text = open(os.path.join(HERE, "src", "pipeline.py"), encoding="utf-8").read()
gate = source_text.index("completeness.missing_before_translation")
check("above the size lookup", gate < source_text.index("self._add_skus(normalised)"))
check("above the translation", gate < source_text.index("self._enrich(product)"))
check("above the image search", gate < source_text.index("self._compare("))
after = source_text.index("completeness.missing_after_translation")
check("the translation check sits after the translation",
      after > source_text.index("self._enrich(product)"))
check("and still before the paid search", after < source_text.index("self._compare("))

harvest = open(os.path.join(HERE, "daily_run.py"), encoding="utf-8").read()
check("the harvest refuses an unweighed product before it fills a quota slot",
      harvest.index("completeness.has_usable_weight")
      < harvest.index("harvested.append(product)"))
check("and counts them separately from the ones the other batch will take",
      "unweighed" in harvest and "wrong_side" in harvest)


# --------------------------------------------------------------------------
print(f"\n{CHECKS} checks, {len(FAILURES)} failed")
for failure in FAILURES:
    print(f"  - {failure}")
sys.exit(1 if FAILURES else 0)

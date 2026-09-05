"""
Is there enough of this listing to sell it honestly?

The client, 3 September, after being asked to choose between publishing heavy
unmatched products and excluding them:

    "اي منتج سواء الى الشحن السريع ام الشحن المجاني اذا كانت معلومات المنتج
     غير واضحة يتم استبعاد المنتج لا مشكلة هناك منتجات بالمليارات لا يهم اذا
     قمت باستبعاد المنتج الذي لا يستوفي بالشروط"

    "any product, fast shipping or free shipping, if the product information is
     unclear the product is excluded. No problem, there are products in the
     billions, it does not matter if you exclude one that does not meet the
     conditions."

That sentence is worth more than it looks, because until now the pipeline's
answer to a missing fact was to invent a safe one. A listing with no stated
weight was called light, which charged the customer carriage and published it.
He has just said the opposite: when we do not know, we do not publish.

So every invented value becomes a rejection instead, and each rejection names
the fact that was missing, in Arabic, in the audit file - because the only way
he can tell me a check is too strict is if he can see which check fired and how
often. Nothing here is a judgement about quality: it is only ever "this field
is not there".

The weight is the one that matters most. His shipping rule is decided entirely
by the 2 kg line, so a product with no weight from anywhere cannot be filed as
fast or free without guessing, and guessing is what he has just refused.

KDX_REQUIRE_COMPLETE=off puts the old behaviour back for a run, and every
individual check can be switched off by name in KDX_COMPLETENESS_SKIP, so a
check that turns out to reject too much can be dropped in one place without a
release.
"""

from __future__ import annotations

import os


def _enabled() -> bool:
    return os.environ.get("KDX_REQUIRE_COMPLETE", "on").strip().lower() != "off"


def _skipped() -> set:
    raw = os.environ.get("KDX_COMPLETENESS_SKIP", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


# The Arabic the client reads in the audit file. Each one names the missing
# fact, never a verdict about the product.
REASONS = {
    "no_title": "لا يوجد اسم للمنتج في بيانات المورّد - معلومات ناقصة، لا يُنشر",
    "no_photo": "لا توجد صورة حقيقية للمنتج بعد استبعاد صور الإعلانات - "
                "معلومات ناقصة، لا يُنشر",
    "no_category": "المنتج غير مصنّف عند المورّد - معلومات ناقصة، لا يُنشر",
    "no_options": "لا توجد خيارات (مقاسات/ألوان) قابلة للقراءة - "
                  "معلومات ناقصة، لا يُنشر",
    "no_weight": "لا يوجد وزن: المورّد لم يذكره، ولا يوجد في تصنيفه عدد كافٍ من "
                 "المنتجات الموزونة المتفقة على جهة واحدة من حد الـ 2 كجم - "
                 "لا يمكن تحديد الشحن (سريع أم مجاني) بدون تخمين، فلا يُنشر",
    "untranslated": "تعذّرت ترجمة اسم المنتج أو وصفه من الصينية - "
                    "معلومات غير واضحة للعميل، لا يُنشر",
}


def _blank(value) -> bool:
    return not str(value or "").strip()


def missing_before_translation(normalised: dict) -> str | None:
    """
    The checks that can be answered from the supplier record alone.

    Deliberately every one of them runs before the translation and before the
    image search, so a listing we are going to refuse costs no OpenAI call and
    no SerpApi search. That ordering is the client's own rule from 29 August -
    "a point must never be spent on a product that was going to be rejected
    anyway" - applied to the two meters that cost him money rather than points.
    """
    if not _enabled():
        return None
    skip = _skipped()

    if "no_title" not in skip and _blank(normalised.get("title_zh")):
        return "no_title"
    if "no_photo" not in skip and not (normalised.get("images") or []):
        return "no_photo"
    if "no_category" not in skip and _blank(normalised.get("category_id")):
        return "no_category"
    if "no_options" not in skip and not (normalised.get("variants") or []):
        return "no_options"
    if "no_weight" not in skip and not has_usable_weight(normalised):
        return "no_weight"
    return None


def has_usable_weight(normalised: dict) -> bool:
    """
    True when the weight came from somewhere real.

    Three sources count, and they are exactly the three that are not a guess:

      - the supplier declared it (weight_assumed is False);
      - a category answered, whether that is a number the client typed himself
        or a median the pool measured. Both set weight_category_id, and the
        learned one only answers when its own samples agree about the 2 kg
        line, so a category holding both a screw and a toolbox stays silent -
        which is his objection of 3 September, enforced.

    What does not count is the blanket light default. It sets weight_assumed
    with no category behind it, and it is the invented number this whole module
    exists to stop publishing.

    5 SEPTEMBER - and this is his instruction, not a relaxation of his rule.
    His objection on 3 September was to guessing which side of the 2 kg line a
    product falls, because that decides what the customer is charged for
    carriage. He has now answered that himself:

        "وافعل وزن المنتج على الموجود في 1688 او وهمي اكثر من 10 kg حتى اكمل
         اعداد الشحن المجاني من خلال لوحة التحكم"

    A made-up figure over 10 kg is over his line, so an unweighed product is
    filed as FREE shipping - the side where the customer is charged nothing -
    and the carriage is in the price already, from the carton. The guess he
    refused was one that could overcharge his customer. This one cannot.

    Set KDX_VIRTUAL_WEIGHT_KG=0 and the check goes back to refusing, in one
    restart, with no release.
    """
    if not normalised.get("weight_assumed"):
        return True
    if bool(str(normalised.get("weight_category_id") or "").strip()):
        return True
    import mapping
    return mapping.VIRTUAL_WEIGHT_KG > 0


def missing_after_translation(enriched: dict) -> str | None:
    """
    The one check that needs the translator's answer.

    It reads the NAME that would reach the shop, not the flag that says whether
    a translator was configured. Those are two different questions and only one
    of them is about the product: a run with no OpenAI key carries the Chinese
    title straight through, and the customer meets the same unreadable listing
    either way. What decides it is what the shopper would see.

    A Chinese name also cannot be compared - every rival title scores zero
    against it - so such a product would be priced by margin while the audit
    said it had been compared. Two reasons, one rejection.

    enrich.enrich already refuses a name that is nothing but Chinese, and
    strips Chinese out of the rest. This is the check for what gets past both.
    """
    if not _enabled() or "untranslated" in _skipped():
        return None
    import enrich as enrich_module

    for field in ("name_ar", "name_en"):
        value = str(enriched.get(field) or "").strip()
        if not value or enrich_module.has_cjk(value):
            return "untranslated"
    return None


def reason_ar(code: str) -> str:
    return REASONS.get(code, "معلومات المنتج غير مكتملة - لا يُنشر")

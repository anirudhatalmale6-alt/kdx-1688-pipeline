"""
Stage 4 + 5: clean the Chinese listing, then translate it.

One model call per product returns both languages as structured JSON, so the
cleaning and the translation can never disagree with each other. Brand names
are preserved verbatim; store names, promo copy, contact details and links are
stripped before anything is written to KDX.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4.1-mini"

# Option labels per translation call. See _translate_labels for why this is not
# "all of them".
LABELS_PER_CALL = int(os.environ.get("KDX_LABELS_PER_CALL", "40"))

SYSTEM_PROMPT = """You clean and translate product listings taken from 1688 for a Saudi online store.

Remove completely, never translate:
- shop or factory names, and phrases like 旗舰店 / 专营店 / 厂家直销
- any URL, WeChat/QQ/phone/contact detail
- promotional copy: discounts, coupons, "free shipping", "add me for a better price"
- decorative symbols, bracket spam like 【】★☆, and truncated or garbled fragments
- anything sexual, religious, weapon, drug, tobacco or counterfeit related

Keep and translate faithfully:
- what the product actually is, its material, dimensions, capacity, contents
- a brand already written in Latin letters stays EXACTLY as written (FaSoLa, Deli)
- a brand written in Chinese characters: use its official Latin name if it has
  one, otherwise leave the brand out. Never copy Chinese characters through.
- technical and commercial terms stay accurate (220V, 50/60Hz, cotton, nylon)

No Chinese character may appear anywhere in name_ar, name_en, description_ar or
description_en. A Saudi customer cannot read them.

Write like a real store, not like a translation. Arabic must read naturally to a
Saudi customer. Never invent a feature that is not in the source.

Return ONLY this JSON object:
{"name_ar":"","name_en":"","description_ar":"","description_en":"","keywords":[],"category":""}
name_* max 70 characters. description_* 2-4 clean sentences. keywords: 5-8 Arabic search terms."""


class EnrichError(RuntimeError):
    pass


def _strip_code_fence(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


TERMS_PROMPT = """You translate product option names from a Chinese wholesale listing for a Saudi store.

These are colour, style and size labels: 红色, 均码, XL, 30L, 款式A.

Rules:
- sizes that are already latin or numeric (S, M, XL, 42, 30L) stay exactly as they are
- colours and styles become the natural Saudi Arabic word, and plain English
- never invent an option that is not in the input, never merge two of them

Return ONLY a JSON object of the form
{"terms":{"<original>":{"en":"","ar":""}}}
with one entry for every original given, keys copied character for character."""


CATEGORIES_PROMPT = """You translate category names from the 1688 wholesale catalogue for a Saudi online store.

These are shop department names, not product titles: 女装, 家用电器, 五金、工具, 半身裙.

An input may arrive as a path, "服饰配件、饰品 > 饰品配件 > 水钻". Translate ONLY
the last segment; the earlier ones are there to tell you which meaning is
wanted. 水钻 under jewellery parts is a rhinestone, not a water drill.

Rules:
- use the word a Saudi shopper would see in a store menu, not a literal gloss
- keep it short: a category name, not a sentence, and no punctuation at the end
- a name joined by 、or / stays one category: translate it as one name
- never invent a category that is not in the input, never merge two of them
- if a name is a brand or a latin abbreviation, leave it as it is
- the key you return is the WHOLE input string, path and all, character for
  character, but the name you return is the last segment only

Return ONLY a JSON object of the form
{"terms":{"<original>":{"en":"","ar":""}}}
with one entry for every original given, keys copied character for character."""


def _chat(system: str, user: str, api_key: str | None, timeout: int) -> dict:
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise EnrichError("no API key configured")

    payload = {
        "model": MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))

    result = json.loads(_strip_code_fence(body["choices"][0]["message"]["content"]))
    result["_usage"] = body.get("usage", {})
    return result


# Chinese, Japanese and Korean blocks, plus the full-width punctuation that
# comes with them.
_CJK = re.compile(r"[⺀-鿿豈-﫿＀-￯]+")

# What is left dangling when a brand written in Chinese is lifted out of a
# sentence. "تيشيرت للأطفال من 可可鸭 بنمط كرتوني" must not become
# "تيشيرت للأطفال من بنمط كرتوني" - the preposition has to go with the brand.
_DANGLING = re.compile(
    r"(?:\s|^)(?:من|ماركة|علامة|from|by|brand)\s*$", re.IGNORECASE)


def has_cjk(text) -> bool:
    """True when a shopper would still be looking at Chinese."""
    return bool(_CJK.search(str(text or "")))


def strip_cjk(text: str) -> str:
    """
    Take the Chinese out of a line meant for a Saudi shopper.

    The prompt asks for this and the prompt is not a guarantee: on the first
    real night a published title read "تيشيرت بأكمام طويلة للأطفال من 可可鸭
    بنمط كرتوني". A model instruction is a request; this is the enforcement.
    """
    if not text:
        return text
    out = []
    last = 0
    for match in _CJK.finditer(text):
        head = text[last:match.start()]
        # Drop the preposition that introduced the brand, so the sentence
        # closes cleanly instead of trailing off.
        head = _DANGLING.sub("", head)
        out.append(head)
        last = match.end()
    out.append(text[last:])
    return " ".join("".join(out).split()).strip(" -،,·")


def enrich(title_zh: str, description_zh: str, api_key: str | None = None,
           timeout: int = 60) -> dict:
    result = _chat(SYSTEM_PROMPT,
                   f"TITLE:\n{title_zh}\n\nDESCRIPTION:\n{description_zh}",
                   api_key, timeout)

    for field in ("name_ar", "name_en", "description_ar", "description_en"):
        if not result.get(field):
            raise EnrichError(f"model returned an empty {field}")
        result[field] = strip_cjk(result[field])
        # Emptied by the strip means the model returned nothing but a Chinese
        # brand, which is not a product name. Better to fail the offer than to
        # publish a blank title.
        if not result[field]:
            raise EnrichError(f"{field} was nothing but Chinese text")

    return result


# What a 1688 seller joins the parts of a SKU label with. Kept in the split so
# the label can be put back together character for character.
#
# Every character in this list was put there by a measurement, not by taste. The
# 234 option names that had reached the shop in Chinese were run through the
# third pass after each addition: dash alone left 85 stuck, nearly all pneumatic
# valves written "4V310-10~优质款【AC220V】"; adding ~ left 59, nearly all model
# codes bracketed or spaced off from the words - "【ME-01】新升级版 光辉绿色".
#
# A single space counts. Chinese does not put spaces inside a phrase, so a space
# in one of these labels is the seller separating two things.
_SEPARATORS = re.compile(r"([-–—/／|｜+＋、,，~～【】]|\s+)")

# Half of these separators are themselves Chinese characters - 【 】 、 ， live in
# the CJK punctuation block - so putting a label back together with them still in
# it produces a string that is, correctly, judged to be Chinese and thrown away.
# That is not a hypothetical: adding 【 】 to the split above took the survivors
# from 59 back up to 132 until this map existed. The bracket is punctuation, not
# a word, so it is carried across rather than translated.
_AS_ASCII = {"【": "[", "】": "]", "／": "/", "｜": "|", "＋": "+", "～": "~",
             "、": ",", "，": ",", "–": "-", "—": "-"}


def _reassemble(term: str, named: dict, language: str) -> str:
    """Put a segmented label back together in the language asked for."""
    out = []
    for part in _SEPARATORS.split(term):
        if _SEPARATORS.fullmatch(part):
            out.append(_AS_ASCII.get(part, part))
            continue
        entry = named.get(part.strip()) or {}
        value = str(entry.get(language) or "").strip()
        out.append(value if value and not _CJK.search(value) else part)
    return "".join(out)


def _translate_labels(terms, prompt: str, api_key: str | None, timeout: int) -> dict:
    wanted = [str(term).strip() for term in terms if str(term).strip()]
    if not wanted:
        return {}

    def ask(subset: list) -> dict:
        # In batches, because one product can carry a lot of labels: the blind
        # box set his shop refused on 1 September had 146 colour options, and
        # asked for all 146 at once the model answered with far fewer than it
        # was given. Every label it omits keeps its Chinese, so a long list does
        # not fail loudly - it just publishes Chinese.
        answers: dict = {}
        for start in range(0, len(subset), LABELS_PER_CALL):
            window = subset[start:start + LABELS_PER_CALL]
            result = _chat(prompt, json.dumps(window, ensure_ascii=False),
                           api_key, timeout)
            answers.update(result.get("terms") or {})
        return answers

    translated = ask(wanted)

    out = {}
    for term in wanted:
        entry = translated.get(term) or {}
        out[term] = {"en": str(entry.get("en") or term).strip(),
                     "ar": str(entry.get("ar") or term).strip()}

    # The fallback above is "keep the Chinese", which is the right failure - a
    # size that vanishes is worse than a size in Chinese - but it is not the
    # right outcome. On 1 September 3 of 76 colour labels reached the shop in
    # Chinese because the model simply left those keys out of its answer, and
    # nothing noticed: every one of the 76 had an entry.
    #
    # So the labels that came back still Chinese are asked for again, by
    # themselves. A short list is easier for the model than a long one, and one
    # extra call for three labels is cheap. Anything still Chinese after this
    # keeps the original, deliberately.
    unresolved = [term for term, entry in out.items()
                  if _CJK.search(entry["ar"]) or _CJK.search(entry["en"])]
    if unresolved:
        try:
            second = ask(unresolved)
        except Exception:                                  # noqa: BLE001
            second = {}
        for term in unresolved:
            entry = second.get(term) or {}
            english, arabic = str(entry.get("en") or "").strip(), str(entry.get("ar") or "").strip()
            if english and not _CJK.search(english):
                out[term]["en"] = english
            if arabic and not _CJK.search(arabic):
                out[term]["ar"] = arabic

    # Third pass, by segment. A 1688 SKU label is often a whole specification
    # joined with dashes - "M005-单向推车-黑色-标配款-单手折叠（可坐可趟）" - and asked
    # for whole, the model hands it straight back unchanged, twice. That is not
    # a theory: on 2 September all nine options of a pushchair reached the
    # client's shopping cart in Chinese, and replaying those nine labels through
    # this function reproduced it exactly.
    #
    # Cut on the separators and the pieces are the ordinary colour and version
    # words this prompt was written for. They also repeat across the variants of
    # one product - nine labels here held eight distinct pieces - so the extra
    # call is small, and the label goes back together in its original shape.
    stuck = [term for term, entry in out.items()
             if _CJK.search(entry["ar"]) or _CJK.search(entry["en"])]
    if stuck:
        pieces = []
        for term in stuck:
            for piece in (part.strip() for part in _SEPARATORS.split(term)):
                # A separator can be a CJK character itself, so "is it Chinese"
                # is not enough to decide it is a word worth asking about.
                if _SEPARATORS.fullmatch(piece or " "):
                    continue
                if piece and _CJK.search(piece) and piece not in pieces:
                    pieces.append(piece)
        try:
            named = ask(pieces) if pieces else {}
        except Exception:                                  # noqa: BLE001
            named = {}
        for term in stuck:
            for language in ("en", "ar"):
                rebuilt = _reassemble(term, named, language)
                # Still Chinese means a piece went untranslated, and half a
                # label is worse than the whole one: keep the original so the
                # caller's own check still sees it for what it is.
                if rebuilt and not _CJK.search(rebuilt):
                    out[term][language] = rebuilt
    return out


def translate_terms(terms, api_key: str | None = None, timeout: int = 60) -> dict:
    """
    Translate the colour and size labels of one product in a single call.

    Returned as {original: {"en": ..., "ar": ...}}. A label the model omits or
    renames falls back to the original rather than disappearing: a size that
    silently vanishes from a product is worse than a size shown in Chinese.
    """
    return _translate_labels(terms, TERMS_PROMPT, api_key, timeout)


def translate_categories(terms, api_key: str | None = None, timeout: int = 90) -> dict:
    """
    Same contract as translate_terms, but prompted for shop department names.

    Separate from translate_terms because the two read very differently: "均码"
    is a size, "五金、工具" is a menu heading, and a prompt that does one well
    does the other badly.
    """
    return _translate_labels(terms, CATEGORIES_PROMPT, api_key, timeout)

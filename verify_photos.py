"""
Checks for the photograph guard and for reading his shop's answer.

    python3 verify_photos.py

No network: every response is a stub, including the ones that lie. The two
failures this exists to stop both happened for real on 2026-08-30 - twenty-one
products published with empty picture frames, and a run that reported all
twenty-one as published because nobody read what the shop said back.
"""

from __future__ import annotations

import io
import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import photos  # noqa: E402
import pipeline  # noqa: E402

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


class FakeResponse:
    def __init__(self, status=200, content_type="image/jpeg", body=b"\xff\xd8jpeg"):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self._body = body

    def read(self) -> bytes:
        # The checker reads the body now, so the Chinese-text scorer can look at
        # a photograph without downloading it a second time.
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """
    A stand-in for urlopen that answers per URL, and counts every call so a
    second look at the same URL is visible.
    """

    def __init__(self, answers: dict, default=200, bodies=None):
        self.answers = answers
        self.default = default
        self.calls = []
        # Two URLs are the same photograph only when a test says so. Answering
        # one fixed body for every URL would make the whole suite one photograph
        # repeated, and the content dedupe below would look like it worked when
        # it had simply been handed nothing to tell apart.
        self.bodies = dict(bodies or {})

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.calls.append((url, dict(getattr(request, "headers", {}))))
        answer = self.answers.get(url, self.default)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            status, kind = answer
        else:
            status, kind = answer, "image/jpeg"
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "no", {}, io.BytesIO(b""))
        return FakeResponse(status, kind,
                            self.bodies.get(url, b"\xff\xd8jpeg" + url.encode()))


GOOD = "https://cbu01.alicdn.com/img/ibank/good.jpg"
GONE = "https://cbu01.alicdn.com/img/ibank/gone.jpg"
HTML = "https://cbu01.alicdn.com/img/ibank/html.jpg"
SLOW = "https://cbu01.alicdn.com/img/ibank/slow.jpg"
BLANK = "https://cbu01.alicdn.com/img/ibank/blank.jpg"


def placeholder_bytes() -> bytes:
    """
    The actual blank the client photographed, not an imitation of one.

    O1CN01K4bsgT20A7ZQywdag_!!2220793886808-0-cib.jpg_800x800.jpg - 7,784 bytes,
    sha256 155edfed73f38e4b..., every pixel 255,255,255 - kept base64 in
    samples/ rather than as a .jpg because .gitignore excludes image files to
    stop the client's console screenshots from ever being committed, and that
    rule is worth more than the convenience of a second file extension.
    """
    import base64
    path = os.path.join(HERE, "samples", "blank_placeholder.jpg.b64")
    with open(path, "r", encoding="utf-8") as handle:
        return base64.b64decode(handle.read())


def drawn(colour=(255, 255, 255), mark=True) -> bytes:
    """An 800x800 JPEG, with a shape on it unless asked for a flat field."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (800, 800), colour)
    if mark:
        ImageDraw.Draw(image).ellipse((200, 200, 600, 600), fill=(30, 60, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def main() -> int:
    print("a photograph that answers is kept, one that does not is not")
    opener = FakeOpener({GOOD: 200, GONE: 404})
    checker = photos.PhotoChecker(opener=opener)
    check("a 200 image is reachable", checker.reachable(GOOD) is True)
    check("a 404 is not", checker.reachable(GONE) is False)
    check("an empty URL is not, without a request",
          checker.reachable("") is False and len(opener.calls) == 2)

    print("\na 200 that IS an image can still be an empty frame")
    # 4 September: the client sent a screenshot of a product in his shop with a
    # white square where the picture should be, and asked whether it was his
    # shop's fault. It was not. The URL answered 200 with a real 800x800 JPEG in
    # which every pixel was 255,255,255 - one placeholder shared by five
    # unrelated offers among the 2,906 images published up to that day.
    real_blank = placeholder_bytes()
    check("the placeholder really is flat, which is what this whole rule rests on",
          photos.spread_of(real_blank) == 0, str(photos.spread_of(real_blank)))
    opener = FakeOpener({BLANK: 200, GOOD: 200},
                        bodies={BLANK: real_blank, GOOD: drawn()})
    checker = photos.PhotoChecker(opener=opener)
    check("it is reachable - that was never the problem",
          checker.reachable(BLANK) is True)
    check("and it is refused anyway, because there is no photograph in it",
          checker.blank(BLANK) is True)
    check("so keep() drops it", checker.keep([BLANK]) == [])
    check("CONTROL a real photograph on a white background is kept",
          checker.keep([GOOD]) == [GOOD])
    check("CONTROL and is not called blank", checker.blank(GOOD) is False)
    check("the count is reported, not silent",
          checker.summary()["urls_blank"] == 1, str(checker.summary()))

    # CONTROL colour-blind: the rule is "no picture", not "not white". A flat
    # black or flat grey field is equally empty and the next supplier should not
    # need a new rule.
    for name, colour in (("black", (0, 0, 0)), ("grey", (128, 128, 128))):
        url = f"https://cbu01.alicdn.com/img/ibank/flat-{name}.jpg"
        flat = photos.PhotoChecker(
            opener=FakeOpener({url: 200}, bodies={url: drawn(colour, mark=False)}))
        flat.reachable(url)
        check(f"CONTROL a flat {name} field is empty too", flat.blank(url) is True)

    # CONTROL not having looked is not evidence. Bytes it could not decode, and
    # bytes it never kept, are both "no opinion" - never "blank".
    junk = photos.PhotoChecker(opener=FakeOpener({GOOD: 200}))
    junk.reachable(GOOD)
    check("CONTROL bytes that are not an image at all are not called blank",
          junk.blank(GOOD) is False)
    check("CONTROL and spread_of says so by refusing to answer",
          photos.spread_of(b"\xff\xd8not-a-jpeg") is None)
    starved = photos.PhotoChecker(
        opener=FakeOpener({BLANK: 200}, bodies={BLANK: real_blank}), keep_bytes=0)
    starved.reachable(BLANK)
    check("CONTROL a photograph whose bytes the budget refused to keep is not "
          "called blank on no evidence", starved.blank(BLANK) is False)

    print("\na product left with nothing but blanks is held, and says why")
    payload = {"images": [BLANK], "variants": []}
    held = photos.PhotoChecker(opener=FakeOpener({BLANK: 200},
                                                 bodies={BLANK: real_blank}))
    report = photos.prune(payload, held)
    check("the blank is pruned out of the gallery", payload["images"] == [])
    check("and the report separates blank from dead, because a dead URL might "
          "work next time and a blank one never will",
          report["blank"] == [BLANK] and report["kept"] == 0, str(report))
    mixed = {"images": [BLANK, GOOD], "variants": [{"image": BLANK,
                                                    "images": [BLANK, GOOD]}]}
    both = photos.PhotoChecker(
        opener=FakeOpener({BLANK: 200, GOOD: 200},
                          bodies={BLANK: real_blank, GOOD: drawn()}))
    photos.prune(mixed, both)
    check("CONTROL a product that also has a real photograph keeps it and is "
          "published, not thrown away",
          mixed["images"] == [GOOD], str(mixed["images"]))
    check("CONTROL and the variant swatch moves off the blank too",
          mixed["variants"][0]["image"] == GOOD
          and mixed["variants"][0]["images"] == [GOOD],
          str(mixed["variants"][0]))

    print("\na 200 that is not an image is not a photograph")
    # His importer converts to webp. Handed an HTML error page with a 200 it
    # would store a broken file, and the empty frame would look like our bug.
    opener = FakeOpener({HTML: (200, "text/html; charset=utf-8")})
    checker = photos.PhotoChecker(opener=opener)
    check("a 200 text/html URL is refused", checker.reachable(HTML) is False)

    print("\nno Referer is sent")
    # Measured on 2026-08-30: alicdn answers 403 to a request carrying
    # `Referer: https://kdx-sa.com/` and 200 to one carrying none. His server
    # sends none, so the check must send none - otherwise the guard would
    # reject every photograph his shop can actually fetch.
    opener = FakeOpener({GOOD: 200})
    photos.PhotoChecker(opener=opener).reachable(GOOD)
    headers = {k.lower(): v for k, v in opener.calls[0][1].items()}
    check("the check does not pretend to be a browser on his domain",
          "referer" not in headers, str(headers))

    print("\nthe same URL is asked about once")
    opener = FakeOpener({GOOD: 200})
    checker = photos.PhotoChecker(opener=opener)
    for _ in range(5):
        checker.reachable(GOOD)
    check("five asks, one request", len(opener.calls) == 1, str(len(opener.calls)))
    check("and the summary counts URLs, not asks",
          checker.summary() == {"urls_checked": 1, "urls_dead": 0,
                                "urls_blank": 0},
          str(checker.summary()))

    print("\na timeout is retried, a 404 is not")
    opener = FakeOpener({SLOW: TimeoutError("slow")})
    photos.PhotoChecker(opener=opener, attempts=2).reachable(SLOW)
    check("a timeout gets a second attempt", len(opener.calls) == 2,
          str(len(opener.calls)))
    opener = FakeOpener({GONE: 404})
    photos.PhotoChecker(opener=opener, attempts=3).reachable(GONE)
    check("a 404 does not", len(opener.calls) == 1, str(len(opener.calls)))

    print("\npruning reaches every place a photograph hides")
    payload = {
        "images": [GOOD, GONE],
        "variants": [
            {"original": "red", "image": GONE, "images": [GONE, GOOD]},
            {"original": "blue", "image": GONE, "images": [GONE]},
        ],
    }
    checker = photos.PhotoChecker(opener=FakeOpener({GOOD: 200, GONE: 404}))
    report = photos.prune(payload, checker)
    check("the gallery loses the dead one", payload["images"] == [GOOD],
          str(payload["images"]))
    check("the report says what was dropped",
          report == {"had": 2, "kept": 1, "dropped": [GONE], "blank": []},
          str(report))
    check("and a dead URL is not filed as a blank one - they are different "
          "faults with different answers", report["blank"] == [])
    # A variant is a colour swatch on his page. Left pointing at a dead URL it
    # renders an empty frame next to a live price, which is the same defect
    # one level down.
    check("a variant's gallery is pruned too",
          payload["variants"][0]["images"] == [GOOD],
          str(payload["variants"][0]["images"]))
    check("a variant's main photo falls back to a live one",
          payload["variants"][0]["image"] == GOOD,
          str(payload["variants"][0]["image"]))
    check("a variant with nothing left is emptied, not left lying",
          payload["variants"][1]["image"] == ""
          and payload["variants"][1]["images"] == [],
          str(payload["variants"][1]))

    print("\nthe same photograph under two URLs is published once")
    # The client's report of 2 September: repeated pictures on a product page.
    # 1688 serves one photograph from more than one path, so deduplicating the
    # URLs - which mapping.py already did - never caught it. Measured on 12
    # published products that day, 3 of 91 photographs were a second copy.
    TWIN = "https://cbu01.alicdn.com/img/ibank/good-again.jpg"
    OTHER = "https://cbu01.alicdn.com/img/ibank/other.jpg"
    same = b"\xff\xd8one-and-the-same"
    payload = {
        "images": [GOOD, TWIN, OTHER],
        "variants": [{"original": "red", "image": GOOD, "images": [GOOD, TWIN]}],
    }
    opener = FakeOpener({GOOD: 200, TWIN: 200, OTHER: 200},
                        bodies={GOOD: same, TWIN: same})
    checker = photos.PhotoChecker(opener=opener)
    photos.prune(payload, checker)
    check("the second copy is dropped from the gallery",
          payload["images"] == [GOOD, OTHER], str(payload["images"]))
    # CONTROL. A photograph that only looks similar is a different photograph,
    # and dropping it would cost the shopper a real view of the product.
    check("CONTROL a genuinely different photograph is kept",
          OTHER in payload["images"], str(payload["images"]))
    check("and the colour swatch loses its copy too",
          payload["variants"][0]["images"] == [GOOD],
          str(payload["variants"][0]["images"]))
    check("CONTROL the swatch still points at a photograph that survived",
          payload["variants"][0]["image"] == GOOD,
          str(payload["variants"][0]["image"]))

    # CONTROL for the instrument itself. Without bytes there is no fingerprint,
    # and no fingerprint must mean "keep", never "assume duplicate" - otherwise
    # a checker over its memory budget would silently strip whole galleries.
    starved = photos.PhotoChecker(opener=FakeOpener({GOOD: 200, TWIN: 200}),
                                  keep_bytes=0)
    thin = {"images": [GOOD, TWIN], "variants": []}
    photos.prune(thin, starved)
    check("CONTROL with no bytes kept, nothing is called a duplicate",
          thin["images"] == [GOOD, TWIN], str(thin["images"]))

    print("\na product with no photograph left is not published")
    payload = {"images": [GONE], "variants": []}
    checker = photos.PhotoChecker(opener=FakeOpener({GONE: 404}))
    report = photos.prune(payload, checker)
    check("prune empties the gallery", payload["images"] == [])
    check("and says none survived", report["kept"] == 0, str(report))

    print("\nthe answer from his shop is read, not assumed")
    # Every one of these came back with HTTP 200 from the live endpoint.
    ok = {"success": True, "imported_count": 1, "failed_count": 0,
          "skipped_count": 0, "failed_items": []}
    check("a real import is silent", pipeline._publish_trouble(ok) == "")
    # Before 30 August skipped_count meant "already there". Since his developer
    # made the route upsert it means his shop declined the product for a reason
    # of its own, so the message carries that reason instead of my old guess.
    skipped = dict(ok, imported_count=0, skipped_count=1, message="duplicate sku")
    check("success:true with skipped_count=1 is trouble",
          "duplicate sku" in pipeline._publish_trouble(skipped),
          pipeline._publish_trouble(skipped))
    updated = dict(ok, imported_count=0, updated_count=1)
    check("an update is NOT trouble, now the route upserts",
          pipeline._publish_trouble(updated) == "",
          pipeline._publish_trouble(updated))
    failed = dict(ok, imported_count=0, failed_count=1,
                  failed_items=[{"source_offer_id": "1", "error": "bad"}])
    check("failed_count is trouble",
          "rejected" in pipeline._publish_trouble(failed),
          pipeline._publish_trouble(failed))
    check("success:false is trouble",
          pipeline._publish_trouble({"success": False, "message": "no"}) != "")
    check("imported nothing at all is trouble",
          pipeline._publish_trouble(dict(ok, imported_count=0)) != "",
          pipeline._publish_trouble(dict(ok, imported_count=0)))
    check("no answer at all is not invented into one",
          pipeline._publish_trouble({}) == "")

    print("\n'received, processing' is an acknowledgement, not a result")
    # His developer moved the import to a background job between 07:58 and 08:05
    # on 4 September. This is the reply, verbatim from the run log. The old rule
    # read it as "nothing landed" and six consecutive batches published ZERO
    # while his shop was in fact filling up - and a product recorded as
    # not-published can be selected and pushed AGAIN, which is how one product
    # becomes two.
    ACK = {"success": True,
           "message": "تم استلام البيانات بنجاح، وجاري معالجتها وإدخالها في الخلفية."}
    check("his background reply is recognised as an acknowledgement",
          pipeline.is_acknowledgement(ACK) is True)
    check("and is therefore not counted as trouble",
          pipeline._publish_trouble(ACK) == "", pipeline._publish_trouble(ACK))

    # CONTROL the test is the ABSENCE of every counter, never the wording. A
    # message in any language, or none, reads the same way.
    check("CONTROL a bare success with no counters is the same thing",
          pipeline.is_acknowledgement({"success": True}) is True)

    # CONTROL a reply that DOES carry counters is still read exactly as before,
    # so a genuine zero remains trouble rather than being excused as async.
    for name, response in (
            ("imported_count 0", dict(ok, imported_count=0)),
            ("skipped_count 1", dict(ok, imported_count=0, skipped_count=1)),
            ("failed_count 1", dict(ok, imported_count=0, failed_count=1))):
        check(f"CONTROL {name} is a RESULT, not an acknowledgement",
              pipeline.is_acknowledgement(response) is False)
        check(f"CONTROL {name} is still trouble",
              pipeline._publish_trouble(response) != "")
    check("CONTROL a real import is not an acknowledgement either",
          pipeline.is_acknowledgement(ok) is False)
    check("CONTROL success:false is never an acknowledgement, whatever it omits",
          pipeline.is_acknowledgement({"success": False, "message": "no"}) is False)
    check("CONTROL and no answer at all is not one",
          pipeline.is_acknowledgement({}) is False)

    print("\nthe response list is unwrapped without guessing")
    check("one batch, one response", pipeline._one_response([ok]) is ok)
    check("an empty list is an empty dict", pipeline._one_response([]) == {})
    check("a list of something else does not explode",
          pipeline._one_response(["oops"]) == {})

    print("\nno Chinese survives into a title a Saudi customer reads")
    # A real published title on the first live night:
    # "تيشيرت بأكمام طويلة للأطفال من 可可鸭 بنمط كرتوني".
    import enrich  # noqa: E402
    stripped = enrich.strip_cjk("تيشيرت بأكمام طويلة للأطفال من 可可鸭 بنمط كرتوني")
    check("the Chinese brand is gone", "可可鸭" not in stripped, stripped)
    check("and the preposition that introduced it goes with it",
          stripped == "تيشيرت بأكمام طويلة للأطفال بنمط كرتوني", stripped)
    check("the same in English", enrich.strip_cjk(
        "Kids Long Sleeve T-shirt by 可可鸭 Cartoon Style")
        == "Kids Long Sleeve T-shirt Cartoon Style")
    # CONTROL: a Latin brand is a brand the customer can read, and the client
    # asked for brands to be kept.
    check("CONTROL a Latin brand is untouched",
          enrich.strip_cjk("غطاء قدم من FaSoLa") == "غطاء قدم من FaSoLa")
    check("CONTROL an ordinary Arabic title is untouched",
          enrich.strip_cjk("حجر زجاجي ملون بشكل مخلب") == "حجر زجاجي ملون بشكل مخلب")
    check("full-width bracket spam goes too",
          enrich.strip_cjk("كرسي مكتب 【爆款】 مريح") == "كرسي مكتب مريح",
          enrich.strip_cjk("كرسي مكتب 【爆款】 مريح"))
    check("a name that was nothing but Chinese comes back empty, not blank-padded",
          enrich.strip_cjk("可可鸭") == "")
    check("CONTROL an empty input stays empty rather than raising",
          enrich.strip_cjk("") == "")

    print("\nthe check can be switched off, and off means off")
    # A network that cannot reach alicdn at all would otherwise hold the whole
    # catalogue and read like a pricing bug.
    keep = os.environ.get("KDX_CHECK_IMAGES")
    try:
        for value, expected in (("0", False), ("false", False), ("off", False),
                                ("no", False), ("1", True), ("", True)):
            os.environ["KDX_CHECK_IMAGES"] = value
            import importlib
            importlib.reload(photos)
            check(f"KDX_CHECK_IMAGES={value!r} -> enabled={expected}",
                  photos.ENABLED is expected, str(photos.ENABLED))
    finally:
        if keep is None:
            os.environ.pop("KDX_CHECK_IMAGES", None)
        else:
            os.environ["KDX_CHECK_IMAGES"] = keep
        import importlib
        importlib.reload(photos)

    print("\nthe shop is sent the display-size copy, where there is one")
    small = photos.display_url(GOOD)
    check("the CDN is asked for the size his shop shows",
          small == GOOD + f"_{photos.DISPLAY_PX}x{photos.DISPLAY_PX}.jpg", small)
    check("CONTROL asking twice does not stack suffixes",
          photos.display_url(small) == small, photos.display_url(small))
    check("CONTROL a URL that is not on the CDN is left alone",
          photos.display_url("https://example.com/a.jpg")
          == "https://example.com/a.jpg")
    check("CONTROL an empty URL stays empty", photos.display_url("") == "")

    payload = {"images": [GOOD, GONE],
               "variants": [{"image": GONE, "images": [GONE, GOOD]}]}
    opener = FakeOpener({}, default=200)
    checker = photos.PhotoChecker(opener=opener)
    changed = photos.resize_for_display(payload, checker)
    check("every photograph in the gallery is swapped", payload["images"] ==
          [photos.display_url(GOOD), photos.display_url(GONE)], str(payload["images"]))
    check("and the colour's photographs too",
          payload["variants"][0]["images"]
          == [photos.display_url(GONE), photos.display_url(GOOD)])
    check("the swatch follows its own gallery",
          payload["variants"][0]["image"] == photos.display_url(GONE))
    check("and the count is reported", changed == 4, str(changed))

    # The one that matters: a small copy that does not exist must not take a
    # photograph away. His importer copies the picture once and never again.
    payload = {"images": [GOOD, GONE], "variants": [{"image": GOOD, "images": [GOOD]}]}
    opener = FakeOpener({photos.display_url(GOOD): 404,
                         photos.display_url(GONE): 200}, default=200)
    checker = photos.PhotoChecker(opener=opener)
    photos.resize_for_display(payload, checker)
    check("a display copy that 404s leaves the original in place",
          payload["images"][0] == GOOD, payload["images"][0])
    check("CONTROL while the one that answers is still swapped",
          payload["images"][1] == photos.display_url(GONE), payload["images"][1])
    check("CONTROL and the swatch keeps the original it can still show",
          payload["variants"][0]["image"] == GOOD,
          payload["variants"][0]["image"])
    check("CONTROL no photograph is lost to the swap",
          len(payload["images"]) == 2 and len(payload["variants"][0]["images"]) == 1)

    # And the whole thing must be switchable off, reproducing what shipped
    # before it existed.
    keep_px = os.environ.get("KDX_IMAGE_DISPLAY_PX")
    os.environ["KDX_IMAGE_DISPLAY_PX"] = "0"
    try:
        import importlib
        importlib.reload(photos)
        payload = {"images": [GOOD], "variants": [{"image": GOOD, "images": [GOOD]}]}
        checker = photos.PhotoChecker(opener=FakeOpener({}, default=200))
        moved = photos.resize_for_display(payload, checker)
        check("CONTROL set to 0 the original URLs ship untouched",
              payload["images"] == [GOOD] and moved == 0, str(payload["images"]))
        check("CONTROL and no request is made for a smaller copy",
              photos.display_url(GOOD) == GOOD)
    finally:
        if keep_px is None:
            os.environ.pop("KDX_IMAGE_DISPLAY_PX", None)
        else:
            os.environ["KDX_IMAGE_DISPLAY_PX"] = keep_px
        import importlib
        importlib.reload(photos)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

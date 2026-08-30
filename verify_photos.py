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

    def __init__(self, answers: dict, default=200):
        self.answers = answers
        self.default = default
        self.calls = []

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
        return FakeResponse(status, kind)


GOOD = "https://cbu01.alicdn.com/img/ibank/good.jpg"
GONE = "https://cbu01.alicdn.com/img/ibank/gone.jpg"
HTML = "https://cbu01.alicdn.com/img/ibank/html.jpg"
SLOW = "https://cbu01.alicdn.com/img/ibank/slow.jpg"


def main() -> int:
    print("a photograph that answers is kept, one that does not is not")
    opener = FakeOpener({GOOD: 200, GONE: 404})
    checker = photos.PhotoChecker(opener=opener)
    check("a 200 image is reachable", checker.reachable(GOOD) is True)
    check("a 404 is not", checker.reachable(GONE) is False)
    check("an empty URL is not, without a request",
          checker.reachable("") is False and len(opener.calls) == 2)

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
          checker.summary() == {"urls_checked": 1, "urls_dead": 0},
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
          report == {"had": 2, "kept": 1, "dropped": [GONE]}, str(report))
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
          pipeline._publish_trouble({"success": True}) != "")
    check("no answer at all is not invented into one",
          pipeline._publish_trouble({}) == "")

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

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

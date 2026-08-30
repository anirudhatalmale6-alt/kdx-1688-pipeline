"""
Checks for the Chinese-writing-in-the-photograph scorer.

    python3 verify_imagetext.py

The interesting checks are the two real photographs at the end: one plain
product shot and one supplier poster, both drawn on the fly with Pillow to the
same shapes the measured pair had, plus - when the real files are present in
samples/photos/ - the actual alicdn images the client complained about. A
scorer that cannot tell those two apart is worthless however many unit checks
pass, and a scorer with no positive control is a function that returns zero.
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import imagetext  # noqa: E402

PASS = FAIL = SKIP = 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def note(label: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  skip  {label}")


class FakeChecker:
    def __init__(self, bodies=None):
        self.bodies = bodies or {}
        self.asked = []

    def body(self, url: str) -> bytes:
        self.asked.append(url)
        return self.bodies.get(url, b"")


def stub_scores(mapping: dict):
    """Replace the OCR with a lookup, so the ordering can be checked on its own."""
    original = imagetext.score_gallery

    def fake(urls, _checker):
        return [(url, mapping.get(url)) for url in (urls or [])]

    imagetext.score_gallery = fake
    return original


def main() -> int:
    global PASS, FAIL
    print("ordering a gallery, cleanest photograph first")

    original = stub_scores({"a": 6.1, "b": 0.0, "c": 0.4})
    try:
        ranked = imagetext.order_gallery(["a", "b", "c"], None)
        check("the poster goes last", ranked["images"], ["b", "c", "a"])
        check("nothing is dropped while no threshold is set", ranked["dropped"], [])
        check("every score is reported", ranked["scores"],
              {"a": 6.1, "b": 0.0, "c": 0.4})

        ranked = imagetext.order_gallery(["a", "b", "c"], None, max_percent=2.0)
        check("above the threshold the poster is dropped", ranked["images"], ["b", "c"])
        check("and it is named", ranked["dropped"], ["a"])

        # CONTROL: dropping must never leave a product with nothing to show.
        # An ugly photograph beats an empty frame, and today this channel gives
        # exactly one photograph per offer.
        ranked = imagetext.order_gallery(["a"], None, max_percent=2.0)
        check("the last photograph is kept even though it is a poster",
              ranked["images"], ["a"])
        check("and it is not reported as dropped", ranked["dropped"], [])
    finally:
        imagetext.score_gallery = original

    original = stub_scores({"a": None, "b": 5.0, "c": None, "d": 0.1})
    try:
        ranked = imagetext.order_gallery(["a", "b", "c", "d"], None, max_percent=2.0)
        check("an unmeasured photograph keeps the supplier's order among equals",
              ranked["images"], ["a", "c", "d"])
        check("and is never dropped for not being measured",
              "a" in ranked["images"] and "c" in ranked["images"], True)
        check("only the measured poster is dropped", ranked["dropped"], ["b"])
    finally:
        imagetext.score_gallery = original

    check("an empty gallery is not a crash",
          imagetext.order_gallery([], None), {"images": [], "scores": {}, "dropped": []})

    print("\nthe bytes come from the check that already downloaded them")
    checker = FakeChecker({"x": b""})
    original = imagetext.text_percent
    imagetext.text_percent = lambda data: 1.5
    try:
        scored = imagetext.score_gallery(["x", "x", "y"], checker)
        check("each distinct url is asked about once", checker.asked, ["x", "y"])
        check("and the repeat reuses the answer", [p for _u, p in scored], [1.5, 1.5, 1.5])
    finally:
        imagetext.text_percent = original

    print("\nreading real photographs")
    if not imagetext.available():
        note(f"tesseract with {imagetext.LANGUAGE} is not installed here, so "
             f"nothing can be scored - and that is exactly why a missing "
             f"tesseract must not filter anything")
        check("with no OCR the score is None, not zero",
              imagetext.text_percent(b"\x89PNG\r\n\x1a\n"), None)
        check("and None never drops a photograph",
              imagetext.order_gallery(["a"], FakeChecker(), 2.0)["dropped"], [])
    else:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            note("Pillow is not installed, so the drawn controls are skipped")
            Image = None

        if Image is not None:
            font = None
            for candidate in ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                              "/usr/share/fonts/truetype/arphic/uming.ttc"):
                if os.path.exists(candidate):
                    font = ImageFont.truetype(candidate, 96)
                    break

            def render(text: str) -> bytes:
                image = Image.new("RGB", (800, 800), "white")
                draw = ImageDraw.Draw(image)
                draw.ellipse((250, 250, 550, 550), fill=(180, 60, 60))
                if text and font is not None:
                    draw.text((60, 620), text, fill="black", font=font)
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=92)
                return buffer.getvalue()

            clean = imagetext.text_percent(render(""))
            check("a photograph with no writing measures zero", clean, 0.0)

            if font is None:
                note("no CJK font on this machine, so the positive control "
                     "cannot be drawn - a zero above proves nothing on its own")
            else:
                poster = imagetext.text_percent(render("好棉好柔软"))
                print(f"        measured: clean {clean}%, poster {poster}%")
                check("a photograph with a Chinese caption measures more than the "
                      "clean one", poster is not None and poster > clean, True)

        # The real pair, when they have been kept alongside the repo.
        here = os.path.dirname(os.path.abspath(__file__))
        pairs = [("clean.jpg", "under", 2.0), ("poster.jpg", "over", 2.0)]
        folder = os.path.join(here, "samples", "photos")
        for name, side, limit in pairs:
            path = os.path.join(folder, name)
            if not os.path.exists(path):
                note(f"samples/photos/{name} is not in the repo (client photos "
                     f"are not committed), so this control did not run")
                continue
            with open(path, "rb") as handle:
                score = imagetext.text_percent(handle.read())
            print(f"        {name} measured {score}%")
            check(f"{name} measures {side} {limit}%",
                  score is not None and ((score < limit) if side == "under"
                                         else (score > limit)), True)

    print(f"\n{PASS} passed, {FAIL} failed, {SKIP} skipped")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

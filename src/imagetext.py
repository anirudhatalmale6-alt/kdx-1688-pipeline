"""
Chinese writing printed inside the photograph itself.

The client raised this on 2026-08-30: some supplier photos are half marketing
poster - 萌趣刺绣 / 好棉好柔软 across the bottom, a 100%棉 badge in the corner.
Nothing in the text pipeline can touch that; the characters are pixels.

What CAN be done is choose. A 1688 offer usually carries five to ten
photographs and only one or two of them are posters, so the fix is to put the
clean ones first and, if the client wants, leave the posters out. That is what
this module is for: it scores one photograph by how much of its area is covered
by Chinese characters, and orders a gallery cleanest-first.

Measured on 2026-08-30 against twelve photographs from the 30 August run, using
tesseract's own confidence rather than a bare character count - a bare count
reported Chinese on clean product shots, because OCR invents characters out of
folds and shadows:

    conf >= 70, chi_sim, psm 6
    ten clean product shots           0.00 - 0.83 % of the image
    the poster he complained about    6.11 %, and the text it read was real:
                                      立体装饰 / 萌趣刺绣 / 好棉好柔软

So the two populations are an order of magnitude apart and a threshold between
them is meaningful rather than fitted.

Two safety rules, both deliberate:

  * with no tesseract installed the score is None, and None never filters
    anything. A missing dependency must not silently empty the catalogue.
  * scoring never removes the last photograph. A product with one poster photo
    keeps that poster: an ugly picture is better than an empty frame, and today
    - until the detail permission lands - one photograph is all this channel
    gives, so the ordering below only starts paying once there is a gallery to
    order.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile

CJK = re.compile(r"[一-鿿]")

# Below this confidence tesseract is guessing at texture, not reading text.
MIN_CONFIDENCE = float(os.environ.get("KDX_OCR_MIN_CONFIDENCE", "70"))

# Percent of the image area that may be Chinese writing before the photograph
# counts as a poster. 0 disables the whole thing, which is the default until the
# client picks a number: measured, the two populations sit at <1 % and >6 %.
MAX_TEXT_PERCENT = float(os.environ.get("KDX_MAX_CJK_TEXT_PCT", "0"))

LANGUAGE = os.environ.get("KDX_OCR_LANGUAGE", "chi_sim")
TIMEOUT = int(os.environ.get("KDX_OCR_TIMEOUT", "30"))


def available() -> bool:
    """Is there a tesseract with the Chinese model? Nothing filters without one."""
    binary = shutil.which("tesseract")
    if not binary:
        return False
    try:
        langs = subprocess.run([binary, "--list-langs"], capture_output=True,
                               text=True, timeout=TIMEOUT).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return LANGUAGE in langs.split()


def _size(data: bytes) -> tuple:
    try:
        from PIL import Image
    except ImportError:
        return (0, 0)
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001
        return (0, 0)


def text_percent(data: bytes) -> float | None:
    """
    Percentage of the photograph covered by confidently-read Chinese text.

    None means "not measured" - no tesseract, no Pillow, unreadable bytes - and
    is deliberately different from 0.0, which means "measured, and clean".
    """
    if not data or not available():
        return None
    width, height = _size(data)
    if not width or not height:
        return None

    handle, path = tempfile.mkstemp(suffix=".img")
    try:
        with os.fdopen(handle, "wb") as image_file:
            image_file.write(data)
        try:
            result = subprocess.run(
                ["tesseract", path, "stdout", "-l", LANGUAGE, "--psm", "6", "tsv"],
                capture_output=True, text=True, timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    covered = 0
    reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t",
                            quoting=csv.QUOTE_NONE)
    for row in reader:
        try:
            confidence = float(row.get("conf") or -1)
            box = int(row["width"]) * int(row["height"])
        except (TypeError, ValueError, KeyError):
            continue
        if confidence >= MIN_CONFIDENCE and CJK.search((row.get("text") or "")):
            covered += box
    return round(100.0 * covered / (width * height), 2)


def _fetch(url: str) -> bytes:
    """Last resort when the reachability check did not keep the bytes."""
    import urllib.request

    import photos
    try:
        request = urllib.request.Request(url, headers=photos.HEADERS, method="GET")
        with urllib.request.urlopen(request, timeout=photos.TIMEOUT) as response:
            return response.read()
    except Exception:  # noqa: BLE001
        return b""


def score_gallery(urls, checker) -> list:
    """
    [(url, percent_or_None)] in the order given, scoring each once.

    The bytes come from the reachability check that has already run, so a
    photograph is downloaded once per night and not once per question. Only when
    that check did not keep them - its cache is bounded - is a copy fetched.
    """
    scored, seen = [], {}
    for url in urls or []:
        if url not in seen:
            data = checker.body(url) if checker is not None else b""
            if not data:
                data = _fetch(url)
            seen[url] = text_percent(data)
        scored.append((url, seen[url]))
    return scored


def order_gallery(urls, checker, max_percent: float | None = None) -> dict:
    """
    Put the clean photographs first, and optionally drop the posters.

    Returns {"images": [...], "scores": {url: percent}, "dropped": [...]}.

    An unscored photograph keeps its place rather than being pushed to either
    end: not knowing is not evidence of a poster, and it is certainly not
    evidence of a clean shot.
    """
    limit = MAX_TEXT_PERCENT if max_percent is None else max_percent
    scored = score_gallery(urls, checker)
    if not scored:
        return {"images": [], "scores": {}, "dropped": []}

    # A photograph that was never measured sorts as if it were clean, so it
    # neither jumps the queue nor is treated as a poster. Python's sort is
    # stable, so equal scores keep the supplier's own order - which matters,
    # because the supplier's first photograph is usually the main product shot.
    def rank(pair):
        _url, percent = pair
        return 0.0 if percent is None else percent

    ordered = [pair[0] for pair in sorted(scored, key=rank)]

    kept = ordered
    dropped: list = []
    if limit > 0:
        scores = dict(scored)
        clean = [url for url in ordered
                 if scores.get(url) is None or scores[url] <= limit]
        # Never leave a product with no photograph at all: an ugly picture beats
        # an empty frame, and holding the product would cost the client a sale
        # over a caption.
        if clean:
            dropped = [url for url in ordered if url not in clean]
            kept = clean
    return {"images": kept, "scores": dict(scored), "dropped": dropped}

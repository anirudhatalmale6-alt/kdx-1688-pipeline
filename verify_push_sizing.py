"""
Proof for the two things that lost a product on 1 September, and for the fix.

    python3 verify_push_sizing.py     # offline, no credentials, nothing sent

What happened: a product with 146 colour options, each with its own photograph,
was refused three times with "The read operation timed out" and never reached
the shop. What was measured afterwards, against his live endpoint:

    10 photographs   11.5 s
    34 photographs   32.2 s
    34 again         34.3 s      <- no faster the second time

and, from a failure his server reported in full,

    delete from `product_images` where `id` = 59536

Three facts follow, and every check below is one of them:

  1. the cost is the number of PHOTOGRAPHS, not the size of the JSON. The first
     attempt at a fix cut the gallery to five and still timed out, because the
     146 variant photographs travelled with it. So the batcher counts
     photographs, including the ones hanging off variants.
  2. chunks cannot accumulate. His import deletes the photograph set before
     writing the new one, so a second chunk would erase the first - one product
     goes in one request, and a product bigger than the whole budget travels
     alone rather than being split.
  3. a timeout must not be retried. His server starts the same downloads again
     while the first attempt is still running; that is how one slow product
     became 142 seconds of waiting.

The photograph check itself is here too, because it now runs concurrently and
the only thing that matters about that is that it does not change any answer.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import kdx_client  # noqa: E402
import photos  # noqa: E402

PASSED = FAILED = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok    {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def product(offer: str, gallery: int, variants: int = 0) -> dict:
    """A payload whose photographs are all distinct, so counting them is unambiguous."""
    return {
        "source_offer_id": offer,
        "name_en": f"product {offer}",
        "images": [f"https://cbu01.alicdn.com/{offer}/g{n}.jpg" for n in range(gallery)],
        "variants": [{"original": f"c{n}",
                      "image": f"https://cbu01.alicdn.com/{offer}/v{n}.jpg",
                      "images": [f"https://cbu01.alicdn.com/{offer}/v{n}.jpg"]}
                     for n in range(variants)],
    }


print("counting the work, not the bytes")
check("a plain gallery is counted", kdx_client.photo_count(product("a", 5)) == 5)
check("variant photographs count too - the ones the first fix forgot",
      kdx_client.photo_count(product("a", 5, variants=146)) == 151,
      str(kdx_client.photo_count(product("a", 5, variants=146))))
check("the same URL twice is one download",
      kdx_client.photo_count({"images": ["u", "u"],
                              "variants": [{"image": "u", "images": ["u"]}]}) == 1)
check("a product with no photographs costs nothing",
      kdx_client.photo_count({"source_offer_id": "x", "name_en": "x"}) == 0)


print("\ngrouping products into requests his server can finish")
client = kdx_client.KdxClient("https://example.invalid", "token")

small = [product(str(n), 4) for n in range(10)]
groups = client.batches(small, batch_size=20, photos_per_request=40)
check("ten small products fit in one request",
      len(groups) == 1 and len(groups[0]) == 10, str([len(g) for g in groups]))

groups = client.batches(small, batch_size=20, photos_per_request=12)
check("a tighter photograph budget splits them",
      all(sum(kdx_client.photo_count(p) for p in g) <= 12 for g in groups)
      and sum(len(g) for g in groups) == 10,
      str([sum(kdx_client.photo_count(p) for p in g) for g in groups]))

check("the product count still caps a request of tiny products",
      len(client.batches([product(str(n), 0) for n in range(45)],
                         batch_size=20, photos_per_request=40)) == 3)

huge = product("big", 143, variants=146)
mixed = [product("a", 4), huge, product("b", 4)]
groups = client.batches(mixed, batch_size=20, photos_per_request=40)
check("a product bigger than the whole budget travels ALONE",
      any(len(g) == 1 and g[0]["source_offer_id"] == "big" for g in groups),
      str([[p["source_offer_id"] for p in g] for g in groups]))
check("and it is not dropped, cut down or split in half",
      sum(len(g) for g in groups) == 3
      and kdx_client.photo_count(
          [p for g in groups for p in g if p["source_offer_id"] == "big"][0]) == 289,
      "his import deletes the photograph set before writing, so half a product "
      "would erase the other half")
check("nothing is lost when the list is empty", client.batches([], 20) == [])


print("\nbuying enough time for the photographs in the request")
check("a small request keeps the base timeout",
      client.timeout_for([product("a", 2)]) <= client.timeout + 3 * 1.5 + 1)
big_wait = client.timeout_for([huge])
check("the 146-option product is given minutes, not 45 seconds",
      big_wait > 300, f"{big_wait}s for {kdx_client.photo_count(huge)} photographs")
check("and it is still capped, so nothing can hold the night open forever",
      client.timeout_for([huge] * 20) == kdx_client.MAX_TIMEOUT,
      str(client.timeout_for([huge] * 20)))
check("the wait grows with the work",
      client.timeout_for([product("a", 40)]) > client.timeout_for([product("a", 4)]))


print("\na timeout is reported, never retried")


class TimingOutOpener:
    def __init__(self):
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        raise TimeoutError("The read operation timed out")


opener = TimingOutOpener()
timed_out = kdx_client.KdxClient("https://example.invalid", "token", max_retries=3)
timed_out._wait_turn = lambda: None
original = kdx_client.urllib.request.urlopen
kdx_client.urllib.request.urlopen = opener
try:
    error = ""
    try:
        timed_out._post("/api/v1/products/import", {"products": []}, timeout=90)
    except kdx_client.KdxError as exc:
        error = str(exc)
finally:
    kdx_client.urllib.request.urlopen = original

check("his server is asked exactly once", opener.calls == 1, f"{opener.calls} calls")
check("and the report says how long we waited", "90s" in error, error)


print("\nchecking photographs concurrently must not change any answer")

DEAD = {"https://cbu01.alicdn.com/a/g2.jpg"}


class FakeResponse:
    def __init__(self, url):
        self.status = 200
        self.headers = {"Content-Type": "image/jpeg"}
        self._url = url

    def read(self):
        return b"\xff\xd8" + self._url.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def opener_for(order: list):
    def opener(request, timeout=None):
        url = request.full_url
        order.append(url)
        if url in DEAD:
            raise urllib_error.HTTPError(url, 404, "gone", None, None)
        return FakeResponse(url)
    return opener


import urllib.error as urllib_error  # noqa: E402

payload = product("a", 4, variants=3)
payload["images"].append("https://cbu01.alicdn.com/a/g2.jpg")   # a duplicate of a dead one

serial_order: list = []
serial = photos.PhotoChecker(opener=opener_for(serial_order), workers=1)
serial_report = photos.prune({k: (list(v) if isinstance(v, list) else v)
                              for k, v in payload.items()}, serial)

parallel_order: list = []
parallel = photos.PhotoChecker(opener=opener_for(parallel_order), workers=8)
parallel_report = photos.prune({k: (list(v) if isinstance(v, list) else v)
                                for k, v in payload.items()}, parallel)

check("the same photographs survive either way",
      serial_report["kept"] == parallel_report["kept"],
      f"{serial_report['kept']} vs {parallel_report['kept']}")
check("the same ones are dropped",
      serial_report["dropped"] == parallel_report["dropped"],
      str((serial_report["dropped"], parallel_report["dropped"])))
check("the dead photograph really was dropped, so this is not two empty sets",
      set(serial_report["dropped"]) == {"https://cbu01.alicdn.com/a/g2.jpg"},
      str(serial_report["dropped"]))
check("each URL is fetched once, however many places it appears",
      len(parallel_order) == len(set(parallel_order)) == len(serial_order),
      f"{len(parallel_order)} fetches, {len(set(parallel_order))} distinct")
check("the byte budget is still respected under threads",
      parallel.held_bytes <= parallel.keep_bytes)

tight = photos.PhotoChecker(opener=opener_for([]), workers=8, keep_bytes=10)
tight.warm([f"https://cbu01.alicdn.com/a/g{n}.jpg" for n in range(20)])
check("and a small budget stops being honoured only when it is full",
      tight.held_bytes <= 10, str(tight.held_bytes))

print("\nthe option labels that reached the shop in Chinese")

# Same product, same night: 146 colour options went out with their Chinese
# names because the translator was handed all 146 in one call and answered with
# a fraction of them. Every missing label falls back to the original, which is
# the right failure - a size that vanishes is worse - but it is silent: all 146
# had an entry, so nothing downstream could tell.
import enrich  # noqa: E402

CHINESE = {"红色": ("Red", "أحمر"), "蓝色": ("Blue", "أزرق"), "均码": ("One size", "مقاس واحد")}


def responder(*, batch_limit: int = 1000, deaf_to: set | None = None):
    """A translator that answers at most `batch_limit` labels and never `deaf_to`."""
    seen_batches: list = []
    deaf = deaf_to or set()

    def chat(system, user, api_key, timeout):
        import json as _json
        asked = _json.loads(user)
        seen_batches.append(asked)
        answered = {}
        for term in asked[:batch_limit]:
            if term in deaf:
                continue
            english, arabic = CHINESE.get(term, (term, term))
            answered[term] = {"en": english, "ar": arabic}
        return {"terms": answered}

    return chat, seen_batches


original_chat = enrich._chat
try:
    labels = [f"色{n}" for n in range(95)] + list(CHINESE)
    enrich._chat, batches_seen = responder()
    enrich._translate_labels(labels, "prompt", "key", 30)
    check("a long label list is asked for in batches, not all at once",
          len(batches_seen) >= 2 and all(len(b) <= enrich.LABELS_PER_CALL
                                         for b in batches_seen),
          str([len(b) for b in batches_seen]))
    first_pass = -(-len(labels) // enrich.LABELS_PER_CALL)
    check("and the first pass covers every label exactly once",
          sorted(t for b in batches_seen[:first_pass] for t in b) == sorted(labels),
          str([len(b) for b in batches_seen]))

    # The model that answers only the first few of what it is given: this is
    # what actually happened, and the retry is what catches it.
    enrich._chat, batches_seen = responder(batch_limit=1)
    out = enrich._translate_labels(list(CHINESE), "prompt", "key", 30)
    check("a label the model skipped is asked for again on its own",
          len(batches_seen) >= 2 and len(batches_seen[-1]) < len(CHINESE),
          str([len(b) for b in batches_seen]))
    check("CONTROL: the first answer is kept, not thrown away and re-asked",
          out["红色"]["ar"] == "أحمر", str(out["红色"]))

    enrich._chat, _ = responder(deaf_to={"蓝色"})
    out = enrich._translate_labels(list(CHINESE), "prompt", "key", 30)
    check("a label nothing can translate keeps its Chinese rather than vanishing",
          out["蓝色"]["ar"] == "蓝色" and len(out) == 3, str(out))
    check("while the ones that did translate are still translated",
          out["均码"]["en"] == "One size", str(out))

    calls = {"n": 0}

    def exploding(system, user, api_key, timeout):
        calls["n"] += 1
        if calls["n"] > 1:                  # the retry, on the leftovers
            raise RuntimeError("the model is down")
        return {"terms": {}}

    enrich._chat = exploding
    out = enrich._translate_labels(["红色"], "prompt", "key", 30)
    check("a failed retry loses nothing - the original survives",
          out == {"红色": {"en": "红色", "ar": "红色"}}, str(out))
finally:
    enrich._chat = original_chat

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)

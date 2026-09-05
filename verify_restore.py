#!/usr/bin/env python3
"""
Refilling an empty shop from disk, without sending it anything twice.

5 September 2026. He deleted every product, so restore_from_disk.py pushes back
the payloads already on disk. The dangerous parts are all about repetition and
about scope:

  * a dry run must send NOTHING - the default has to be safe
  * an offer priced on two days has two files, and only the later one may go
  * the thirty-seven products he was asked to delete must not come back
  * an interrupted run must resume, never restart: his import appends options,
    so a product sent twice is a product with double the options
  * --mirror must be a copy, and must leave a real size axis alone

Nothing here talks to his shop. The push path is exercised against a stub.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
sys.path.insert(0, HERE)

import restore_from_disk as restore                          # noqa: E402

PASS = FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def product(offer_id: str, *, variants=None, sizes=None, price=10.0) -> dict:
    payload = {"source_offer_id": offer_id, "name_en": f"p{offer_id}",
               "name_ar": "منتج", "price": price, "images": ["a.jpg"],
               "sizes": sizes or [], "needs_shipment": "no", "category": {}}
    if variants is not None:
        payload["variants"] = variants
    return payload


OPTIONS = [
    {"original": "30*50", "en": "30*50", "ar": "٣٠*٥٠", "image": "a.jpg",
     "images": ["a.jpg"], "price": 144.87, "price_min": 144.87,
     "price_max": 144.87, "sizes": []},
    {"original": "40*40", "en": "40*40", "ar": "٤٠*٤٠", "image": "b.jpg",
     "images": ["b.jpg"], "price": 219.79, "price_min": 219.79,
     "price_max": 219.79, "sizes": []},
]

REAL_SIZES = [
    {"original": "أسود", "en": "Black", "ar": "أسود", "image": "k.jpg",
     "images": ["k.jpg"], "price": 31.0, "price_min": 31.0, "price_max": 34.0,
     "sizes": [{"original": "M", "en": "M", "ar": "M", "price": 31.0},
               {"original": "L", "en": "L", "ar": "L", "price": 34.0}]},
]


def write_out(root: str, day: str, payload: dict) -> str:
    folder = os.path.join(root, day)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{payload['source_offer_id']}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


work = tempfile.mkdtemp(prefix="restore-verify-")

# --------------------------------------------------------------------------
section("The review list does not come back")
# --------------------------------------------------------------------------

listing = os.path.join(work, "review.txt")
with open(listing, "w", encoding="utf-8") as handle:
    handle.write("منتجات منشورة في المتجر تحتاج مراجعة\n\n"
                 "  خزان هواء أفقي\n"
                 "  https://detail.1688.com/offer/1078951728898.html\n"
                 "  https://detail.1688.com/offer/717716012309.html\n"
                 "1072288684025\n")

ids = restore.excluded_ids(listing)
check("offer ids are read out of the 1688 URLs",
      {"1078951728898", "717716012309"} <= ids, True)
check("a bare id on its own line counts too", "1072288684025" in ids, True)
check("the one reported outside the file is always excluded",
      "1080706692044" in ids, True)
check("Arabic prose contributes no ids", "منتجات" in str(ids), False)
check("with no list, the standing exclusion still holds",
      restore.excluded_ids(""), set(restore.ALWAYS_EXCLUDE))

# The real file that ships with the repo - the one he was actually given.
shipped = os.path.join(HERE, restore.DEFAULT_EXCLUDE)
if os.path.exists(shipped):
    real = restore.excluded_ids(shipped)
    check("the shipped review list yields its 37 products plus the one after it",
          len(real), 38)

# --------------------------------------------------------------------------
section("An offer priced twice is sent once, in its later shape")
# --------------------------------------------------------------------------

out = os.path.join(work, "out")
write_out(out, "2026-09-02", product("111", price=10.0))
write_out(out, "2026-09-04", product("111", price=12.0))     # repriced later
write_out(out, "2026-09-03", product("222"))

files = restore.payload_files(out)
check("one entry per offer, not one per file", sorted(files), ["111", "222"])
check("the later day wins", "2026-09-04" in files["111"], True)
check("...and it is the later PRICE that would be sent",
      restore.load(files["111"])["price"], 12.0)

# --------------------------------------------------------------------------
section("What the batch published a minute ago must not be sent again")
# --------------------------------------------------------------------------

logs = os.path.join(work, "logs")
os.makedirs(logs, exist_ok=True)
with open(os.path.join(logs, "audit-2026-09.csv"), "w",
          encoding="utf-8-sig", newline="") as handle:
    handle.write("timestamp,offer_id,sku_id,decision,reason_code\n")
    handle.write("2026-09-05 15:00:00,111,a,publish,\n")      # before the moment
    handle.write("2026-09-05 16:31:07,222,b,publish,\n")      # after it
    handle.write("2026-09-05 16:32:00,999,c,reject,mains_spec\n")

check("only what was published after the moment counts",
      restore.published_since("2026-09-05 16:28", logs), {"222"})
check("a rejected offer is not in the shop, so not excluded",
      "999" in restore.published_since("2026-09-05 16:28", logs), False)
check("no moment given, nothing excluded", restore.published_since("", logs), set())

# The audit writes a space. An ISO 'T' silently matches nothing, which reads as
# "the batch published nothing" and would send those products a second time.
try:
    restore.published_since("2026-09-05T16:28", logs)
    check("a T-shaped timestamp is refused, not quietly empty", "allowed", "refused")
except SystemExit:
    check("a T-shaped timestamp is refused, not quietly empty", "refused", "refused")

guarded = subprocess.run(
    [sys.executable, os.path.join(HERE, "restore_from_disk.py"),
     "--out-dir", out, "--state", os.path.join(work, "dry2.txt"), "--no-exclude",
     "--logs-dir", logs, "--not-published-since", "2026-09-05 16:28"],
    capture_output=True, text=True, env=dict(os.environ), timeout=120)
check("the freshly published one drops out of the run",
      "to send now           1" in guarded.stdout, True)
check("...and the run says why", "would double their options" in guarded.stdout, True)

# --------------------------------------------------------------------------
section("Mirroring options into sizes[] is a copy, and a narrow one")
# --------------------------------------------------------------------------

sizeless = product("333", variants=json.loads(json.dumps(OPTIONS)))
check("it fires where there is no size axis", restore.mirror(sizeless), True)
check("one sizes entry per option", len(sizeless["sizes"]), 2)
check("each carries its own price",
      [size["price"] for size in sizeless["sizes"]], [144.87, 219.79])
check("each carries its own photo",
      [size["image"] for size in sizeless["sizes"]], ["a.jpg", "b.jpg"])
check("variants[] is left whole", sizeless["variants"], OPTIONS)

sized = product("444", variants=json.loads(json.dumps(REAL_SIZES)))
check("a real size axis is not mirrored over", restore.mirror(sized), False)
check("...and keeps whatever sizes it had", sized["sizes"], [])

single = product("555", variants=[json.loads(json.dumps(OPTIONS[0]))])
check("one option is not a choice", restore.mirror(single), False)

plain = product("666", sizes=[{"original": "S", "en": "S", "ar": "S"}])
check("a product with no variants is untouched", restore.mirror(plain), False)
check("...and keeps its size names", len(plain["sizes"]), 1)

# --------------------------------------------------------------------------
section("A payload that is not a product is refused, not sent")
# --------------------------------------------------------------------------

bad = os.path.join(work, "bad.json")
with open(bad, "w", encoding="utf-8") as handle:
    json.dump({"products": [product("777")]}, handle)        # the envelope
try:
    restore.load(bad)
    check("the envelope is rejected", "loaded", "raised")
except ValueError:
    check("the envelope is rejected", "raised", "raised")

# --------------------------------------------------------------------------
section("Resuming: what went out never goes out twice")
# --------------------------------------------------------------------------

state = os.path.join(work, "state", "restored.txt")
check("no state file is an empty set", restore.done_already(state), set())
restore.record(state, ["111", "222"])
restore.record(state, ["333"])
check("every batch is appended as it returns",
      restore.done_already(state), {"111", "222", "333"})
check("the file survives a reread", restore.done_already(state),
      restore.done_already(state))

# --------------------------------------------------------------------------
section("The default sends nothing at all")
# --------------------------------------------------------------------------

env = dict(os.environ)
env.pop("KDX_API_TOKEN", None)                # a real push could not even start
env["KDX_BASE_URL"] = "https://example.invalid"

dry = subprocess.run(
    [sys.executable, os.path.join(HERE, "restore_from_disk.py"),
     "--out-dir", out, "--state", os.path.join(work, "dry.txt"), "--no-exclude"],
    capture_output=True, text=True, env=env, timeout=120)
check("a bare run exits clean", dry.returncode, 0)
check("...and says so", "DRY RUN" in dry.stdout, True)
check("...and counts what it would send", "to send now           2" in dry.stdout,
      True)
check("...and wrote no state file",
      os.path.exists(os.path.join(work, "dry.txt")), False)

limited = subprocess.run(
    [sys.executable, os.path.join(HERE, "restore_from_disk.py"),
     "--out-dir", out, "--state", os.path.join(work, "dry.txt"),
     "--no-exclude", "--limit", "1"],
    capture_output=True, text=True, env=env, timeout=120)
check("--limit narrows it", "to send now           1" in limited.stdout, True)

excluding = subprocess.run(
    [sys.executable, os.path.join(HERE, "restore_from_disk.py"),
     "--out-dir", out, "--state", os.path.join(work, "dry.txt"),
     "--exclude", listing],
    capture_output=True, text=True, env=env, timeout=120)
check("an excluded offer is not counted in",
      "to send now           2" in excluding.stdout, True)

# --------------------------------------------------------------------------
section("Pushing: batches, and the state written as they land")
# --------------------------------------------------------------------------

sent_batches: list = []


class StubClient:
    def __init__(self, *_, **__):
        pass

    def push(self, products, batch_size=20):
        sent_batches.append([p["source_offer_id"] for p in products])
        return [{"success": True, "message": "تم استلام البيانات بنجاح"}]


pushed_state = os.path.join(work, "pushed.txt")
argv = sys.argv[:]
real_client = restore.kdx_client.KdxClient
try:
    restore.kdx_client.KdxClient = StubClient
    os.environ["KDX_API_TOKEN"] = "stub"
    sys.argv = ["restore_from_disk.py", "--push", "--no-exclude",
                "--out-dir", out, "--state", pushed_state, "--batch-size", "1"]
    code = restore.main()
finally:
    restore.kdx_client.KdxClient = real_client
    sys.argv = argv
    os.environ.pop("KDX_API_TOKEN", None)

check("the push run exits clean", code, 0)
check("one request per batch-size products", sent_batches, [["111"], ["222"]])
check("every offer sent is recorded",
      restore.done_already(pushed_state), {"111", "222"})

# Now run it again. Nothing may go out a second time.
sent_batches.clear()
try:
    restore.kdx_client.KdxClient = StubClient
    os.environ["KDX_API_TOKEN"] = "stub"
    sys.argv = ["restore_from_disk.py", "--push", "--no-exclude",
                "--out-dir", out, "--state", pushed_state]
    again = restore.main()
finally:
    restore.kdx_client.KdxClient = real_client
    sys.argv = argv
    os.environ.pop("KDX_API_TOKEN", None)

check("a second run sends nothing", sent_batches, [])
check("...and says so rather than failing", again, 0)

# A batch his server refuses must not be recorded as sent.
class AngryClient(StubClient):
    def push(self, products, batch_size=20):
        raise restore.kdx_client.KdxError("500 from his import")


broken_state = os.path.join(work, "broken.txt")
try:
    restore.kdx_client.KdxClient = AngryClient
    os.environ["KDX_API_TOKEN"] = "stub"
    sys.argv = ["restore_from_disk.py", "--push", "--no-exclude",
                "--out-dir", out, "--state", broken_state]
    failed = restore.main()
finally:
    restore.kdx_client.KdxClient = real_client
    sys.argv = argv
    os.environ.pop("KDX_API_TOKEN", None)

check("a refused batch is reported as a failure", failed, 1)
check("...and nothing it contained is recorded as sent",
      restore.done_already(broken_state), set())

shutil.rmtree(work, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

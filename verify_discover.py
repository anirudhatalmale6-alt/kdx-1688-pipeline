"""
Discovery: the walk that feeds the night's run, checked against the behaviour
measured on the live gateway on 29 August.

Three of those measurements are load-bearing and are pinned here, so that if a
later change breaks them the suite says so instead of the catalogue quietly
drying up:

  * one photograph is worth about 75 offers across four pages, not 20;
  * the frontier comes from the TAIL of the results - expanding the top result
    returned 0 and 1 new offers, expanding offers further down returned 41-77;
  * an offer that has been seen before must never be handed over twice, because
    this runs every night from cron.

    python3 verify_discover.py
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import sys
import tempfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import catalog  # noqa: E402
import discover  # noqa: E402
import source as source_module  # noqa: E402

PASS = FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"\n         {detail}" if detail else ""))


def row(offer_id: int, category: str = "1031912", subject: str = "连衣裙") -> dict:
    """One search row in the shape the live gateway really returns."""
    return {"offerId": str(offer_id), "subject": subject, "quantityBegin": 2,
            "unit": "件", "oldPrice": 1010,
            "imageUrl": f"https://cbu01.alicdn.com/img/ibank/pic-{offer_id}.jpg",
            "province": "浙江", "city": "杭州", "supplyAmount": 100,
            "categoryId": category, "detailUrl": f"https://detail.1688.com/{offer_id}"}


class FakeSource:
    """
    A similarity graph, replayed. Each photograph maps to a list of offers, four
    pages of twenty like the real thing, and every offer's own photograph is a
    key in the same graph - which is what makes the walk possible at all.
    """

    PAGE = 20

    def __init__(self, graph: dict, fail_on: str = ""):
        self.graph = graph
        self.fail_on = fail_on
        self.calls: list = []

    def search_by_image(self, pic_url: str, page: int = 1) -> list:
        self.calls.append((pic_url, page))
        if self.fail_on and self.fail_on in pic_url:
            raise source_module.SourceError("handle image error with url " + pic_url)
        offers = self.graph.get(pic_url, [])
        window = offers[(page - 1) * self.PAGE: page * self.PAGE]
        return [source_module.normalise_search_row(r) for r in window]


def graph_of(*chunks) -> dict:
    """Build {photograph: [rows]} where chunk i is what seed i returns."""
    return {pic: rows for pic, rows in chunks}


def ledger_in(directory: str) -> discover.Ledger:
    return discover.Ledger(os.path.join(directory, "discovered.json"))


SEED = "https://cbu01.alicdn.com/img/ibank/seed.jpg"


print("\n1. the seed list is the client's, and its problems are loud")
work = tempfile.mkdtemp(prefix="kdx-discover-")
seed_file = os.path.join(work, "seeds.txt")
with open(seed_file, "w", encoding="utf-8") as handle:
    handle.write("# the photographs we start from\n"
                 "\n"
                 "https://cbu01.alicdn.com/img/ibank/a.jpg\n"
                 "  https://cbu01.alicdn.com/img/ibank/b.jpg  \n")
seeds = discover.read_seeds(seed_file)
check("comments and blank lines are not seeds", len(seeds) == 2, str(seeds))
check("surrounding whitespace is stripped",
      seeds[1] == "https://cbu01.alicdn.com/img/ibank/b.jpg", seeds[1])

try:
    discover.read_seeds(os.path.join(work, "nope.txt"))
except discover.DiscoveryError as exc:
    check("a missing seed file says so", "not found" in str(exc))
else:
    check("a missing seed file says so", False, "it was accepted")

empty = os.path.join(work, "empty.txt")
open(empty, "w").close()
try:
    discover.read_seeds(empty)
except discover.DiscoveryError as exc:
    check("an empty seed file is refused, not treated as zero work",
          "no URLs" in str(exc))
else:
    check("an empty seed file is refused", False, "it was accepted")

old = os.environ.pop("KDX_SEEDS", None)
try:
    discover.read_seeds("")
except discover.DiscoveryError as exc:
    check("no seeds at all explains WHY a picture is needed",
          "cannot fetch an offer by id" in str(exc), str(exc))
else:
    check("no seeds at all is refused", False, "it was accepted")
finally:
    if old is not None:
        os.environ["KDX_SEEDS"] = old

print("\n2. one photograph yields four pages, the way the gateway does")
offers = [row(700000 + n) for n in range(75)]
src = FakeSource({SEED: offers})
walker = discover.Discovery(src, ledger_in(work), day="2026-08-29")
found = walker.run([SEED], quota=300)
check("all 75 offers come back, not the first page's 20",
      len(found) == 75, str(len(found)))
pages_asked = [page for pic, page in src.calls if pic == SEED]
check("it asked for exactly the four pages the channel has, and no fifth",
      pages_asked == [1, 2, 3, 4], str(pages_asked))

empty_after = FakeSource({SEED: [row(700000 + n) for n in range(30)]})
probe = discover.Discovery(empty_after, ledger_in(tempfile.mkdtemp()), pages=6)
probe.run([SEED], quota=300)
check("CONTROL when the offers run out early it stops there, not at page six",
      [page for pic, page in empty_after.calls if pic == SEED] == [1, 2, 3],
      str(empty_after.calls))
check("the products are in the normalised shape the pipeline consumes",
      found[0]["offer_id"] == "700000" and found[0]["source_channel"] == "linkplus")

print("\n3. the gateway repeats itself across pages, and that must not reach KDX")
# Not hypothetical: a 300-product run against the live gateway harvested 223
# products while the ledger recorded 204 - nineteen products that would have
# been published twice on the same night.
repeated = ([row(750000 + n) for n in range(20)]        # page 1
            + [row(750010 + n) for n in range(20)]      # page 2, overlapping it
            + [row(750040 + n) for n in range(5)])      # page 3
src_dupes = FakeSource({SEED: repeated})
work_d = tempfile.mkdtemp(prefix="kdx-dupes-")
walker_d = discover.Discovery(src_dupes, ledger_in(work_d), day="2026-08-29")
got = walker_d.run([SEED], quota=300)
ids = [p["offer_id"] for p in got]
check("no product is handed over twice in one night",
      len(ids) == len(set(ids)), f"{len(ids)} products, {len(set(ids))} distinct")
check("and the count is the distinct one, not the raw row count",
      len(ids) == 35, str(len(ids)))
check("CONTROL the fixture really did repeat itself",
      len(repeated) == 45, str(len(repeated)))
check("what the pipeline is handed matches what the ledger recorded",
      len(ids) == ledger_in(work_d).summary()["offers_known"])
shutil.rmtree(work_d)

print("\n4. nothing is handed over twice - this runs every night")
src2 = FakeSource({SEED: offers})
walker2 = discover.Discovery(src2, ledger_in(work), day="2026-08-30")
again = walker2.run([SEED], quota=300)
check("a second night finds nothing new from the same seed",
      len(again) == 0, str(len(again)))
check("and it did not even search the photograph again",
      src2.calls == [], str(src2.calls))
fresh_ledger = ledger_in(work)
check("the ledger was on disk, not in memory",
      fresh_ledger.knows_offer("700000") and fresh_ledger.knows_offer("700074"))
check("CONTROL an offer that was never found is not in it",
      not fresh_ledger.knows_offer("999999"))
check("no half-written temporary file is left behind",
      not os.path.exists(os.path.join(work, "discovered.json.tmp")))
shutil.rmtree(work)

print("\n5. the quota is a ceiling, and a banned category does not eat it")


class Categories:
    """Only what Discovery asks of a CategoryIndex."""

    def __init__(self, states: dict):
        self.states = states
        self.by_id = {}

    def state_of(self, category_id):
        return self.states.get(str(category_id), "unknown")


work = tempfile.mkdtemp(prefix="kdx-discover-")
mixed = ([row(800000 + n, category="1031912") for n in range(10)]
         + [row(810000 + n, category="666666") for n in range(10)])
src3 = FakeSource({SEED: mixed})
walker3 = discover.Discovery(src3, ledger_in(work),
                            categories=Categories({"666666": catalog.BLOCKED}),
                            day="2026-08-29")
kept = walker3.run([SEED], quota=300)
check("the banned half never reaches the pipeline", len(kept) == 10, str(len(kept)))
check("and it is counted, not silently dropped", walker3.rejected_early == 10)
check("every product that survived is from the allowed category",
      all(p["category_id"] == "1031912" for p in kept))
check("the banned offers ARE in the ledger, so they are not re-fetched nightly",
      ledger_in(work).knows_offer("810000"))

work2 = tempfile.mkdtemp(prefix="kdx-discover-")
walker4 = discover.Discovery(FakeSource({SEED: [row(820000 + n) for n in range(75)]}),
                             ledger_in(work2), day="2026-08-29")
check("a quota of 7 returns exactly 7", len(walker4.run([SEED], quota=7)) == 7)
walker5 = discover.Discovery(FakeSource({SEED: mixed}), ledger_in(work2))
check("CONTROL a quota of zero does no work at all",
      walker5.run([SEED], quota=0) == [] and walker5.searches == 0)
shutil.rmtree(work2)

print("\n6. a category under review is treated like a banned one, not like unknown")
work3 = tempfile.mkdtemp(prefix="kdx-discover-")
walker6 = discover.Discovery(FakeSource({SEED: mixed}), ledger_in(work3),
                             categories=Categories({"666666": catalog.REVIEW}))
check("review is held back too", len(walker6.run([SEED], quota=300)) == 10)
shutil.rmtree(work3)

work3 = tempfile.mkdtemp(prefix="kdx-discover-")
walker7 = discover.Discovery(FakeSource({SEED: mixed}), ledger_in(work3),
                             categories=Categories({}))
check("CONTROL unknown does NOT reject - most leaf ids sit below the walked depth",
      len(walker7.run([SEED], quota=300)) == 20)
shutil.rmtree(work3)

print("\n7. a banned WORD is caught here too, before the quota is spent")
import rules  # noqa: E402

work3 = tempfile.mkdtemp(prefix="kdx-discover-")
# Taken from rules.BANNED_TERMS rather than typed here, so this stays true if
# the client edits the list. This channel carries no description and no
# attributes, so the title is the only place a banned word can appear.
term = rules.BANNED_TERMS["sexual"][4]                     # 情趣
check("the term under test really is one of the client's banned words",
      any(term in terms for terms in rules.BANNED_TERMS.values()))
walker8 = discover.Discovery(
    FakeSource({SEED: [row(830000, subject=f"批发{term}用品"),
                       row(830001, subject="连衣裙")]}),
    ledger_in(work3))
survived = walker8.run([SEED], quota=300)
check(f"a title containing {term!r} is dropped before the quota is spent",
      [p["offer_id"] for p in survived] == ["830001"],
      str([p["offer_id"] for p in survived]))
check("CONTROL the innocent product in the same batch survived",
      walker8.rejected_early == 1)
shutil.rmtree(work3)

print("\n8. the frontier is taken from the TAIL, which is the measured rule")
first = [row(900000 + n) for n in range(75)]
tail_pic = first[-1]["imageUrl"]
head_pic = first[0]["imageUrl"]
second = [row(910000 + n) for n in range(75)]

work4 = tempfile.mkdtemp(prefix="kdx-discover-")
src4 = FakeSource({SEED: first, tail_pic: second, head_pic: first})
walker9 = discover.Discovery(src4, ledger_in(work4), day="2026-08-29")
harvest = walker9.run([SEED], quota=150)
check("the walk continued past the first photograph on its own",
      len(harvest) == 150, str(len(harvest)))
expanded = [pic for pic, page in src4.calls if page == 1]
check("it expanded the LAST result, not the first",
      tail_pic in expanded and head_pic not in expanded, str(expanded[:3]))
check("the ledger records what each photograph was worth",
      ledger_in(work4).state["expanded"][SEED]["new"] == 75)
check("the lifetime yield is reported, so a drying-up walk is visible",
      ledger_in(work4).summary()["new_per_search"] > 0)
shutil.rmtree(work4)

print("\n9. limits and failures are reported, never silent")
work5 = tempfile.mkdtemp(prefix="kdx-discover-")
walker10 = discover.Discovery(FakeSource({SEED: [row(920000 + n) for n in range(75)]}),
                              ledger_in(work5), max_searches=2, day="2026-08-29")
capped = walker10.run([SEED], quota=300)
check("the search ceiling stops the run", walker10.searches == 2)
check("it took only the two pages it was allowed", len(capped) == 40, str(len(capped)))
check("and it said so out loud",
      any("ceiling" in note for note in walker10.notes), str(walker10.notes))

# A ledger of its own: work5's already records SEED as expanded, and an
# expanded photograph is deliberately never searched again.
walker11 = discover.Discovery(FakeSource({SEED: []}, fail_on="seed.jpg"),
                              ledger_in(tempfile.mkdtemp(prefix="kdx-dead-")))
check("a dead photograph is a note, not an exception",
      walker11.run([SEED], quota=300) == []
      and any("seed failed" in note for note in walker11.notes), str(walker11.notes))
shutil.rmtree(work5)

print("\n10. offers already paid for are not thrown away")
work7 = tempfile.mkdtemp(prefix="kdx-surplus-")
plenty = [row(940000 + n) for n in range(75)]
src5 = FakeSource({SEED: plenty})
night1 = discover.Discovery(src5, ledger_in(work7), day="2026-08-29")
took = night1.run([SEED], quota=12)
check("the first night takes only its quota", len(took) == 12, str(len(took)))
kept = ledger_in(work7).state["pending"]
check("the other 63 are kept, not discarded - the search was already paid for",
      len(kept) == 63, str(len(kept)))

src6 = FakeSource({SEED: plenty})
night2 = discover.Discovery(src6, ledger_in(work7), day="2026-08-30")
second = night2.run([SEED], quota=12)
check("the second night is served from the surplus", len(second) == 12)
check("and it made NO gateway search at all", src6.calls == [], str(src6.calls))
check("it says how many came from the surplus", night2.from_surplus == 12)
check("none of them repeats a product from the first night",
      not ({p["offer_id"] for p in took} & {p["offer_id"] for p in second}))
check("the price survived the round trip through JSON as a Decimal",
      isinstance(second[0]["variants"][0]["price"], Decimal),
      type(second[0]["variants"][0]["price"]).__name__)
check("and it is the same price, not a float that looks like it",
      second[0]["variants"][0]["price"] == Decimal("10.10"),
      str(second[0]["variants"][0]["price"]))
check("the surplus went down by what was taken",
      ledger_in(work7).summary()["waiting"] == 51,
      str(ledger_in(work7).summary()["waiting"]))
check("a held offer counts as known, so a later search will not re-offer it",
      ledger_in(work7).knows_offer("940070"))
shutil.rmtree(work7)

print("\n11. the run refuses to overlap itself")
import daily_run  # noqa: E402

work6 = tempfile.mkdtemp(prefix="kdx-lock-")
lock_path = os.path.join(work6, "daily.lock")
handle = daily_run.take_lock(lock_path)
try:
    daily_run.take_lock(lock_path)
except daily_run.Locked as exc:
    check("a second run will not start while the first holds the lock",
          "another run holds" in str(exc))
else:
    check("a second run will not start", False, "it started")
daily_run.release_lock(handle, lock_path)
check("the lock is gone once the run finishes", not os.path.exists(lock_path))

stale = os.path.join(work6, "stale.lock")
with open(stale, "w", encoding="utf-8") as fh:
    fh.write("1 old\n")
os.utime(stale, (0, 0))            # 1970: certainly older than the stale window
handle = daily_run.take_lock(stale)
check("a lock left behind by a dead run is cleared, not obeyed for ever",
      os.path.exists(stale))
daily_run.release_lock(handle, stale)

# On 4 September the twelve-hour rule was twelve hours of silence: systemd
# killed the 01:21 batch at its 15-minute wall, the lock outlived it, and the
# twenty batches from 01:41 to 07:22 each read a lock two hours old, exited in
# one second, and reported success. Age was the wrong question.
import subprocess  # noqa: E402

departed = subprocess.Popen([sys.executable, "-c", "pass"])
departed.wait()
fresh = os.path.join(work6, "killed.lock")
with open(fresh, "w", encoding="utf-8") as fh:
    fh.write(f"{departed.pid} 2026-09-04T01:21:26 99999999\n")
check("a lock is not believed just because it is recent - the run that took "
      "this one is gone, so the next batch takes it",
      not daily_run.lock_holder_alive(fresh))
handle = daily_run.take_lock(fresh)
check("and taking it really succeeds, which is the six hours that were lost",
      os.path.exists(fresh))
daily_run.release_lock(handle, fresh)

# CONTROL a lock a LIVE run holds must still be obeyed, or the fix above trades
# six silent hours for two runs on one ledger. This is the case the first
# version of lock_holder_alive got wrong, and the case section 11 above already
# exercises with this very process: it took a real lock a few lines up.
holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
try:
    held = os.path.join(work6, "held.lock")
    with open(held, "w", encoding="utf-8") as fh:
        fh.write(f"{holder.pid} now "
                 f"{daily_run.process_start_time(holder.pid)}\n")
    check("CONTROL a lock whose run IS alive is obeyed, whatever that run is "
          "called - identity is the pid and when it started, not a name",
          daily_run.lock_holder_alive(held))
    try:
        daily_run.take_lock(held)
    except daily_run.Locked:
        check("CONTROL so a second run still refuses to start", True)
    else:
        check("CONTROL so a second run still refuses to start", False,
              "it started alongside a living run")
finally:
    holder.kill()
    holder.wait()

# CONTROL a pid we wrote hours ago can be recycled. pid 1 is alive on every
# Linux box; the start time we claim for it is not the one it has.
recycled = os.path.join(work6, "recycled.lock")
with open(recycled, "w", encoding="utf-8") as fh:
    fh.write("1 2026-09-04T01:21:26 12345\n")
check("CONTROL a recycled pid is not mistaken for the run that took the lock",
      not daily_run.lock_holder_alive(recycled))
check("CONTROL and pid 1 really is alive, so that is a start-time mismatch and "
      "not an absent process",
      daily_run.process_start_time(1) != "",
      daily_run.process_start_time(1))

# CONTROL anything unreadable is assumed HELD - including the two-field format
# this very version replaced, which is what is sitting on the server right now.
for name, body in (("garbage.lock", "written by an older version\n"),
                   ("oldformat.lock", "167504 2026-09-04T01:21:26\n")):
    older = os.path.join(work6, name)
    with open(older, "w", encoding="utf-8") as fh:
        fh.write(body)
    check(f"CONTROL a lock it cannot read is assumed held ({name})",
          daily_run.lock_holder_alive(older))
shutil.rmtree(work6)

print("\n8. forty-nine departments must not become four")

# Ten seeds, each worth a full four pages, exactly like a real department.
def department(index: int) -> tuple:
    pic = f"https://cbu01.alicdn.com/img/ibank/dept-{index}.jpg"
    rows = [row(index * 1000 + n, category=f"10{index:03d}") for n in range(80)]
    return pic, rows


many = dict(department(i) for i in range(10))
seed_list = list(many)

work7 = tempfile.mkdtemp(prefix="kdx-share-")
walker = discover.Discovery(FakeSource(many), ledger_in(work7), day="2026-08-31")
got = walker.run(seed_list, quota=100)
opened = {product["category_id"] for product in got}
check("a hundred products off ten seeds touch all ten departments",
      len(opened) == 10, f"{len(opened)} departments: {sorted(opened)}")
check("and each department contributes its share, not all of one",
      max(sum(1 for p in got if p["category_id"] == c) for c in opened) <= 10,
      str({c: sum(1 for p in got if p["category_id"] == c) for c in opened}))
check("the quota is still filled", len(got) == 100, str(len(got)))

# CONTROL: without the share, the same run is four departments wide - which is
# the behaviour every run had before there were forty-nine seeds.
work8 = tempfile.mkdtemp(prefix="kdx-noshare-")
unfair = discover.Discovery(FakeSource(many), ledger_in(work8), day="2026-08-31",
                            max_per_seed=0)
got_unfair = unfair.run(seed_list, quota=100)
check("CONTROL uncapped, the same seeds give a narrow catalogue",
      len({p["category_id"] for p in got_unfair}) < 5,
      str(len({p["category_id"] for p in got_unfair})))

check("CONTROL one seed alone is never capped",
      discover.Discovery(FakeSource(many), ledger_in(tempfile.mkdtemp()),
                         day="x").fair_share(300, 1) == 0)
check("CONTROL the share is worked out from the quota and the seed count",
      discover.Discovery(FakeSource(many), ledger_in(tempfile.mkdtemp()),
                         day="x").fair_share(300, 49) == 6)

# Nothing paid for is thrown away: what a department could not contribute
# tonight is held, not dropped.
held = discover.Ledger(os.path.join(work7, "discovered.json")).summary()["waiting"]
check("what a department could not give tonight is held, not lost",
      held > 0, str(held))

print("\n8b. the leftovers are drawn a department at a time, not oldest-first")
# Held in walk order, the front of the queue is entirely the first departments'
# leftovers. A night drew 148 from the surplus and 83 were shoes and children's
# clothing, undoing the fair share above.
work_fifo = tempfile.mkdtemp(prefix="kdx-fifo-")
ledger_fifo = ledger_in(work_fifo)
for department in range(5):
    for n in range(40):
        ledger_fifo.hold(source_module.normalise_search_row(
            row(700000 + department * 100 + n, category=f"cat-{department}")))
ledger_fifo.save()
reader = ledger_in(work_fifo)
drawn = reader.take_pending(50)
# NOTE: every offer above carries a distinct category and no photograph, which
# is the weak version of this test - see 8c for the one that matters.
spread = {product["category_id"] for product in drawn}
check("fifty leftovers reach all five departments, not the first two",
      len(spread) == 5, str(sorted(spread)))
counts = {c: sum(1 for p in drawn if p["category_id"] == c) for c in spread}
check("and no department takes more than its turn",
      max(counts.values()) - min(counts.values()) <= 1, str(counts))
reader.save()      # what run() does at the end of a night
check("CONTROL what was drawn is gone from the surplus",
      ledger_in(work_fifo).summary()["waiting"] == 150,
      str(ledger_in(work_fifo).summary()["waiting"]))
check("CONTROL asking for more than is held returns everything, not a crash",
      len(ledger_in(work_fifo).take_pending(10_000)) == 150)
shutil.rmtree(work_fifo, ignore_errors=True)

print("\n8c. spreading across categories is not spreading across departments")
# The bug this catches shipped and was visible on the live shop: a draw of
# fifteen came back under fifteen different categories and was fifteen kinds of
# bra. One department holds dozens of leaf categories, so category-spread and
# department-spread are not the same measurement.
work_dept = tempfile.mkdtemp(prefix="kdx-dept-")
ledger_dept = ledger_in(work_dept)
PHOTOS = [f"https://cbu01.alicdn.com/img/ibank/dept{d}.jpg" for d in range(4)]
for index, photo in enumerate(PHOTOS):
    for leaf in range(30):                       # 30 leaf categories per department
        ledger_dept.hold(
            source_module.normalise_search_row(
                row(600000 + index * 1000 + leaf, category=f"leaf-{index}-{leaf}")),
            held_by=photo)
ledger_dept.save()

taken = ledger_in(work_dept).take_pending(20)
check("CONTROL the weak measurement passes either way - 20 distinct categories",
      len({p["category_id"] for p in taken}) == 20,
      str(len({p["category_id"] for p in taken})))
departments = collections.Counter(
    p["category_id"].rsplit("-", 1)[0] for p in taken)
check("and the draw really does reach every department, not one of them",
      len(departments) == 4, str(dict(departments)))
check("with the turns taken evenly",
      max(departments.values()) - min(departments.values()) <= 1,
      str(dict(departments)))

# The photograph must not travel on into the product handed to the pipeline.
check("CONTROL the bookkeeping key is stripped before the product is used",
      all("_held_by" not in product for product in taken))

# An older ledger, written before the photograph was recorded, still works.
work_old = tempfile.mkdtemp(prefix="kdx-old-")
ledger_old = ledger_in(work_old)
for n in range(20):
    ledger_old.hold(source_module.normalise_search_row(
        row(650000 + n, category=f"old-{n % 4}")))
ledger_old.save()
old_draw = ledger_in(work_old).take_pending(8)
check("CONTROL a ledger with no photographs falls back to the category",
      len({p["category_id"] for p in old_draw}) == 4, str(len(old_draw)))
for directory in (work_dept, work_old):
    shutil.rmtree(directory, ignore_errors=True)

print("\n9. a night made entirely of leftovers opens nothing new")
work9 = tempfile.mkdtemp(prefix="kdx-surplus-")
ledger9 = ledger_in(work9)
for n in range(500):
    ledger9.hold(source_module.normalise_search_row(row(90000 + n, category="99999")))
ledger9.save()
balanced = discover.Discovery(FakeSource(many), ledger_in(work9), day="2026-09-01")
got9 = balanced.run(seed_list, quota=100)
from_old = sum(1 for p in got9 if p["category_id"] == "99999")
check("at most half a night comes from the surplus",
      from_old <= 50, f"{from_old} of {len(got9)}")
check("so the rest of the night still opens new departments",
      len({p["category_id"] for p in got9 if p["category_id"] != "99999"}) >= 5,
      str(sorted({p["category_id"] for p in got9})))

# CONTROL: told it may, it still takes the whole night from the surplus - the
# old behaviour is a setting, not a thing that was deleted.
work10 = tempfile.mkdtemp(prefix="kdx-surplus2-")
ledger10 = ledger_in(work10)
for n in range(500):
    ledger10.hold(source_module.normalise_search_row(row(90000 + n, category="99999")))
ledger10.save()
greedy = discover.Discovery(FakeSource(many), ledger_in(work10), day="2026-09-01",
                            surplus_share=1.0)
got10 = greedy.run(seed_list, quota=100)
check("CONTROL surplus_share=1 fills the whole night from the surplus",
      all(p["category_id"] == "99999" for p in got10), str(len(got10)))

for directory in (work7, work8, work9, work10):
    shutil.rmtree(directory, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)

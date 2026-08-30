"""
Checks for turning a word into a seed photograph.

    python3 verify_wordseed.py

No network. Every claim is paired with its control, because the failures that
matter here are all silent ones: a picture that opens nothing being written to
the seed file, a search being spent without being counted, a cached word being
paid for twice.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import searches  # noqa: E402
import wordseed  # noqa: E402

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


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeResponse(io.StringIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_opener(payload):
    def opener(url, timeout=0):
        opener.calls.append(url)
        return FakeResponse(json.dumps(payload))
    opener.calls = []
    return opener


class FakeSource:
    """Answers per URL: a list of offers, an exception, or an empty list."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def search_by_image(self, url, page=1):
        self.asked.append(url)
        answer = self.answers.get(url, [])
        if isinstance(answer, Exception):
            raise answer
        return answer


OFFERS = [{"offer_id": "1"}, {"offer_id": "2"}]


def seeder_for(answers, images_payload, meter=None, cache_path=""):
    images = wordseed.GoogleImages(api_key="x", opener=fake_opener(images_payload))
    cache = wordseed.SeedCache(path=cache_path, clock=lambda: 1000)
    return wordseed.WordSeeder(images=images, source=FakeSource(answers),
                               meter=meter, cache=cache), cache


# --------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="wordseed-")
    os.environ["KDX_STATE_DIR"] = tmp

    print("\nthe path follows the environment, like every other piece of state")
    check("word_seeds.json lands under KDX_STATE_DIR",
          wordseed.cache_path() == os.path.join(tmp, "word_seeds.json"),
          wordseed.cache_path())
    os.environ["KDX_WORD_SEEDS"] = os.path.join(tmp, "elsewhere.json")
    check("its own variable still wins",
          wordseed.cache_path() == os.path.join(tmp, "elsewhere.json"))
    del os.environ["KDX_WORD_SEEDS"]

    print("\nGoogle Images: full-size pictures before thumbnails")
    payload = {"images_results": [
        {"thumbnail": "https://tbn/a.jpg"},
        {"original": "https://big/b.jpg", "thumbnail": "https://tbn/b.jpg"},
        {"original": "https://big/c.jpg"},
    ]}
    images = wordseed.GoogleImages(api_key="k", opener=fake_opener(payload))
    urls = images.find("鞋", count=5)
    check("originals come first, thumbnail kept as a fallback",
          urls == ["https://big/b.jpg", "https://big/c.jpg", "https://tbn/a.jpg"], str(urls))
    check("count is honoured", len(images.find("鞋", count=2)) == 2)

    # A seed is fetched from China. A menswear seed that answered HTTP 200 here
    # was refused by the gateway on the night of 30 August.
    china = {"images_results": [
        {"original": "https://www.irkmagazine.com/wp-content/uploads/a.png"},
        {"original": "https://cbu01.alicdn.com/img/ibank/b.jpg"},
        {"original": "https://example.com/c.jpg"},
    ]}
    ordered = wordseed.GoogleImages(api_key="k", opener=fake_opener(china)).find("男装")
    check("a host Alibaba certainly reaches is tried first",
          ordered[0] == "https://cbu01.alicdn.com/img/ibank/b.jpg", str(ordered))
    check("CONTROL but the others are kept, not discarded", len(ordered) == 3, str(ordered))
    check("CONTROL with no Chinese host the order is untouched",
          wordseed.GoogleImages.china_first(["https://a/x.jpg", "https://b/y.jpg"])
          == ["https://a/x.jpg", "https://b/y.jpg"])
    check("CONTROL the count still bounds the result",
          len(wordseed.GoogleImages(api_key="k",
                                    opener=fake_opener(china)).find("男装", count=2)) == 2)
    # CONTROL: a SerpApi error must not be read as "no pictures found"
    broken = wordseed.GoogleImages(api_key="k",
                                   opener=fake_opener({"error": "Invalid API key"}))
    try:
        broken.find("鞋")
        check("CONTROL a SerpApi error is raised, not swallowed", False)
    except wordseed.WordSeedError as exc:
        check("CONTROL a SerpApi error is raised, not swallowed",
              "Invalid API key" in str(exc))
    try:
        wordseed.GoogleImages(api_key="", opener=fake_opener({}))
        check("no key is refused up front", False)
    except wordseed.WordSeedError:
        check("no key is refused up front", True)

    print("\na picture is only a seed once it has opened something")
    answers = {"https://big/b.jpg": [], "https://big/c.jpg": OFFERS}
    seeder, cache = seeder_for(answers, payload, cache_path=os.path.join(tmp, "one.json"))
    result = seeder.resolve("鞋")
    check("the empty picture is rejected and the next one tried",
          result["seed"] == "https://big/c.jpg", str(result))
    check("it reports how many pictures it had to try", result["tried"] == 2, str(result))
    check("the offer count is recorded, not assumed", result["offers"] == 2)
    # CONTROL: when nothing opens, there is no seed and there is a reason
    seeder2, _ = seeder_for({"https://big/b.jpg": [], "https://big/c.jpg": [],
                             "https://tbn/a.jpg": []}, payload,
                            cache_path=os.path.join(tmp, "two.json"))
    empty = seeder2.resolve("鞋")
    check("CONTROL a word that opens nothing yields no seed",
          empty["seed"] is None and empty["error"], str(empty))
    check("CONTROL and it does not raise - one dead word must not stop the rest",
          isinstance(empty, dict))

    print("\na refusal from the gateway is a reason, not a shrug")
    refused = {"https://big/b.jpg": RuntimeError("LinkPlus refused: handle image error"),
               "https://big/c.jpg": OFFERS}
    seeder3, _ = seeder_for(refused, payload, cache_path=os.path.join(tmp, "three.json"))
    third = seeder3.resolve("鞋")
    check("a refused picture is skipped and the next tried",
          third["seed"] == "https://big/c.jpg", str(third))
    allrefused = {url: RuntimeError("handle image error with url " + url)
                  for url in ["https://big/b.jpg", "https://big/c.jpg", "https://tbn/a.jpg"]}
    seeder4, _ = seeder_for(allrefused, payload, cache_path=os.path.join(tmp, "four.json"))
    dead = seeder4.resolve("鞋")
    check("CONTROL the gateway's own words survive into the report",
          dead["seed"] is None and "handle image error" in dead["error"], str(dead))

    print("\na word opened once is never paid for twice")
    path = os.path.join(tmp, "cache.json")
    seeder5, cache5 = seeder_for(answers, payload, cache_path=path)
    first = seeder5.resolve("厨房")
    second = seeder5.resolve("厨房")
    check("the second call is served from disk", second["cached"] is True, str(second))
    check("and returns the same picture", second["seed"] == first["seed"])
    check("the searcher was not asked again",
          len(seeder5.images.opener.calls) == 1, str(seeder5.images.opener.calls))
    check("the gateway was not asked again",
          seeder5.source.asked == ["https://big/b.jpg", "https://big/c.jpg"],
          str(seeder5.source.asked))
    reloaded = wordseed.SeedCache(path=path)
    check("the cache survives a restart", reloaded.get("厨房")["seed"] == first["seed"])
    check("and lists its seeds", reloaded.seeds() == [first["seed"]])
    # CONTROL: --force must actually bypass the cache
    forced = seeder5.resolve("厨房", force=True)
    check("CONTROL --force asks again", forced["cached"] is False and
          len(seeder5.images.opener.calls) == 2, str(seeder5.images.opener.calls))
    reloaded.forget("厨房")
    check("a dead seed can be forgotten",
          wordseed.SeedCache(path=path).get("厨房") is None)

    print("\nevery search is charged to the same meter as price comparison")
    meter = searches.SearchMeter(cap=10, state_path=os.path.join(tmp, "meter.json"))
    seeder6, _ = seeder_for(answers, payload, meter=meter,
                            cache_path=os.path.join(tmp, "five.json"))
    seeder6.resolve("玩具")
    check("one word costs exactly one search", meter.used == 1, str(meter.summary()))
    seeder6.resolve("玩具")
    check("a cached word costs nothing", meter.used == 1, str(meter.summary()))
    # CONTROL: the cap is a real ceiling, and it stops the hunt rather than
    # quietly eating the comparison budget
    small = searches.SearchMeter(cap=1, state_path=os.path.join(tmp, "meter2.json"))
    seeder7, _ = seeder_for(answers, payload, meter=small,
                            cache_path=os.path.join(tmp, "six.json"))
    seeder7.resolve("甲")
    try:
        seeder7.resolve("乙")
        check("CONTROL the monthly cap stops the seed hunt", False)
    except searches.OutOfSearches:
        check("CONTROL the monthly cap stops the seed hunt", True)
    check("CONTROL and nothing was spent past the cap", small.used == 1)

    print("\nwhich words become doors")
    rows = [
        {"name_zh": "鞋", "depth": 1, "state": "allowed"},
        {"name_zh": "成人用品", "depth": 1, "state": "blocked"},
        {"name_zh": "宗教用品", "depth": 1, "state": "review"},
        {"name_zh": "女装", "depth": 1, "state": "allowed"},
        {"name_zh": "半身裙", "depth": 2, "state": "allowed"},
        {"name_zh": "鞋", "depth": 1, "state": "allowed"},
    ]
    words = wordseed.words_from_categories(rows, depth=1)
    check("only allowed categories become doors", words == ["鞋", "女装"], str(words))
    check("CONTROL a blocked category never becomes a door",
          "成人用品" not in words and "宗教用品" not in words)
    check("depth selects the level asked for",
          wordseed.words_from_categories(rows, depth=2) == ["半身裙"])
    check("duplicates collapse",
          wordseed.words_from_categories(rows, depth=1).count("鞋") == 1)
    check("limit is honoured",
          wordseed.words_from_categories(rows, depth=1, limit=1) == ["鞋"])

    print("\nagainst the real category tree")
    with open(os.path.join(HERE, "data", "categories.json"), encoding="utf-8") as handle:
        real = json.load(handle)
    departments = wordseed.words_from_categories(real, depth=1)
    check("the whole market is 49 departments, not a million photographs",
          len(departments) == 49, str(len(departments)))
    check("and none of them is a banned one",
          not {"成人用品", "宗教用品", "医药、保养"} & set(departments))
    deeper = wordseed.words_from_categories(real, depth=2)
    check("going one level deeper is still under the monthly allowance",
          0 < len(departments) + len(deeper) < 30000, str(len(deeper)))

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
A word becomes a photograph, so the catalogue does not depend on anyone's camera.

This channel searches by picture. The keyword API exists but is ACL-declined
(`product.search.keywordQuery` -> "AppKey is not allowed(acl)", measured 30 Aug),
so until that permission lands there is no way to hand 1688 a word and be given
offers back.

There is a way round it. Google Images turns a word into pictures, and the
gateway accepts any public direct image URL - not only alicdn ones (measured
30 Aug across four hosts). So:

    word  ->  Google Images  ->  image URL  ->  similar-offer search  ->  a department

That makes the system general without a single photograph from the client, and
without waiting on the permission. The words themselves already exist: the 1688
category tree is readable (`alibaba.category.get` is granted), and it carries
1,481 allowed category names in Chinese.

Two things this module refuses to do:

  * accept a picture that has not been proved to open something. A candidate is
    only a seed once the gateway has returned offers for it. An image that looks
    right and returns nothing is not a door, and finding that out at 00:05 is
    too late.
  * spend searches off the books. Every Google Images call goes through the same
    monthly meter as price comparison, because they are the same 30,000. A seed
    hunt that quietly ate the comparison budget would show up as products
    published at full margin, which is the expensive kind of silent.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

import paths


class WordSeedError(Exception):
    pass


def cache_path() -> str:
    """Resolved per call - see the note in fx.cache_path()."""
    return paths.state_path("word_seeds.json", "KDX_WORD_SEEDS")


# --------------------------------------------------------------------------
# Turning a word into candidate pictures
# --------------------------------------------------------------------------

class GoogleImages:
    """
    SerpApi's google_images engine. Only the URLs are wanted from it.

    `original` is preferred over `thumbnail` deliberately: the thumbnail is a
    ~200px gstatic copy, and a small blurred picture opens a vaguer door. The
    thumbnail is kept as a fallback rather than dropped, because a vague door
    still beats no door.
    """

    ENDPOINT = "https://serpapi.com/search"

    def __init__(self, api_key: str = "", timeout: int = 60, opener=None):
        self.api_key = api_key or os.environ.get("KDX_SERPAPI_KEY", "")
        if not self.api_key:
            raise WordSeedError("no KDX_SERPAPI_KEY: a word cannot become a picture")
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def find(self, query: str, count: int = 5) -> list:
        url = f"{self.ENDPOINT}?" + urllib.parse.urlencode({
            "engine": "google_images", "q": query,
            "api_key": self.api_key, "hl": "zh-cn"})
        with self.opener(url, timeout=self.timeout) as response:
            payload = json.load(response)
        if payload.get("error"):
            raise WordSeedError(f"SerpApi: {payload['error']}")

        originals, thumbnails = [], []
        for row in payload.get("images_results", []):
            original = str(row.get("original") or "")
            thumbnail = str(row.get("thumbnail") or "")
            if original.startswith("http"):
                originals.append(original)
            elif thumbnail.startswith("http"):
                thumbnails.append(thumbnail)
        return (originals + thumbnails)[:count]


# --------------------------------------------------------------------------
# The cache: a word resolved once is resolved for good
# --------------------------------------------------------------------------

class SeedCache:
    def __init__(self, path: str = "", clock=time.time):
        self.path = path or cache_path()
        self.clock = clock
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            state = {}
        return state if isinstance(state, dict) else {}

    def save(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=1)

    def get(self, word: str) -> dict | None:
        row = self.state.get(word)
        return row if isinstance(row, dict) and row.get("seed") else None

    def put(self, word: str, seed: str, offers: int, tried: int) -> None:
        self.state[word] = {"seed": seed, "offers": offers, "tried": tried,
                            "at": int(self.clock())}
        self.save()

    def forget(self, word: str) -> None:
        """A seed that has gone dead is worse than no seed: it is a door that
        used to work, so nobody looks at it again."""
        self.state.pop(word, None)
        self.save()

    def seeds(self) -> list:
        return [row["seed"] for row in self.state.values()
                if isinstance(row, dict) and row.get("seed")]


# --------------------------------------------------------------------------
# Word -> proven seed
# --------------------------------------------------------------------------

class WordSeeder:
    def __init__(self, images, source, meter=None, cache: SeedCache | None = None,
                 candidates: int = 4):
        self.images = images
        self.source = source
        self.meter = meter
        self.cache = cache if cache is not None else SeedCache()
        self.candidates = candidates

    def _charge(self, note: str) -> None:
        if self.meter is not None:
            self.meter.spend(1, note=note)

    def resolve(self, word: str, query: str = "", force: bool = False) -> dict:
        """
        Give me a picture that opens this word's part of the market.

        Returns {word, seed, offers, tried, cached, error}. A word that cannot
        be opened returns seed=None and says why, rather than raising: one dead
        department must not stop the other forty-eight.
        """
        if not force:
            cached = self.cache.get(word)
            if cached:
                return {"word": word, "seed": cached["seed"], "cached": True,
                        "offers": cached.get("offers", 0), "tried": 0, "error": ""}

        # Charged outside the catch below, deliberately. Running out of the
        # monthly allowance is not "this word had no pictures" - it is the end
        # of the hunt, and it has to reach the caller. Folded into the same try,
        # it came back as a per-word failure and every remaining department
        # reported a Google Images problem that did not exist.
        self._charge(f"wordseed:{word}")
        try:
            urls = self.images.find(query or word, count=self.candidates)
        except Exception as exc:  # noqa: BLE001
            return {"word": word, "seed": None, "cached": False, "offers": 0,
                    "tried": 0, "error": f"no pictures for this word: {exc}"}

        tried = 0
        last = ""
        for url in urls:
            tried += 1
            try:
                found = self.source.search_by_image(url, page=1)
            except Exception as exc:  # noqa: BLE001
                last = str(exc)[:160]
                continue
            if not found:
                last = "the gateway accepted the picture but returned no offers"
                continue
            self.cache.put(word, url, len(found), tried)
            return {"word": word, "seed": url, "cached": False,
                    "offers": len(found), "tried": tried, "error": ""}

        return {"word": word, "seed": None, "cached": False, "offers": 0,
                "tried": tried,
                "error": last or "Google Images returned no usable picture"}


# --------------------------------------------------------------------------
# Which words
# --------------------------------------------------------------------------

def words_from_categories(rows: list, depth: int = 1, limit: int = 0) -> list:
    """
    The department names, in Chinese, from the tree we already hold.

    Only `allowed` categories become doors. A blocked category is filtered again
    later on the offer itself - that filter is the one that matters, because an
    offer can sit in an allowed category and still be a banned product - but
    there is no reason to spend a search opening a door we would then close.
    """
    out = []
    for row in rows:
        if row.get("state") != "allowed":
            continue
        if depth and int(row.get("depth") or 0) != depth:
            continue
        name = str(row.get("name_zh") or "").strip()
        if name and name not in out:
            out.append(name)
    return out[:limit] if limit else out

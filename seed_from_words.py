"""
Open the whole market from words, with no photographs from anyone.

    python3 seed_from_words.py --departments          # the 49 top-level ones
    python3 seed_from_words.py --departments --limit 5
    python3 seed_from_words.py --words my_words.txt
    python3 seed_from_words.py --departments --out /opt/kdx/seeds.txt

Each word costs exactly one Google Images search, once, ever - the answer is
cached, so running this again is free for every word already opened. Those
searches come out of the same monthly SerpApi allowance as price comparison,
and are charged to the same meter, so nothing is spent off the books.

A word is only written to the seed file after the gateway has actually returned
offers for its picture. A picture that opens nothing never becomes a seed.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import aop_client  # noqa: E402
import searches  # noqa: E402
import source as source_module  # noqa: E402
import wordseed  # noqa: E402


def load_categories() -> list:
    path = os.environ.get("KDX_CATEGORIES", os.path.join(HERE, "data", "categories.json"))
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_words(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle
                if line.strip() and not line.startswith("#")]


def parse(argv: list) -> dict:
    options = {"departments": False, "words": "", "out": "", "limit": 0,
               "depth": 1, "force": False}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--departments":
            options["departments"] = True
        elif item == "--force":
            options["force"] = True
        elif item in ("--words", "--out", "--limit", "--depth"):
            index += 1
            if index >= len(argv):
                raise SystemExit(f"{item} needs a value")
            options[item[2:]] = argv[index]
        else:
            raise SystemExit(f"unknown argument: {item}")
        index += 1
    options["limit"] = int(options["limit"])
    options["depth"] = int(options["depth"])
    return options


def main(argv: list) -> int:
    options = parse(argv)
    if options["words"]:
        words = read_words(options["words"])
    elif options["departments"]:
        words = wordseed.words_from_categories(
            load_categories(), depth=options["depth"], limit=options["limit"])
    else:
        raise SystemExit(__doc__)

    meter = searches.build_meter()
    if meter is not None:
        before = meter.summary()
        print(f"SerpApi this month: {before['used']}/{before['cap']} used, "
              f"{before['remaining']} left")
        if meter.remaining() < len(words):
            print(f"WARNING: {len(words)} words but only {meter.remaining()} "
                  f"searches left - it will stop when the allowance runs out")

    seeder = wordseed.WordSeeder(
        images=wordseed.GoogleImages(),
        source=source_module.LinkPlusSource(aop_client.build_pool_from_env()),
        # Passing the meter is the whole point of building it. Left out, the
        # first live run opened five departments and still reported 0/30000
        # used - five searches spent off the books, and the only way to see it
        # was to read the number at the bottom of a real run.
        meter=meter)

    print(f"\n{len(words)} word(s)\n")
    opened, failed, reused = [], [], 0
    for index, word in enumerate(words, start=1):
        try:
            result = seeder.resolve(word, force=options["force"])
        except searches.OutOfSearches as exc:
            print(f"stopped at word {index}: {exc}")
            break
        if result["seed"]:
            opened.append(result)
            if result["cached"]:
                reused += 1
                print(f"{index:>3}. {word}  (already open)")
            else:
                print(f"{index:>3}. {word}  -> {result['offers']} offers"
                      f"  (picture {result['tried']} of those tried)")
        else:
            failed.append(result)
            print(f"{index:>3}. {word}  -- NOT OPENED: {result['error'][:110]}")

    seeds = [result["seed"] for result in opened]
    unique = sorted(set(seeds))
    if len(unique) != len(seeds):
        print(f"\nnote: {len(seeds) - len(unique)} word(s) resolved to a picture "
              f"another word had already used - the duplicates were dropped")

    destination = options["out"] or os.environ.get("KDX_SEEDS", "")
    if destination:
        directory = os.path.dirname(destination)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write("# written by seed_from_words.py - one proven picture "
                         "per department\n")
            for result in opened:
                handle.write(f"# {result['word']}\n{result['seed']}\n")
        print(f"\n{len(unique)} seed(s) written to {destination}")
    else:
        print("\nno --out and no KDX_SEEDS: nothing written")

    print(f"{len(opened)} opened ({reused} already known), {len(failed)} not opened")
    if meter is not None:
        after = meter.summary()
        print(f"SerpApi now: {after['used']}/{after['cap']} used, "
              f"{after['remaining']} left")
    return 0 if opened else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

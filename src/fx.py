"""
Daily CNY -> SAR rate.

Every published price is this number multiplied by a 1688 price, so a wrong rate
does not cause one bad product - it mis-prices the entire night's catalogue.
That is why this module refuses to guess:

  - it reads two independent sources and requires them to agree;
  - it rejects any rate outside a plausible band;
  - if it cannot get a trustworthy number it RAISES rather than falling back to
    a stale one, so the night's run stops instead of publishing wrong prices.

The cache exists so a source outage mid-run cannot change prices halfway
through, not to paper over a failed fetch.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from decimal import Decimal

CACHE_PATH = os.environ.get("KDX_FX_CACHE", "/opt/kdx/fx_rate.json")

# SAR is pegged to USD, and CNY/USD has not left this range in decades. Anything
# outside it means the source changed shape or returned a different pair.
MIN_RATE = Decimal("0.35")
MAX_RATE = Decimal("0.80")

# Two sources may legitimately differ by a small margin; more than this means one
# of them is stale or wrong, and we must not pick a winner by ourselves.
MAX_DISAGREEMENT = Decimal("0.02")   # 2%

SOURCES = [
    ("open.er-api.com", "https://open.er-api.com/v6/latest/CNY",
     lambda data: data["rates"]["SAR"]),
    ("floatrates.com", "https://www.floatrates.com/daily/cny.json",
     lambda data: data["sar"]["rate"]),
    ("currency-api", "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest"
                     "/v1/currencies/cny.json",
     lambda data: data["cny"]["sar"]),
]


class FxError(RuntimeError):
    pass


def _fetch(url: str, extract, timeout: int = 25) -> Decimal:
    request = urllib.request.Request(url, headers={"User-Agent": "kdx-pipeline/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return Decimal(str(extract(data)))


def _plausible(rate: Decimal) -> bool:
    return MIN_RATE <= rate <= MAX_RATE


def fetch_rate(min_agreeing: int = 2) -> tuple[Decimal, list]:
    """
    Returns (rate, provenance). Raises FxError unless at least `min_agreeing`
    independent sources produce a plausible rate that agrees with the others.
    """
    readings, failures = [], []
    for name, url, extract in SOURCES:
        try:
            rate = _fetch(url, extract)
        except Exception as exc:  # noqa: BLE001 - any failure is just one source down
            failures.append(f"{name}: {exc}")
            continue
        if not _plausible(rate):
            failures.append(f"{name}: implausible rate {rate}")
            continue
        readings.append((name, rate))

    if len(readings) < min_agreeing:
        raise FxError(f"only {len(readings)} usable FX source(s); {failures}")

    rates = [rate for _, rate in readings]
    spread = (max(rates) - min(rates)) / min(rates)
    if spread > MAX_DISAGREEMENT:
        raise FxError(f"FX sources disagree by {spread:.2%}: {readings}")

    # Median, so one outlier among three cannot drag the number.
    chosen = sorted(rates)[len(rates) // 2]
    return chosen, readings


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def load_cached() -> dict | None:
    try:
        with open(CACHE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def save_cache(rate: Decimal, readings: list) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump({"date": _today(),
                   "rate": str(rate),
                   "sources": {name: str(value) for name, value in readings},
                   "fetched_at": int(time.time())}, handle, indent=1)


def rate_for_today(force_refresh: bool = False) -> Decimal:
    """
    The rate the whole night's run must use. Fetched once per day and then held,
    so prices cannot shift halfway through a run.
    """
    cached = load_cached()
    if not force_refresh and cached and cached.get("date") == _today():
        rate = Decimal(cached["rate"])
        if _plausible(rate):
            return rate

    rate, readings = fetch_rate()
    save_cache(rate, readings)
    return rate


if __name__ == "__main__":
    value, provenance = fetch_rate()
    print(f"1 CNY = {value} SAR")
    for name, reading in provenance:
        print(f"  {name}: {reading}")

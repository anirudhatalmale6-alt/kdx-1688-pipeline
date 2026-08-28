"""
Offline proof for the pieces that do not need any credentials:
the daily point budget, the audit log, and the FX guards.

    python3 verify_core.py

No network except one live FX read at the end, which is skipped with --offline.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


def test_budget(tmp: str) -> None:
    import budget as budget_module

    state = os.path.join(tmp, "points.json")
    quota = budget_module.PointBudget(daily=300, state_path=state)

    print("1. the daily quota is enforced")
    check("starts with the full 300", quota.remaining() == 300)
    for _ in range(299):
        quota.spend()
    check("299 spent leaves 1", quota.remaining() == 1, str(quota.remaining()))
    quota.spend()
    check("300 spent leaves 0", quota.remaining() == 0)
    try:
        quota.spend()
        check("the 301st point is refused", False, "it was allowed")
    except budget_module.OutOfPoints:
        check("the 301st point is refused", True)

    print("2. a restart must not hand itself a fresh 300")
    reopened = budget_module.PointBudget(daily=300, state_path=state)
    check("count survives a restart", reopened.remaining() == 0, str(reopened.remaining()))

    print("3. a new day resets the count")
    reopened.state["day"] = (datetime.now(budget_module.RIYADH) - timedelta(days=1)).strftime("%Y-%m-%d")
    check("yesterday's state rolls over", reopened.remaining() == 300, str(reopened.remaining()))
    check("yesterday is kept in history",
          reopened.state["history"].get(
              (datetime.now(budget_module.RIYADH) - timedelta(days=1)).strftime("%Y-%m-%d")) == 300,
          str(reopened.state.get("history")))

    print("4. the reset happens at midnight in Riyadh, not UTC")
    now = datetime.now(budget_module.RIYADH)
    seconds = reopened.seconds_until_next_day()
    check("reset is under 24h away", 0 < seconds <= 86400, str(seconds))
    check("reset lands on 00:00 local",
          (now + timedelta(seconds=seconds)).strftime("%H:%M") == "00:00",
          (now + timedelta(seconds=seconds)).strftime("%H:%M"))


def test_audit(tmp: str) -> None:
    import audit as audit_module
    from rules import AuditRecord

    print("5. every product leaves exactly one row")
    log = audit_module.AuditLog(path=os.path.join(tmp, "audit.csv"))
    log.write(AuditRecord(offer_id="1", sku_id="1-A", decision="published",
                          reason_code="published", reason_ar="", final_price_sar="25.69"), points_spent=1)
    log.write(AuditRecord(offer_id="2", sku_id="2-A", decision="rejected",
                          reason_code="heavy_unmatched", reason_ar=""), points_spent=1)
    log.write(AuditRecord(offer_id="3", sku_id="3-A", decision="rejected",
                          reason_code="banned_category", reason_ar=""), points_spent=0)

    with open(log.path, encoding="utf-8-sig") as handle:
        lines = [line for line in handle.read().splitlines() if line.strip()]
    check("header plus three rows", len(lines) == 4, f"{len(lines)} lines")
    check("counts group by decision", log.counts() == {"published": 1, "rejected": 2}, str(log.counts()))

    print("6. a rejection reason is filled in Arabic when the engine leaves it blank")
    check("heavy_unmatched translated", "أثقل من 2 كجم" in lines[2], lines[2][:80])
    check("banned_category translated", "فئة ممنوعة" in lines[3], lines[3][:80])

    print("7. reopening appends, never re-writes the header")
    audit_module.AuditLog(path=log.path).write(
        AuditRecord(offer_id="4", sku_id="4-A", decision="updated", reason_code="updated", reason_ar=""))
    with open(log.path, encoding="utf-8-sig") as handle:
        lines = [line for line in handle.read().splitlines() if line.strip()]
    check("one header, five rows total", len(lines) == 5 and lines[0].startswith("timestamp"), f"{len(lines)}")


def test_fx(tmp: str, offline: bool) -> None:
    import fx as fx_module

    fx_module.CACHE_PATH = os.path.join(tmp, "fx.json")

    print("8. controls: a bad rate must stop the run, never be averaged in")
    real_sources = list(fx_module.SOURCES)
    fx_module.SOURCES = [("nonsense", "https://open.er-api.com/v6/latest/CNY", lambda d: 99),
                         ("also", "https://open.er-api.com/v6/latest/CNY", lambda d: 0.0001)]
    try:
        fx_module.fetch_rate()
        check("implausible rates refused", False, "accepted")
    except fx_module.FxError:
        check("implausible rates refused", True)

    fx_module.SOURCES = [("a", "https://open.er-api.com/v6/latest/CNY", lambda d: 0.40),
                         ("b", "https://open.er-api.com/v6/latest/CNY", lambda d: 0.70)]
    try:
        fx_module.fetch_rate()
        check("disagreeing sources refused", False, "accepted")
    except fx_module.FxError:
        check("disagreeing sources refused", True)

    fx_module.SOURCES = real_sources
    if offline:
        print("  SKIP  live rate (--offline)")
        return
    print("9. the live rate is sane and cached for the day")
    rate = fx_module.rate_for_today()
    check("rate is plausible", fx_module.MIN_RATE < rate < fx_module.MAX_RATE, str(rate))
    check("cache holds today's date",
          (fx_module.load_cached() or {}).get("date") == fx_module._today())
    check("second read uses the cache", fx_module.rate_for_today() == rate)


def main() -> int:
    offline = "--offline" in sys.argv
    with tempfile.TemporaryDirectory() as tmp:
        test_budget(tmp)
        test_audit(tmp)
        test_fx(tmp, offline)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

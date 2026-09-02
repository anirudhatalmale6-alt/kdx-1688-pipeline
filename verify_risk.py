"""
Proof for the 1688 listing-risk reading.

    python3 verify_risk.py

No network, no credentials. The recorded bodies below are the real ones: the
gateway's answers to fenxiao.risk.queryGoodsRisk on 2 September 2026, taken from
the client's own second app.

What this suite is really guarding is a claim I got wrong and had to correct in
front of the client. The API's name reads like a per-product risk check, and I
told him it would back up the banned-term filter. It does not: it takes no offer
id, only two counts of our own listing volume, and it answers about the account.
The assertions below hold that correction in place.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

import risk  # noqa: E402
from aop_client import AopError  # noqa: E402

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


class Recorded:
    """Answers with a fixed payload and remembers what it was asked."""

    def __init__(self, payload=None, raises=None):
        self.payload = payload
        self.raises = raises
        self.calls = []

    def call(self, route, params=None, authed=True):
        self.calls.append((route, dict(params or {})))
        if self.raises is not None:
            raise self.raises
        return self.payload


NO_RISK = {"result": {"data": {"riskLevel": "无"}, "errorCode": "0", "success": True}}
HIGH = {"result": {"data": {"riskLevel": "高"}, "errorCode": "0", "success": True}}
MEDIUM = {"result": {"data": {"riskLevel": "中"}, "errorCode": "0", "success": True}}
REFUSED = {"result": {"errorCode": "301", "errorInfo": "风险发布量不能为空",
                      "success": False}}


def main() -> int:
    print("1. the shape of the question, which is not the shape I first claimed")
    client = Recorded(NO_RISK)
    reading = risk.check(client, published_today=20, on_sale=74)
    route, params = client.calls[0]
    check("it is asked on the distribution namespace",
          route.namespace == "com.alibaba.fenxiao", route.namespace)
    check("the two counts go in the documented argument",
          '"publishCount": 20' in params["goodsRiskQueryParam"]
          and '"onCount": 74' in params["goodsRiskQueryParam"],
          params["goodsRiskQueryParam"])
    # The correction, asserted rather than written in a comment: there is no
    # product in this question, so nothing this returns can be a verdict on one.
    check("CONTROL no offer id is sent, because the API takes none",
          "offerId" not in params["goodsRiskQueryParam"]
          and not any("offer" in str(key).lower() for key in params),
          str(params))

    print("2. reading the four levels")
    check("无 is read as none", reading["level"] == "none", str(reading))
    check("and the Chinese it came as is kept for the report",
          reading["raw"] == "无", str(reading))
    check("高 is read as high",
          risk.check(Recorded(HIGH), 1, 1)["level"] == "high")
    check("中 is read as medium",
          risk.check(Recorded(MEDIUM), 1, 1)["level"] == "medium")
    check("the numbers it was taken from travel with it",
          reading["asked"] == {"publishCount": 20, "onCount": 74}, str(reading))

    print("3. a reading we could not take is not a reading of 'no risk'")
    # The distinction the whole module turns on. If a failure answered "none"
    # the shop would publish straight through an outage believing it had been
    # cleared to.
    dead = risk.check(Recorded(raises=AopError("gateway timeout")), 5, 5)
    check("a gateway failure gives no level at all", dead["level"] is None, str(dead))
    check("and says why", "timeout" in dead["error"], str(dead))
    refused = risk.check(Recorded(REFUSED), 5, 5)
    check("a business refusal also gives no level", refused["level"] is None, str(refused))
    check("and carries 1688's own words", "风险发布量" in refused["error"], str(refused))
    silent = risk.check(Recorded({"result": {"data": {}, "success": True}}), 5, 5)
    check("a success that names no level is not a level either",
          silent["level"] is None, str(silent))

    print("4. what stops a run and what must never stop one")
    check("high stops it", risk.should_stop({"level": "high"}))
    check("medium stops it too", risk.should_stop({"level": "medium"}))
    check("CONTROL low does not", not risk.should_stop({"level": "low"}))
    check("CONTROL none does not", not risk.should_stop({"level": "none"}))
    # An unreachable gateway must not be able to close the shop for a day, and a
    # level 1688 invents next year is not evidence of danger.
    check("CONTROL an unread level does not stop the run",
          not risk.should_stop({"level": None}))
    check("CONTROL a level this module has never heard of does not either",
          not risk.should_stop({"level": "unknown"}))
    unknown = risk.check(Recorded({"result": {"data": {"riskLevel": "紫"},
                                              "success": True}}), 5, 5)
    check("an unrecognised level is labelled, not guessed at",
          unknown["level"] == "unknown" and unknown["raw"] == "紫", str(unknown))

    print("5. the client can lift the halt without editing code")
    os.environ["KDX_IGNORE_RISK"] = "1"
    try:
        check("KDX_IGNORE_RISK=1 stops the halt", not risk.should_stop({"level": "high"}))
        # And it must not stop the reading: the number is what tells him whether
        # turning the guard off was a good idea.
        still = risk.check(Recorded(HIGH), 1, 1)
        check("CONTROL but the level is still read and reported",
              still["level"] == "high", str(still))
    finally:
        del os.environ["KDX_IGNORE_RISK"]
    check("CONTROL and the halt comes back when it is unset",
          risk.should_stop({"level": "high"}))

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

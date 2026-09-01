"""
Proof for the two-app router, offline.

    python3 verify_pool.py

The pipeline has to hold two 1688 apps at once: the old one owns the image
search the price comparison runs on, the new one owns the product detail and
SKU tables. Which app owns which API is a table I wrote by hand, so the checks
below are mostly about what happens when that table is WRONG.

The dangerous mistake would be a blind retry. An ACL decline is safe to repeat
against the other app because the gateway refused before the API ran; a
business error or a timeout is not, because the call may well have happened.
So the last two checks are the important ones: they prove the fallback stays
asleep for anything that is not an ACL decline.

No network: the clients here are stubs that record what they were asked for.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from aop_client import ApiRoute, AopError, ClientPool  # noqa: E402

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


class Stub:
    """A client that answers, or raises whatever it was told to raise."""

    def __init__(self, name: str, error: Exception | None = None):
        self.name = name
        self.error = error
        self.calls: list = []

    def call(self, route, params=None, authed=True):
        self.calls.append(f"{route.namespace}/{route.api_name}")
        if self.error:
            raise self.error
        return {"answered_by": self.name}


ACL = AopError("HTTP 400 gw.APIACLDecline: AppKey is not allowed(acl)",
               {"error_code": "gw.APIACLDecline",
                "error_message": "AppKey is not allowed(acl)"})
BUSINESS = AopError("gw.ParamMissing: Required argument offerId",
                    {"error_code": "gw.ParamMissing",
                     "error_message": "Required argument offerId"})

IMAGE = ApiRoute(namespace="com.alibaba.linkplus",
                 api_name="alibaba.cross.similar.offer.search")
DETAIL = ApiRoute(namespace="com.alibaba.fenxiao",
                  api_name="alibaba.pifatuan.product.detail.list")
SKU = ApiRoute(namespace="com.alibaba.product", api_name="product.skuinfo.get")
PRODUCT = ApiRoute(namespace="com.alibaba.product", api_name="alibaba.product.get")
QUERY_DETAIL = ApiRoute(namespace="com.alibaba.fenxiao.crossborder",
                        api_name="product.search.queryProductDetail")


def pool(primary=None, second=None) -> ClientPool:
    clients = {"primary": primary or Stub("primary")}
    if second is not None:
        clients["fenxiao"] = second
    return ClientPool(clients, default="primary")


print("routing, when the table is right")
one = pool(second=Stub("fenxiao"))
check("image search goes to the old app",
      one.call(IMAGE)["answered_by"] == "primary")
check("product detail goes to the new app",
      one.call(DETAIL)["answered_by"] == "fenxiao")
# com.alibaba.product is split between the two apps, so the namespace alone
# cannot decide it - this is the case a namespace-only table would get wrong.
check("product.skuinfo.get goes to the new app (per-API override)",
      one.call(SKU)["answered_by"] == "fenxiao")
check("alibaba.product.get stays on the old app, same namespace",
      one.call(PRODUCT)["answered_by"] == "primary")
# crossborder is split the other way round: the namespace belongs to the new app
# but this one API is still ACL-declined there, measured 2026-09-01.
check("queryProductDetail stays on the old app despite its namespace",
      one.call(QUERY_DETAIL)["answered_by"] == "primary")

print("\none app only: nothing changes until the second is configured")
alone = pool()
check("every route goes to the one app", alone.call(DETAIL)["answered_by"] == "primary")
try:
    pool(primary=Stub("primary", error=ACL)).call(IMAGE)
    check("an ACL decline with no second app is raised", False, "no exception")
except AopError:
    check("an ACL decline with no second app is raised", True)

print("\nwhen my routing table is wrong")
declining, answering = Stub("primary", error=ACL), Stub("fenxiao")
wrong = pool(primary=declining, second=answering)
check("a misrouted call recovers on the other app",
      wrong.call(IMAGE)["answered_by"] == "fenxiao")
check("and the pool remembers, so the second call goes straight there",
      wrong.label_for(IMAGE) == "fenxiao")
before = len(declining.calls)
wrong.call(IMAGE)
check("the app that already refused is not asked again",
      len(declining.calls) == before,
      f"asked {len(declining.calls) - before} more times")

print("\nthe fallback must stay asleep for anything that is not an ACL decline")
# This is the check that matters. A business error means the API ran; repeating
# it against another app would be a second real call, and for anything that
# writes, a second real effect.
busy, spare = Stub("primary", error=BUSINESS), Stub("fenxiao")
try:
    pool(primary=busy, second=spare).call(IMAGE)
    check("a business error is not retried elsewhere", False, "no exception raised")
except AopError:
    check("a business error is raised, not retried", True)
check("the other app was never called for a business error",
      spare.calls == [], f"it was called {len(spare.calls)} times")

timing_out, spare2 = Stub("primary", error=AopError("request failed after 3 attempts: timeout")), Stub("fenxiao")
try:
    pool(primary=timing_out, second=spare2).call(IMAGE)
    check("a timeout is not retried elsewhere", False, "no exception raised")
except AopError:
    check("a timeout is raised, not retried", True)
check("the other app was never called for a timeout",
      spare2.calls == [], f"it was called {len(spare2.calls)} times")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)

"""
What 1688 thinks of the rate we are publishing at.

`fenxiao.risk.queryGoodsRisk` - 通淘铺货风险预警 - is not what its name suggested
when the client first sent the permission list, and the correction matters
enough to write down. It takes no offer id at all. Its two arguments are

    publishCount   商品数量 newly published today
    onCount        商品数量 currently on sale

and it answers with one word, 风险等级: 无 / 低 / 中 / 高. So it is a warning
about *us* - whether the volume this account is listing puts it at risk - and
never a verdict on a product. It cannot back the banned-term filter, which is
what I told the client on 2 September before calling it. Reported and corrected
the same day.

It is worth having anyway. The client's 1688 account is the thing this whole
system depends on, and an early "中" is the difference between slowing down and
losing it.

Read-only, and cheap: one call per run.
"""

from __future__ import annotations

import json
import os

from aop_client import AopError, ApiRoute

ROUTE = ApiRoute(namespace="com.alibaba.fenxiao", api_name="fenxiao.risk.queryGoodsRisk")

# 1688 answers in Chinese. These are the four documented levels, in order.
LEVELS = {"无": "none", "低": "low", "中": "medium", "高": "high"}

# Above this we stop for the day rather than keep listing. "中" is the first
# level that is not an all-clear, and the client would rather lose an afternoon
# of publishing than the account that feeds it.
STOP_AT = ("medium", "high")


def check(client, published_today: int, on_sale: int) -> dict:
    """
    Ask 1688 whether today's publishing rate looks risky.

    Never raises. A run must not die because an advisory call timed out, and a
    reading we could not take has to be distinguishable from a reading of "no
    risk" - so a failure answers level None with the reason, not "none".
    """
    argument = json.dumps({"publishCount": int(published_today),
                           "onCount": int(on_sale)}, ensure_ascii=False)
    try:
        payload = client.call(ROUTE, {"goodsRiskQueryParam": argument})
    except (AopError, Exception) as exc:                   # noqa: BLE001
        return {"level": None, "raw": "", "error": str(exc)[:200],
                "asked": {"publishCount": published_today, "onCount": on_sale}}

    result = payload.get("result") or {}
    if not result.get("success"):
        return {"level": None, "raw": "",
                "error": str(result.get("errorInfo") or result.get("errorCode") or payload)[:200],
                "asked": {"publishCount": published_today, "onCount": on_sale}}

    raw = str(((result.get("data") or {}).get("riskLevel") or "")).strip()
    return {"level": LEVELS.get(raw, "unknown" if raw else None), "raw": raw,
            "error": "" if raw else "the call succeeded and named no level",
            "asked": {"publishCount": published_today, "onCount": on_sale}}


def should_stop(reading: dict) -> bool:
    """
    Only a level we actually read, and actually recognise, stops a run.

    An unreachable gateway must not halt publishing - that would hand every
    network blip the power to close the shop for a day - and neither must a
    level 1688 introduces later that this module has never heard of, because
    "unknown" is not evidence of danger.

    KDX_IGNORE_RISK=1 turns the halt off without turning the reading off, so the
    number still reaches the report. A guard the client cannot lift from the
    environment is a guard that will be lifted by editing the code at the worst
    possible moment.
    """
    if os.environ.get("KDX_IGNORE_RISK") == "1":
        return False
    return reading.get("level") in STOP_AT

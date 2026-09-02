"""
Which APIs does a given 1688 appKey actually own?

    KDX_1688_APP_KEY=... KDX_1688_APP_SECRET=... KDX_1688_TOKEN=... \
        python3 probe_app.py

Written for the second app the client is having approved. Its permission list
arrived as a table of API *names* with no namespaces, and the gateway path is
`param2/1/<namespace>/<api>/<appKey>` - so the namespace has to be recovered
before anything can be called. The gateway hands us that for free:

    gw.APIUnsupported   this namespace/name pair does not exist -> wrong guess
    gw.APIACLDecline    the pair exists, this app has no permission
    gw.ParamMissing     the pair exists AND this app may call it
    (anything else)     called it, got a business answer

Note the third line. A permission check does not need correct parameters,
because the ACL is evaluated *before* argument validation - so every probe here
is sent with no business parameters at all. That is also what makes it safe:
a call that stops at "you forgot an argument" cannot have done anything.

Run it against the OLD app to learn which names are real; run it again against
the NEW app to learn which of those it owns. The difference is the answer.

SAFETY - this file probes read-only APIs only.
Never add an API that creates, pays for, cancels, follows, clears or feeds
back anything. The permission list contains several that would act for real:

    alibaba.trade.fastCreateOrder            creates a purchase order
    alibaba.trade.pay.protocolPay.preparePay starts a password-free payment
    alibaba.fenxiao.chosen.offerlist.removeall   clears the distribution list
    fenxiao.distributebill.removeall         clears the distribution bills
    alibaba.trade.cancel / createRefund      cancels / refunds a real order
    alibaba.product.follow / unfollow        writes to the account
    supply.task.stop / startTask             starts and stops account tasks
    trade.invoice.apply                      applies for a real invoice

Those are listed here so the next person can see they were considered and
deliberately left out, not overlooked. A missing parameter would not save us:
some of them default.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GATEWAY = "https://gw.open.1688.com/openapi"
FAKE_TOKEN = "00000000-0000-0000-0000-000000000000"
PAUSE = float(os.environ.get("KDX_PROBE_PAUSE", "1.2"))

# (api name, candidate namespaces, what it would give the pipeline)
#
# The namespaces below are not guesses any more. The permission table arrived
# without them, so on 2026-08-31 this probe was swept against the OLD app over
# eight candidate namespaces until every name stopped answering
# gw.APIUnsupported. The answer turned out to be boring: the whole distribution
# package lives under com.alibaba.fenxiao, not under the .crossborder
# namespace the older detail APIs use. Each name below is one the gateway has
# confirmed exists. The first candidate is the confirmed one; any second entry
# is kept only as a fallback in case 1688 moves it.
FENXIAO = "com.alibaba.fenxiao"
CROSSBORDER = "com.alibaba.fenxiao.crossborder"
PROBES = [
    # --- the two blockers, in the names the new list uses -------------------
    ("alibaba.pifatuan.product.detail.list", [FENXIAO],
     "product detail in batches: gallery, description, weight"),
    ("product.skuinfo.get", ["com.alibaba.product", FENXIAO],
     "SKU table: sizes and colours, the thing the shop cannot sell without"),

    # --- what we pay SerpApi for today -------------------------------------
    ("product.keywords.search", [FENXIAO],
     "keyword search inside 1688 - would end the external search bill"),
    ("jxhy.product.getPageList", [FENXIAO],
     "browse the selected-supply catalogue by page"),
    ("jxhy.productFilter.get", [FENXIAO],
     "the filter values that catalogue accepts"),
    ("pool.product.pull", [CROSSBORDER, FENXIAO],
     "pull products out of a product pool"),

    # --- price comparison ---------------------------------------------------
    ("alibaba.pifatuan.product.match.get", [FENXIAO],
     "same-item matching - the price comparison we do by image today"),
    ("supply.similarOffer.search", [FENXIAO],
     "similar offers from other suppliers, same purpose"),
    ("alibaba.cross.similar.offer.search", ["com.alibaba.linkplus"],
     "CONTROL: the image search the OLD app owns and the new list does not"),

    # --- the old names, to see whether the new app carries them too ---------
    ("product.search.queryProductDetail", [CROSSBORDER],
     "the detail API we asked for by name"),
    ("alibaba.product.get", ["com.alibaba.product"],
     "the fallback detail API we asked for by name"),
    ("alibaba.category.attribute.get", ["com.alibaba.product"],
     "category attributes: size, colour, voltage"),
    ("alibaba.category.get", ["com.alibaba.product"],
     "CONTROL: category tree, known to work on the old app"),

    # --- shipping cost, which we currently guess at 1 kg --------------------
    ("alibaba.logistics.myFreightTemplate.list.get", ["com.alibaba.logistics"],
     "real freight templates instead of the 1 kg assumption"),
    ("alibaba.trade.addresscode.get", ["com.alibaba.trade"],
     "address code table, needed before any freight quote"),

    # --- read-only order and tracking, for the fulfilment screens -----------
    ("alibaba.trade.getBuyerOrderList", ["com.alibaba.trade"],
     "read our own purchase orders"),
    ("alibaba.trade.getLogisticsTraceInfo.buyerView", ["com.alibaba.logistics"],
     "shipment tracking - screen 5 of the prototype"),

    # --- identity and plumbing ---------------------------------------------
    ("alibaba.account.basic", ["com.alibaba.account"],
     "which 1688 account this token actually belongs to"),
    ("dkey.get", [FENXIAO],
     "the dkey several distribution APIs want as an argument"),
    ("alibaba.fenxiao.chosen.offerlist.get", [FENXIAO],
     "the offers already selected for distribution"),
    ("fenxiao.aimaterial.getDetail", [FENXIAO],
     "1688's own AI copy and images for a product"),
    ("fenxiao.hitlab.queryHitLabItem", [FENXIAO],
     "best-seller detail, useful for choosing what to import"),

    # --- added 2 September, from the full permission list the client sent ---
    # He asked why several names on it had never been requested. These four are
    # the ones that would change what the pipeline can do, and all four only
    # read. The rest of the list is ordering, paying, refunding and invoicing,
    # which belong to a later phase and are deliberately not probed.
    ("fenxiao.risk.queryGoodsRisk", [FENXIAO],
     "1688's own risk verdict on a product - would back up our ban filter"),
    ("fenxiao.brand.queryAuth", [FENXIAO],
     "whether we are allowed to resell a branded product"),
    ("supply.offer.fetchIdList", [FENXIAO],
     "offer ids in bulk, a second discovery channel"),
    ("open.agent.deepSearch", [FENXIAO],
     "1688's own deep search"),
]

VERDICTS = {
    "gw.APIUnsupported": "NO SUCH API",
    "gw.APIACLDecline": "NEEDS PERMISSION",
    "gw.ParamMissing": "ALLOWED",
    "401": "TOKEN REJECTED",
}


def call(namespace: str, api: str, token: str) -> str:
    """Signed POST with no business parameters. Returns the raw body."""
    app_key = os.environ["KDX_1688_APP_KEY"]
    secret = os.environ["KDX_1688_APP_SECRET"]
    params = {"access_token": token}
    url_path = f"param2/1/{namespace}/{api}/{app_key}"
    joined = "".join(f"{key}{params[key]}" for key in sorted(params))
    params["_aop_signature"] = hmac.new(
        secret.encode(), (url_path + joined).encode(), hashlib.sha1).hexdigest().upper()
    request = urllib.request.Request(
        f"{GATEWAY}/{url_path}",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error_code": "network", "error_message": str(exc)})


def classify(body: str) -> tuple[str, str]:
    try:
        parsed = json.loads(body)
    except ValueError:
        return "ALLOWED", body[:90]
    code = str(parsed.get("error_code", ""))
    message = str(parsed.get("error_message", ""))[:90]
    # No error_code at all means the call went through and answered.
    if not code:
        return "ALLOWED", "answered"
    if code in VERDICTS:
        return VERDICTS[code], message or code
    # A gw.* code is the gateway refusing us before the API ran, so it is never
    # permission to call anything. This branch exists because the first version
    # fell through to ALLOWED for every unrecognised code, and on 2026-08-31 a
    # deliberately wrong secret proved what that costs: gw.SignatureInvalid was
    # reported as ALLOWED, so a single typo in the secret would have printed a
    # clean sheet of twenty-two granted APIs. A wrong key must never read as a
    # granted permission.
    if code.startswith("gw."):
        return "GATEWAY ERROR", message or code
    # Anything else is the API itself answering - a business error, which means
    # the call got through the ACL.
    return "ALLOWED", message or code


def main() -> int:
    for name in ("KDX_1688_APP_KEY", "KDX_1688_APP_SECRET", "KDX_1688_TOKEN"):
        if not os.environ.get(name):
            raise SystemExit(f"set {name}")
    token = os.environ["KDX_1688_TOKEN"]
    print(f"appKey {os.environ['KDX_1688_APP_KEY']}\n")

    # Pre-flight. A mistyped secret signs every request wrongly, and the gateway
    # would answer gw.SignatureInvalid to all twenty-two probes - a uniform
    # failure that looks exactly like "this app owns nothing". Catch it here, on
    # one call, rather than letting it masquerade as a permission verdict.
    verdict, detail = classify(call("com.alibaba.product", "alibaba.product.get", token))
    if verdict == "GATEWAY ERROR" and "ignature" in detail:
        raise SystemExit(f"the appKey and appSecret do not match: {detail}\n"
                         "nothing below would mean anything, so nothing was run.")

    allowed, blocked, missing, other = [], [], [], []
    for api, namespaces, purpose in PROBES:
        for namespace in namespaces:
            verdict, detail = classify(call(namespace, api, token))
            time.sleep(PAUSE)
            if verdict != "NO SUCH API":
                break  # found the real namespace; no point trying the others
        print(f"  {verdict:<17} {namespace}/{api}")
        print(f"  {'':<17} {purpose}")
        if verdict != "ALLOWED":
            print(f"  {'':<17} -> {detail}")
        print()
        {"ALLOWED": allowed, "NEEDS PERMISSION": blocked,
         "NO SUCH API": missing}.get(verdict, other).append(f"{namespace}/{api}")

    # A run where everything says NEEDS PERMISSION would look identical to a
    # run with a broken signature, so prove the token authenticates at all.
    #
    # The control has to be an API that actually checks the token. The first
    # version of this used alibaba.category.get and the control passed
    # *vacuously*: the category tree is public, so an invented token gets
    # gw.ParamMissing exactly like a real one, and the check reported ALLOWED
    # for a token that does not exist. alibaba.product.get answers
    # "Request need user authorized" instead, and does so whether or not the
    # app owns it - the token is checked before the ACL - which is what makes
    # it a valid control on any app, permitted or not.
    verdict, _ = classify(call("com.alibaba.product", "alibaba.product.get", FAKE_TOKEN))
    print("control: the same call with an invented token must fail differently")
    print(f"  fake token -> {verdict}"
          + ("   OK, the real token is what authenticates"
             if verdict == "TOKEN REJECTED" else
             "   WARNING: control did not fail as expected, treat the run as void"))

    print(f"\n{len(allowed)} allowed, {len(blocked)} need permission, "
          f"{len(missing)} not found under any namespace tried, "
          f"{len(other)} unclassified")
    for name in allowed:
        print(f"  allowed:  {name}")
    for name in blocked:
        print(f"  blocked:  {name}")
    for name in missing:
        print(f"  no such:  {name}")
    for name in other:
        print(f"  unclear:  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

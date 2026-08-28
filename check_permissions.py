"""
Which 1688 APIs is this appKey allowed to call?

    KDX_1688_APP_KEY=... KDX_1688_APP_SECRET=... KDX_1688_TOKEN=... \
        python3 check_permissions.py

Run it again the moment the account owner grants a permission - it re-reads the
truth from the gateway instead of anyone remembering what was requested.

The gateway separates the two failures for us, which is the whole point:

    gw.APIUnsupported  the API name does not exist (my mistake)
    gw.APIACLDecline   it exists, this app just has no permission (their step)

A fake token never gets far enough to tell those apart - it stops at 401 - so
the control run at the end is what proves the map above is real.
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
SAMPLE_OFFER = os.environ.get("KDX_TEST_OFFER", "104843239419")

# (namespace, api, sample params, what the pipeline needs it for)
NEEDED = [
    ("com.alibaba.product", "alibaba.category.get", {"categoryID": "0"},
     "category tree - already granted"),
    ("com.alibaba.product", "alibaba.product.get",
     {"webSite": "1688", "offerId": SAMPLE_OFFER},
     "product detail: every image, every price, every SKU - nothing runs without it"),
    ("com.alibaba.fenxiao.crossborder", "product.search.keywordQuery", {"keyword": "连衣裙"},
     "finding products to import in the first place"),
    ("com.alibaba.product", "alibaba.category.attribute.get", {"categoryID": "1031910"},
     "category attributes - where the 220V / 50-60Hz filter reads from"),
    ("com.alibaba.fenxiao.crossborder", "product.search.imageQuery", {"imageAddress": "x"},
     "image search - optional"),
]


def call(namespace: str, api: str, params: dict, token: str) -> tuple[int, str]:
    app_key = os.environ["KDX_1688_APP_KEY"]
    secret = os.environ["KDX_1688_APP_SECRET"]
    params = {key: str(value) for key, value in params.items()}
    params["access_token"] = token
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
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"network error: {exc}"


def classify(body: str) -> str:
    try:
        code = json.loads(body).get("error_code", "")
    except ValueError:
        code = ""
    return {"gw.APIUnsupported": "NO SUCH API",
            "gw.APIACLDecline": "NEEDS PERMISSION",
            "401": "TOKEN REJECTED"}.get(str(code), "ALLOWED")


def main() -> int:
    for name in ("KDX_1688_APP_KEY", "KDX_1688_APP_SECRET", "KDX_1688_TOKEN"):
        if not os.environ.get(name):
            raise SystemExit(f"set {name}")
    token = os.environ["KDX_1688_TOKEN"]

    granted, blocked = [], []
    for namespace, api, params, purpose in NEEDED:
        _, body = call(namespace, api, params, token)
        verdict = classify(body)
        print(f"  {verdict:<16} {namespace}/{api}\n{'':<19}{purpose}")
        (granted if verdict == "ALLOWED" else blocked).append(f"{namespace}/{api}")
        time.sleep(1.2)

    print("\ncontrol: the same call with an invented token must fail differently,")
    print("otherwise the verdicts above mean nothing")
    _, body = call("com.alibaba.product", "alibaba.product.get",
                   {"webSite": "1688", "offerId": SAMPLE_OFFER}, FAKE_TOKEN)
    control = classify(body)
    print(f"  fake token -> {control}"
          + ("  OK, the real token authenticates" if control == "TOKEN REJECTED"
             else "  WARNING: control did not fail as expected"))

    print(f"\n{len(granted)} allowed, {len(blocked)} still to grant")
    for name in blocked:
        print(f"  still blocked: {name}")
    return 0 if not blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())

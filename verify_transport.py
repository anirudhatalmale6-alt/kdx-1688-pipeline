"""
Proof for the 1688 transport layer, against the live gateway.

    KDX_1688_APP_KEY=... KDX_1688_APP_SECRET=... KDX_1688_TOKEN=... \
        python3 verify_transport.py

Two of these checks exist because of mistakes that already cost time:

  - a 4xx from the gateway carries the real complaint in its body, and the
    client used to throw that body away, leaving only "HTTP Error 400"
  - a permission error was being retried three times with backoff, which turns
    an instant answer into a slow one and buries the message

The token checks are always run as a pair. A single reply proves nothing: the
real token is only "working" if an invented one behaves differently.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from aop_client import AopClient, ApiRoute, AopError, Credentials, TokenStore  # noqa: E402

FAKE_TOKEN = "00000000-0000-0000-0000-000000000000"
OFFER = os.environ.get("KDX_TEST_OFFER", "104843239419")

CATEGORY = ApiRoute(namespace="com.alibaba.product", api_name="alibaba.category.get")
PRODUCT = ApiRoute(namespace="com.alibaba.product", api_name="alibaba.product.get")

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label} {detail}")


def client_for(token: str) -> AopClient:
    return AopClient(Credentials(app_key=os.environ["KDX_1688_APP_KEY"],
                                 app_secret=os.environ["KDX_1688_APP_SECRET"],
                                 access_token=token))


def error_from(token: str) -> str:
    try:
        client_for(token).call(PRODUCT, {"webSite": "1688", "offerId": OFFER})
        return ""
    except AopError as exc:
        return str(exc)


def test_offline() -> None:
    print("1. a fixed console token is not an expired one")
    static = TokenStore(client_for("92b6cc37-fixed-console-token"))
    check("a token with no expiry and no refresh is recognised as static", static.is_static())
    check("and is handed back instead of raising",
          static.ensure_fresh() == "92b6cc37-fixed-console-token")

    refreshable = client_for("abc")
    refreshable.credentials.refresh_token = "r"
    refreshable.credentials.expires_at = int(time.time()) + 86400
    store = TokenStore(refreshable)
    check("a real OAuth token is not mistaken for a static one", not store.is_static())
    check("and is still returned while it is fresh", store.ensure_fresh() == "abc")

    empty = TokenStore(client_for(""))
    check("no token at all is not 'static'", not empty.is_static())


def test_live() -> None:
    print("2. the category tree answers with no token at all")
    payload = client_for("").call(CATEGORY, {"categoryID": "0"}, authed=False)
    roots = (payload.get("categoryInfo") or [{}])[0].get("childCategorys") or []
    check("categoryID=0 returns the roots", len(roots) > 10, f"{len(roots)} roots")
    check("and they carry names", bool(roots and roots[0].get("name")))

    print("3. a 4xx body reaches the caller instead of 'HTTP Error 400'")
    real = error_from(os.environ["KDX_1688_TOKEN"])
    check("the real token gets a permission error, not a transport error",
          "acl" in real.lower() or "APIACLDecline" in real, real[:160])
    check("the message is readable, not just a status line",
          "not allowed" in real.lower(), real[:160])

    print("4. the control: an invented token must fail differently")
    fake = error_from(FAKE_TOKEN)
    check("the fake token is rejected at authentication",
          "401" in fake or "authoriz" in fake.lower(), fake[:160])
    check("the two errors are genuinely different", real != fake,
          "both replies identical - the token check proves nothing")

    print("5. a settled answer is not retried")
    start = time.monotonic()
    error_from(FAKE_TOKEN)
    elapsed = time.monotonic() - start
    check("401 raises on the first attempt", elapsed < 2.0,
          f"took {elapsed:.1f}s, which is three attempts with backoff")


def main() -> int:
    test_offline()
    missing = [name for name in ("KDX_1688_APP_KEY", "KDX_1688_APP_SECRET", "KDX_1688_TOKEN")
               if not os.environ.get(name)]
    if missing:
        print(f"\nSKIP live checks: set {', '.join(missing)}")
    else:
        test_live()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

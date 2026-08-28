"""
One-time 1688 authorisation, start to finish.

    python3 authorize.py link
    python3 authorize.py exchange "<the whole address bar after approving>"

Why two steps and not a web server: the account owner has to click "agree" in a
browser while logged in as themselves, and 1688 then sends the code to the
callback address registered in the app console. That address is theirs, not
mine, so the code lands on their site. Pasting the address bar back here is the
shortest honest path - it needs no page to exist on their server, because the
code is in the URL whether the route answers or 404s.

The token is written to KDX_TOKEN_STORE (default /opt/kdx/token.json) and is
never printed in full: the refresh token is a long-lived credential.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from aop_client import AopClient, ApiRoute, Credentials, AopError  # noqa: E402

APP_KEY = os.environ.get("KDX_1688_APP_KEY", "")
APP_SECRET = os.environ.get("KDX_1688_APP_SECRET", "")
REDIRECT_URI = os.environ.get("KDX_1688_REDIRECT_URI", "https://kdx-sa.com/api/1688/callback")
TOKEN_STORE = os.environ.get("KDX_TOKEN_STORE", "/opt/kdx/token.json")

TOKEN_ROUTE = ApiRoute(namespace="system.oauth2", api_name="getToken")


def build_link() -> str:
    query = urllib.parse.urlencode({
        "client_id": APP_KEY,
        "site": "1688",
        "redirect_uri": REDIRECT_URI,
        "state": "kdx",
        "view": "web",
    })
    return f"https://auth.1688.com/oauth/authorize?{query}"


def code_from(pasted: str) -> str:
    """
    Accept either a bare code or the whole pasted address. Taking the whole
    address is deliberate: asking a non-technical person to isolate one query
    parameter is how this step gets done wrong.
    """
    pasted = pasted.strip().strip('"').strip("'")
    if "?" not in pasted and "&" not in pasted:
        return pasted
    query = urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1]
    values = urllib.parse.parse_qs(query)
    if "code" not in values:
        raise SystemExit(f"no ?code= in what you pasted. Got parameters: {sorted(values)}")
    return values["code"][0]


def exchange(code: str) -> dict:
    client = AopClient(Credentials(app_key=APP_KEY, app_secret=APP_SECRET))
    payload = client.call(TOKEN_ROUTE, {
        "grant_type": "authorization_code",
        "need_refresh_token": "true",
        "client_id": APP_KEY,
        "client_secret": APP_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }, authed=False)

    if not payload.get("access_token"):
        raise SystemExit(f"no access_token in the reply: {json.dumps(payload, ensure_ascii=False)[:400]}")

    expires_in = int(payload.get("expires_in") or 0)
    record = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": int(time.time()) + expires_in if expires_in else 0,
        "resource_owner": payload.get("resource_owner", ""),
        "obtained_at": int(time.time()),
    }
    os.makedirs(os.path.dirname(TOKEN_STORE), exist_ok=True)
    with open(TOKEN_STORE, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=1)
    os.chmod(TOKEN_STORE, 0o600)
    return record


def main() -> int:
    if not APP_KEY or not APP_SECRET:
        raise SystemExit("set KDX_1688_APP_KEY and KDX_1688_APP_SECRET first")

    action = sys.argv[1] if len(sys.argv) > 1 else "link"
    if action == "link":
        print(build_link())
        return 0
    if action == "exchange":
        if len(sys.argv) < 3:
            raise SystemExit('usage: python3 authorize.py exchange "<pasted address>"')
        try:
            record = exchange(code_from(sys.argv[2]))
        except AopError as exc:
            raise SystemExit(f"1688 refused the exchange: {exc}") from exc
        masked = record["access_token"][:6] + "..." + record["access_token"][-4:]
        print(f"access_token {masked} saved to {TOKEN_STORE}")
        print("expires:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record["expires_at"]))
              if record["expires_at"] else "unknown")
        print("refresh_token:", "present" if record["refresh_token"] else "MISSING - re-run with need_refresh_token")
        return 0
    raise SystemExit(f"unknown action {action!r}; use link or exchange")


if __name__ == "__main__":
    raise SystemExit(main())

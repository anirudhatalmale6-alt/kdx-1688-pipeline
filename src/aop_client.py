"""
1688 Open Platform (gw.open.1688.com) request signing and transport.

Signature scheme used by the platform ("AOP"):

    signStr   = urlPath + "".join(k + v for k, v in sorted(params.items()))
    signature = HMAC-SHA1(signStr, appSecret)  ->  uppercase hex
    params["_aop_signature"] = signature

where urlPath is everything after "/openapi/", i.e.

    param2/1/<namespace>/<apiName>/<appKey>

Nothing here is guessed at call time: namespace / apiName / protocol / version
all come from config, because the exact API names differ between permission
packages (general product package vs. cross-border sourcing package).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

GATEWAY = "https://gw.open.1688.com/openapi"


@dataclass
class Credentials:
    app_key: str
    app_secret: str
    access_token: str = ""
    refresh_token: str = ""
    # epoch seconds; 0 means "unknown, refresh before first use"
    expires_at: int = 0


@dataclass
class ApiRoute:
    """One callable API endpoint, fully described by config."""

    namespace: str
    api_name: str
    protocol: str = "param2"
    version: str = "1"

    def url_path(self, app_key: str) -> str:
        return f"{self.protocol}/{self.version}/{self.namespace}/{self.api_name}/{app_key}"


def sign(url_path: str, params: dict, app_secret: str) -> str:
    """Return the uppercase hex HMAC-SHA1 signature for one request."""
    concatenated = "".join(f"{k}{params[k]}" for k in sorted(params))
    payload = (url_path + concatenated).encode("utf-8")
    digest = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha1).digest()
    return digest.hex().upper()


class RateLimiter:
    """Minimum spacing between calls. 1688 throttles per second, per API."""

    def __init__(self, calls_per_second: float = 4.0):
        self._min_gap = 1.0 / calls_per_second if calls_per_second > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        gap = self._min_gap - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


class AopError(RuntimeError):
    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload or {}


class AopClient:
    # Statuses where the gateway has already made up its mind: a bad signature,
    # a rejected token, a missing permission. Retrying only delays the message.
    NO_RETRY_STATUS = frozenset({400, 401, 403, 404})

    def __init__(
        self,
        credentials: Credentials,
        calls_per_second: float = 4.0,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.credentials = credentials
        self.limiter = RateLimiter(calls_per_second)
        self.timeout = timeout
        self.max_retries = max_retries

    def call(self, route: ApiRoute, params: dict | None = None, authed: bool = True) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        params = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v))
                  for k, v in params.items()}

        if authed:
            params["access_token"] = self.credentials.access_token

        url_path = route.url_path(self.credentials.app_key)
        params["_aop_signature"] = sign(url_path, params, self.credentials.app_secret)

        url = f"{GATEWAY}/{url_path}"
        body = urllib.parse.urlencode(params).encode("utf-8")

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self.limiter.wait()
            try:
                request = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # The body of a 4xx is the gateway's actual complaint, and it is
                # the only place the useful part appears. Reading it cost me a
                # day once: a bare "HTTP Error 400" turned out to be
                # gw.APIACLDecline, "AppKey is not allowed(acl)", which is a
                # completely different problem from a malformed request.
                detail = exc.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(detail)
                except ValueError:
                    payload = {"error_code": str(exc.code), "error_message": detail[:500]}
                code = str(payload.get("error_code") or payload.get("errorCode") or exc.code)
                message = payload.get("error_message") or payload.get("errorMessage") or detail[:500]
                if exc.code in self.NO_RETRY_STATUS:
                    # Wrong key, missing permission, bad parameters: repeating
                    # the same request three times cannot change the answer.
                    raise AopError(f"HTTP {exc.code} {code}: {message}", payload) from None
                last_error = AopError(f"HTTP {exc.code} {code}: {message}", payload)
                time.sleep(2 ** attempt)
                continue
            except Exception as exc:  # network / decode
                last_error = exc
                time.sleep(2 ** attempt)
                continue

            # The gateway reports business errors inside a 200 response.
            if payload.get("errorCode") or payload.get("error_code"):
                code = str(payload.get("errorCode") or payload.get("error_code"))
                message = payload.get("errorMessage") or payload.get("error_message") or ""
                # Throttling is worth retrying; a permission error is not.
                if "FLOW" in code.upper() or "LIMIT" in code.upper():
                    last_error = AopError(f"{code}: {message}", payload)
                    time.sleep(2 ** attempt)
                    continue
                raise AopError(f"{code}: {message}", payload)

            return payload

        raise AopError(f"request failed after {self.max_retries} attempts: {last_error}")


class TokenStore:
    """
    Access tokens expire. Refresh ahead of expiry rather than on failure, so a
    long catalogue run never dies halfway through.

    Except that KDX's app is a self-use ("自用型") app, and those are issued a
    fixed token from the 1688 console with no expiry and no refresh_token. The
    account owner renews it by hand, if ever. Verified against the live gateway:
    that token authenticates, while an invented one is refused with 401. So a
    token that has no expiry and no refresh_token is treated as static and
    handed back untouched - the alternative was this class raising
    "no refresh_token available" on a token that works perfectly.
    """

    REFRESH_ROUTE = ApiRoute(namespace="system.oauth2", api_name="getToken")

    def __init__(self, client: AopClient, refresh_margin_seconds: int = 3600):
        self.client = client
        self.margin = refresh_margin_seconds

    def is_static(self) -> bool:
        credentials = self.client.credentials
        return bool(credentials.access_token) and not credentials.refresh_token \
            and not credentials.expires_at

    def ensure_fresh(self) -> str:
        credentials = self.client.credentials
        if self.is_static():
            return credentials.access_token
        if credentials.access_token and time.time() < credentials.expires_at - self.margin:
            return credentials.access_token
        if not credentials.refresh_token:
            raise AopError("no refresh_token available; complete the OAuth authorisation once")
        return self.refresh()

    def refresh(self) -> str:
        credentials = self.client.credentials
        payload = self.client.call(
            self.REFRESH_ROUTE,
            {
                "grant_type": "refresh_token",
                "client_id": credentials.app_key,
                "client_secret": credentials.app_secret,
                "refresh_token": credentials.refresh_token,
                "need_refresh_token": "true",
            },
            authed=False,
        )
        credentials.access_token = payload["access_token"]
        credentials.refresh_token = payload.get("refresh_token", credentials.refresh_token)
        expires_in = int(payload.get("expires_in", 0))
        credentials.expires_at = int(time.time()) + expires_in if expires_in else 0
        return credentials.access_token


ENV_APP_KEY = "KDX_1688_APP_KEY"
ENV_APP_SECRET = "KDX_1688_APP_SECRET"
ENV_TOKEN = "KDX_1688_TOKEN"
ENV_REFRESH = "KDX_1688_REFRESH_TOKEN"


def build_from_env(**kwargs) -> "AopClient":
    """
    The one place credentials are read.

    Every script here used to read them itself, and they had drifted: three
    spellings of the token and, in src/categories.py, an entirely different pair
    of names left over from the first day. On a server that reads a single
    environment file, a name that nobody sets is not a warning - it is a
    KeyError at midnight, in a cron job whose output nobody is watching.

    The message names what is missing, because the commonest failure here is a
    token that was never set rather than one that is wrong.
    """
    missing = [name for name in (ENV_APP_KEY, ENV_APP_SECRET, ENV_TOKEN)
               if not os.environ.get(name)]
    if missing:
        raise AopError(
            "1688 credentials are not in the environment: "
            + ", ".join(missing)
            + ". Set them in the service's environment file; they must not be "
              "written into the repository.")
    return AopClient(
        Credentials(app_key=os.environ[ENV_APP_KEY],
                    app_secret=os.environ[ENV_APP_SECRET],
                    access_token=os.environ[ENV_TOKEN],
                    refresh_token=os.environ.get(ENV_REFRESH, "")),
        **kwargs)


# --- two apps at once -------------------------------------------------------
#
# The client had a second 1688 app approved for the distribution (分销) package
# and said he would replace the first app's keys with it. He must not: the
# permission list of the new app does not contain
# com.alibaba.linkplus/alibaba.cross.similar.offer.search, the image search the
# whole price comparison is built on, and that permission lives on the OLD app.
# Neither app can run this pipeline alone, so the pipeline holds both and picks
# per call.

ENV_B_APP_KEY = "KDX_1688_B_APP_KEY"
ENV_B_APP_SECRET = "KDX_1688_B_APP_SECRET"
ENV_B_TOKEN = "KDX_1688_B_TOKEN"
ENV_B_REFRESH = "KDX_1688_B_REFRESH_TOKEN"

# Which app owns what, as measured rather than assumed. Keys are either
# "namespace/apiName" (checked first) or a bare namespace (checked second);
# anything unlisted goes to the default app. The per-API entries exist because
# com.alibaba.product is split down the middle: alibaba.product.get belongs to
# the old app's package and product.skuinfo.get arrived with the new one.
SECOND_APP_ROUTES = {
    "com.alibaba.product/product.skuinfo.get",
    "com.alibaba.fenxiao",
    "com.alibaba.fenxiao.crossborder",
    "com.alibaba.logistics",
    "com.alibaba.trade",
}

# Exceptions to the namespace rows above, and they are not guesses: on
# 2026-09-01, once the second app's permissions opened, a probe of all
# twenty-two APIs measured these two as still gw.APIACLDecline on the second app
# while the first app answers them. A namespace is not a permission boundary -
# com.alibaba.fenxiao.crossborder is split, pool.product.pull belongs to the new
# app and product.search.queryProductDetail does not. Without this set the
# fallback would still recover the call, but only after paying for a refused
# round trip on every single request.
FIRST_APP_ROUTES = {
    "com.alibaba.fenxiao.crossborder/product.search.queryProductDetail",
}

ACL_MARKERS = ("APIACLDecline", "not allowed(acl)", "APIACLDeny")


def _is_acl_decline(error: "AopError") -> bool:
    text = f"{error} {error.payload.get('error_code', '')} {error.payload.get('error_message', '')}"
    return any(marker in text for marker in ACL_MARKERS)


class ClientPool:
    """
    One .call() over several apps, choosing the credentials per route.

    Drop-in for AopClient: the callers only ever use .call(), and a pool built
    with a single app routes every call to it, so nothing changes until a second
    set of credentials is actually present in the environment.

    The routing table above is a starting guess, and a wrong guess is cheap to
    recover from: an ACL decline is the gateway refusing *before* the API runs,
    so nothing happened and the same call can be repeated against the other app.
    When that succeeds the pool remembers it, so one call pays for the mistake
    and the rest go straight to the right app. This is deliberately not a blind
    retry - it fires only on an ACL decline, never on a business error, and
    never on a timeout, because those may mean the call did happen.
    """

    def __init__(self, clients: dict, default: str, routes=None):
        if default not in clients:
            raise AopError(f"default app {default!r} is not one of {sorted(clients)}")
        self.clients = clients
        self.default = default
        self.routes = set(SECOND_APP_ROUTES if routes is None else routes)
        self.second = next((name for name in clients if name != default), None)
        self.learned: dict = {}
        self.log: list = []

    @property
    def credentials(self) -> Credentials:
        return self.clients[self.default].credentials

    def label_for(self, route: ApiRoute) -> str:
        key = f"{route.namespace}/{route.api_name}"
        if key in self.learned:
            return self.learned[key]
        if key in FIRST_APP_ROUTES:
            return self.default
        if self.second and (key in self.routes or route.namespace in self.routes):
            return self.second
        return self.default

    def call(self, route: ApiRoute, params: dict | None = None, authed: bool = True) -> dict:
        key = f"{route.namespace}/{route.api_name}"
        label = self.label_for(route)
        try:
            payload = self.clients[label].call(route, params, authed=authed)
        except AopError as error:
            other = next((name for name in self.clients if name != label), None)
            if not other or not _is_acl_decline(error):
                raise
            payload = self.clients[other].call(route, params, authed=authed)
            self.learned[key] = other
            self.log.append(f"{key}: {label} declined (acl), {other} answered")
            return payload
        self.learned[key] = label
        return payload


def build_pool_from_env(**kwargs) -> "ClientPool":
    """
    The primary app, plus the second one if its keys are in the environment.

    With no KDX_1688_B_* set this returns a pool of one, which behaves exactly
    like build_from_env() - so this can be wired in before the second app is
    approved without changing what runs tonight.
    """
    clients = {"primary": build_from_env(**kwargs)}
    if os.environ.get(ENV_B_APP_KEY) and os.environ.get(ENV_B_APP_SECRET):
        clients["fenxiao"] = AopClient(
            Credentials(app_key=os.environ[ENV_B_APP_KEY],
                        app_secret=os.environ[ENV_B_APP_SECRET],
                        access_token=os.environ.get(ENV_B_TOKEN, ""),
                        refresh_token=os.environ.get(ENV_B_REFRESH, "")),
            **kwargs)
    return ClientPool(clients, default="primary")

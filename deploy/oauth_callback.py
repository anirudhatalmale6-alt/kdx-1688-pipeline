"""
One-shot OAuth catcher for the 1688 authorization code.

1688 sends the account owner back to redirect_uri with ?code=... . That code is
single-use and short-lived, so it has to be caught by something that is already
listening rather than copied out of a browser bar by hand.

Listens on :80, stores the code, and shows the owner an Arabic confirmation page
so they know the step worked.
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

CODE_PATH = "/opt/kdx/oauth_code.json"
CALLBACK_PATH = "/1688/callback"

PAGE = """<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#0f1115;color:#e8eaed;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px}}
.card{{background:#171a21;border:1px solid #262b36;border-radius:14px;padding:32px;max-width:520px;text-align:center}}
h1{{font-size:22px;margin:0 0 12px}} p{{line-height:1.9;color:#aab1bd;margin:0}}
.ok{{color:#39d353;font-size:44px;line-height:1}} .bad{{color:#f0883e;font-size:44px;line-height:1}}
</style>
<div class="card"><div class="{icon}">{glyph}</div><h1>{title}</h1><p>{body}</p></div>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path.rstrip("/") != CALLBACK_PATH.rstrip("/"):
            self._send(200, PAGE.format(icon="ok", glyph="&#9679;", title="الخادم يعمل",
                                        body="هذه الصفحة تنتظر تفويض 1688."))
            return

        code = (query.get("code") or [""])[0]
        if not code:
            reason = (query.get("error_description") or query.get("error") or ["لم يصل رمز التفويض"])[0]
            self._send(400, PAGE.format(icon="bad", glyph="&#9888;", title="لم يكتمل التفويض",
                                        body=f"السبب: {reason}<br>الرجاء إعادة المحاولة."))
            return

        os.makedirs(os.path.dirname(CODE_PATH), exist_ok=True)
        with open(CODE_PATH, "w", encoding="utf-8") as handle:
            json.dump({"code": code,
                       "state": (query.get("state") or [""])[0],
                       "received_at": int(time.time())}, handle)
        os.chmod(CODE_PATH, 0o600)

        self._send(200, PAGE.format(icon="ok", glyph="&#10003;", title="تم التفويض بنجاح",
                                    body="وصل رمز التفويض. يمكنك إغلاق هذه الصفحة الآن."))

    def log_message(self, fmt, *args):  # keep the journal readable
        return


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 80), Handler).serve_forever()

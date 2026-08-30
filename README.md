# KDX / 1688 pipeline — working demo of the decision rules

هذا ليس النظام النهائي. هذا إثبات عملي مبني على المواصفات التي أرسلتها، ويشتغل الآن بدون أي مفاتيح.

This is a runnable demonstration of the rules you specified, built before any
credentials were exchanged so you can judge the work first.

## Run it

```bash
python3 demo_rules.py
```

No dependencies, no network, no credentials. It builds ten products that each
trigger a different rule and prints the audit log the real pipeline would write.

## The nightly run

```bash
# what the server does at 00:00 Riyadh
python3 daily_run.py

# the same thing without publishing anything
python3 daily_run.py --dry-run --quota 20
```

The channel this appKey holds searches by photograph and has no lookup, so the
night starts from `KDX_SEEDS` — a file of image URLs — and walks outwards.
Measured against the live gateway on 29 August: one photograph is worth about
75 offers over four pages, and expanding what comes back reaches new offers, so
**300 products took 56 gateway calls and 73 seconds from a single seed**.

Two things make that repeatable rather than a one-off:

* every offer already handed over is recorded on disk, so tomorrow does not
  republish today;
* the offers a night finds beyond its quota are kept, not discarded — they were
  already paid for with a gateway call, and the next night takes them before it
  searches anything.

Check a seed list before trusting a night to it:

```bash
python3 check_seeds.py seeds.txt
```

One gateway call per seed. It says which are alive, which categories each one
opens, and — because two seeds that return the same offers are one seed — how
many offers any two of them share.

A seed does **not** have to live on Alibaba's own CDN. Measured on 30 August,
one gateway call each: `cbu01.alicdn.com`, `images.unsplash.com` and
`raw.githubusercontent.com` all returned 20 offers, a PNG among them. The only
URL refused was one that was already broken at source — it answered HTTP 400
here too, and the gateway named it in the refusal. So the requirement is not a
particular host: the URL must be public, must point straight at the image file,
and must actually return an image.

`deploy/` has the systemd service and timer, and `deploy/kdx.env.example` lists
every setting. Credentials belong in `/etc/kdx/kdx.env` and nowhere else.

## What is implemented

Every rule below comes from your messages and is exercised by the demo:

| Rule | Where |
|---|---|
| Undercut a matched competitor: ≤100 SAR −3%, 100–500 −2%, >500 −1% | `src/rules.py` `UNDERCUT_BANDS` |
| Margin when not found: 30 / 27 / 24 / 20 / 17 / 15 / 13 / 10 % by price band | `src/rules.py` `MARKUP_BANDS` |
| Match only counts at 95% or above | `src/rules.py` `MATCH_THRESHOLD` |
| Each variant priced independently; a hit on one colour is never reused for another | `src/rules.py` `best_match` |
| ≤2 kg → shipping "yes" (fast); >2 kg → shipping "no" (free); never a shipping fee | `src/rules.py` `shipping_flag` |
| Over 2 kg and not found on any platform → not published | `src/rules.py` `_evaluate_variant` |
| Never publish if undercutting would drop below our cost | `src/rules.py` `_evaluate_variant` |
| Electrical goods only at 220V and 50/60Hz | `src/rules.py` `has_accepted_mains_spec` |
| Excluded categories: sexual, religious, weapons, drugs, tobacco/vape, counterfeits | `src/rules.py` `BANNED_TERMS` |
| Out of stock on 1688 → not published | `src/rules.py` `_evaluate_variant` |
| Existing product → update only, SKU / URL / ratings / sales untouched | `src/kdx_client.py` `update` |
| Audit record per variant: decision, reason, platform, score, cost, final price | `src/rules.py` `AuditRecord` |

## 1688 transport

`src/aop_client.py` carries the platform's signing scheme:

```
signStr   = urlPath + "".join(key + value for key in sorted(params))
signature = HMAC-SHA1(signStr, appSecret) -> uppercase hex
```

with `urlPath = param2/1/<namespace>/<apiName>/<appKey>`, plus per-second rate
limiting, retry on throttling errors only, and token refresh ahead of expiry so
a long catalogue run does not die halfway through.

The namespace and API name are configuration, not constants, because they
differ between permission packages.

## KDX import contract — measured, not assumed

`POST https://kdx-sa.com/api/v1/products/import`, header `X-API-Token`,
body `{"products":[ ... ]}`. The field list was established by sending a wrong
type for every candidate field and reading which ones the validator rejected:

| Field | Required | Type |
|---|---|---|
| `source_offer_id` | yes | string — the 1688 offer id, and the update key |
| `name_en` | yes | string |
| `name_ar` | no | string |
| `description_ar` | no | string |
| `description_en` | no | string |
| `price` | no | number |
| `images` | no | array |
| `sizes` | no | array |
| `needs_shipment` | no | boolean — `true` = fast delivery, `false` = free |
| `category.main_category` | no | array |
| `category.sub_category` | no | array |

Anything else — `source`, `product_url`, `name`, `name_original`,
`price_currency`, `weight`, `sku`, `stock` — passes the HTTP layer untouched but
is not validated, so KDX does not store it. They are still sent because the
client specified that shape; nothing depends on getting them back.

`src/mapping.py` owns the product JSON. `needs_shipment` is derived from weight
(≤ 2 kg → `true`), so the weight itself never needs to reach KDX.

```bash
KDX_API_TOKEN=... python3 verify_kdx.py     # 22 checks, run it twice
```

Each success is paired with a control: a wrong token must be refused, a product
without `name_en` must be caught before it leaves, KDX itself must reject a
malformed `needs_shipment`, and the update payload is asserted to carry no
`sku` / `product_url` / `rating` / `sales` / `stock`.

**Images are hot-linked, not mirrored.** KDX stores the URL and the shop renders
it directly, so a URL that 404s shows the customer a broken-image box — that is
what a placeholder URL in an early test produced.

## Still open

1. **How the system checks Temu, SHEIN, AliExpress, Amazon and Noon.** The five
   platforms offer no image search to third parties; Google Lens through SerpApi
   does return merchant and price and is the route being used. The engine
   consumes a list of `CompetitorHit` objects and does not care where they came
   from, so this stays one replaceable seam.
2. **User authorization for 1688.** `redirect_uri` is mandatory on
   `auth.1688.com/oauth/authorize` and every guessed value is refused. Proven
   with a control pair in a real browser (curl only receives Alibaba's JS
   challenge page): omitting it returns `缺少必要参数` (missing required
   parameter), a wrong one returns `非法请求` (invalid request). Only the value
   registered in the client's own 1688 console will work.
3. `GET /api/alibaba/categories` on kdx-sa.com answers HTTP 500 with
   `gw.SignatureInvalid` from 1688. The same app key and secret sign correctly
   from `src/aop_client.py`, so the fault is in the Laravel signing or in the
   secret stored on that server.
4. The 1688 server only accepts inbound SSH: ports 80 and 8080 are blocked
   upstream by the host, so nothing web-facing can be served from it.

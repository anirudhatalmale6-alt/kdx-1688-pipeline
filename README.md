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

### Opening the whole market from words, with no photographs at all

```bash
python3 seed_from_words.py --departments        # all 49
python3 seed_from_words.py --departments --limit 5
```

The keyword API exists but is refused to this appKey — measured 30 August,
`product.search.keywordQuery` → `gw.APIACLDecline`, "AppKey is not allowed(acl)".
That is a permission, not a missing feature, and it is the one under review.

Until it lands, a word can still become a door. Google Images turns a word into
pictures, the gateway accepts any public image URL, and the 1688 category tree —
readable, since `alibaba.category.get` *is* granted — already names all 49
departments in Chinese. So the run needs no photographs from anyone:

    word → Google Images → image URL → similar-offer search → a department

Measured on 30 August: eight departments opened, twenty offers each, first
attempt on five of them. A word costs exactly one search, once — the answer is
cached, so re-running is free for every word already opened, and those searches
are charged to the same monthly meter as price comparison so nothing is spent
off the books.

A picture only becomes a seed after the gateway has actually returned offers for
it. A picture that opens nothing is never written to the seed file.

**Categories resolve on demand.** The built tree stops one level below the
departments. Across a general catalogue most leaf ids fall outside it, and an
unresolved category is not just a blank department in the shop — `state_of`
answers "unknown", and unknown cannot reject, so the category ban filter
quietly stops working and only the Chinese title stands between a prohibited
product and the shop. `src/category_live.py` climbs `parentIDs` from any leaf,
classifies the whole chain, and caches each answer to disk as it learns it. A
blocked ancestor blocks the leaf. On a live run this took fourteen products from
no department at all to fourteen correct ones — `办公椅` → `كراسي مكتب`.

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

## Two product channels, and why one photograph is not a fault

```
daily_run.py --channel image                    # all of 1688, one photograph each
daily_run.py --channel selected                 # the pool, searched by tonight's words
daily_run.py --channel selected --keywords 连衣裙,台灯   # or by words you choose
```

**image** is `com.alibaba.linkplus / alibaba.cross.similar.offer.search`. It
searches the whole market by photograph and returns eleven fields per row, of
which exactly one is an image. That is the entire reason every product
published before 1 September carries a single photograph: a search channel is
not a detail channel. Nothing about the shop or the permissions was ever wrong.

**selected** is `jxhy.product.getPageList` walking 1,950 curated offers, then
`alibaba.pifatuan.product.detail.list` fetching up to fifty of them per call.
Measured live on 1 September against 30 offers spread across the whole walk:

| | image search | selected pool |
|---|---|---|
| offers reachable | all of 1688 | ~2,000 per keyword |
| main photographs | 1 | 4–5 (30 of 30 had more than one) |
| photographs in the description | — | 5–32 |
| photograph per colour | — | 21 of 30 |
| per-SKU prices | — | 12 of 30 |
| declared weight | never | 8 of 30 |
| description | — | yes |

They are additive. The image search is the only way to reach an arbitrary offer;
the pool is the only way to get a whole product. `src/selected.py` documents the
three traps that each ship a broken catalogue quietly — relative photograph
paths, an absent weight that `_weight_of` turns into 2.5 kg (above the client's
2 kg line, so the product is classed heavy and, unmatched, never published), and
a batch that refuses all fifty offers if one is outside the pool.

### The pool is not a shelf of 1,950

Measured 2 September. The plain walk stops at 2,000 because that is the window
the listing serves, not the size of the catalogue:

* walked a day apart, **1,053 of 2,000** offers were ones the first walk never
  showed. The window moves.
* the listing **reads a keyword**. 连衣裙 → 50 rows, 50 of the titles contain
  the word; 运动鞋 → trainers; and the control that makes it a fact rather than
  a coincidence, the nonsense string `qqzzxxyy` → **0 rows**. Each keyword then
  pages to exactly 2,000 offers (40 pages of 50, page 41 empty), and 10 offers
  sampled from each of 9 keywords were answered by the detail API every time.

So the words decide the catalogue. They come from the category tree, which is
already built and already vetted — only leaves marked `allowed` — and the
starting point rotates with the date so two nights do not walk the same offers.

Not to be confused with `product.keywords.search`, which despite its name is
**not** a keyword search: asked for 连衣裙, 运动鞋 and a nonsense string it
returns the identical 978 rows. It is a fixed list, and nothing here uses it.

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

### How long his import takes, and why one product is one request

Measured 2 September, after a product with 146 colour options was lost to
"The read operation timed out":

| photographs in the request | seconds |
|---|---|
| 10 | 11.5 |
| 34 | 32.2 |
| 34, the identical payload again | 34.3 |

Three things follow, and `verify_push_sizing.py` asserts all of them:

1. **The cost is photographs, not bytes.** The first attempt at a fix cut the
   gallery to five and still timed out, because 146 variant photographs
   travelled with it untouched. `kdx_client.photo_count` counts variants too.
2. **Chunks cannot accumulate.** A failure returned his own SQL:
   `delete from product_images where id = 59536`. The import deletes the
   photograph set before writing the new one, so a second chunk would erase the
   first. One product goes in one request; a product over the whole budget
   travels alone. Batches of *several* products are split on a photograph
   budget, which is the part of "smaller batches" that can honestly be done
   from this side.
3. **A timeout is never retried.** His server re-downloads everything on every
   call, so a retry starts the same queue again while the first is still
   running — that is how one slow product became 142 seconds of waiting. The
   wait is instead bought at the measured rate: `45s + 1.5s per photograph`,
   capped at 600s.

**Open, on his side: a second push duplicates the options.** Offer
717716012309 was created with 146 Chinese option labels and then updated with
the same 146 in Arabic. The page afterwards showed **291** options — 146
Chinese and 145 Arabic together. Photographs are replaced, options are
appended, and the reply is `updated_count: 1, failed_count: 0` either way, so
nothing in the response says it happened. Until his import de-duplicates on
update, a product should be pushed once; `daily_run.py` prints every offer id
his shop answered as an update and records them in the run report.

`src/mapping.py` owns the product JSON. `needs_shipment` is derived from weight
(≤ 2 kg → `true`), so the weight itself never needs to reach KDX.

```bash
KDX_API_TOKEN=... python3 verify_kdx.py     # 24 checks, run it twice
```

Each success is paired with a control: a wrong token must be refused, a product
without `name_en` must be caught before it leaves, KDX itself must reject a
malformed `needs_shipment`, and the update payload is asserted to carry no
`sku` / `product_url` / `rating` / `sales` / `stock`.

### Images — measured on 2026-08-30, and the earlier note here was wrong

KDX does **not** hot-link. Its importer fetches every URL in `images`,
re-encodes it, and serves its own copy from
`https://kdx-sa.com/uploads/product_1688_images/<uuid>.webp`. Verified by
pushing five probes and reading their public pages back.

Three consequences, in order of how much they cost:

1. **A URL that does not answer at import time loses the product its picture
   for good.** The endpoint inserts and never updates, so there is no second
   chance to supply it. `src/photos.py` therefore fetches every photograph
   before the push, drops the dead ones from the gallery *and from each
   variant*, and holds a product that has none left rather than publishing an
   empty frame. `KDX_CHECK_IMAGES=0` turns the guard off.
2. **The check must not send a `Referer`.** alicdn answers `200` to a request
   with none and `403` to one carrying `Referer: https://kdx-sa.com/` — its
   hot-link protection. His server sends none, so the check sends none. This is
   also why mirroring, rather than hot-linking, is the only arrangement that
   could ever have worked: his shop's pages declare
   `referrer: strict-origin-when-cross-origin`, so a browser asking alicdn
   directly would be refused.
3. **A non-alicdn host is not necessarily accepted.** A Wikimedia URL was taken
   by the validator and came out as `img/no-image.png` on the page, while five
   alicdn URLs came out as stored webp files. Whatever his importer's rule is,
   it is not "any public image".

The answer from the endpoint is now read rather than discarded: `success: true`
used to accompany `skipped_count: 1` for an offer it already held, so a push
that stored nothing was counted as a publication.

### One photograph per product, and why

Every product published so far reached the shop with exactly one picture, no
colours and no sizes. That is not the shop and it is not the mapping: the only
channel this appKey holds is a **search**, and its rows carry eleven fields —
`offerId, subject, quantityBegin, unit, oldPrice, imageUrl, province, city,
supplyAmount, categoryId, detailUrl`. `imageUrl` is singular. There is no
gallery, no SKU table, no description and no weight in the response at all.

Control, so this is not blamed on the wrong component: a probe product pushed
with two image URLs came back from the public product page with **both** stored
as mirrored webp files. His shop handles a gallery correctly.

Sixteen candidate detail-API names were put to the gateway on 2026-08-30, which
separates "no such API" from "exists, no permission" for us:

| API | verdict |
|---|---|
| `com.alibaba.product / alibaba.category.get` | allowed (positive control) |
| `com.alibaba.linkplus / alibaba.cross.similar.offer.search` | allowed — the channel in use |
| `com.alibaba.product / alibaba.product.get` | exists, **ACL declined** |
| `com.alibaba.fenxiao.crossborder / product.search.queryProductDetail` | exists, **ACL declined** |
| twelve other guessed names | `gw.APIUnsupported` |

So the gallery is one permission away, on the client's own 1688 console, and
`docs/product-detail-permission-ar.md` is the request written out for him.

**2026-08-31 — he asked for both and was declined**, and screenshotted the
gateway saying so. Three further roads were measured before accepting that:

* **thirty-five more API names** across `linkplus`, `product`,
  `fenxiao.crossborder`, `fenxiao`, `trade`, `media`, `offer` and `p4p` — 51
  probed in total, and **exactly two are callable**: the similar-offer search
  and `alibaba.category.get`. Even `alibaba.category.attribute.get` and the
  keyword/image searches in the cross-border package are ACL-declined.
* **thirteen parameter variants of the search we do hold** — `SA`/`ar`,
  `CN`/`zh`, no locale, `needSku`, `needDetail`, `needImages`, `needSkuInfo`,
  `returnFields=all`, `fields=all`, `outMemberId`, `scene=detail`,
  `pageSize=100`. All thirteen returned **the identical eleven fields**.
* **sizes written into the Chinese title** — 7 of 151 titles mention size at
  all, and most of those are the marketing word 大码 ("large size"), not a size
  list. Not a source.

There is no back door. What that leaves is an ordering argument rather than a
technical one, and it is in `docs/no-permission-plan-ar.md`: 1688 reviews the
*site* before granting product-data permissions, so a shop under construction is
exactly what gets declined. Publishing with one photograph is what makes the
shop reviewable — and because his import route upserts, every product published
today fills in its gallery, sizes and description automatically on the day the
permission lands. Nothing published now has to be deleted or re-added.

### Chinese writing printed inside the photograph

`src/imagetext.py` scores a photograph by the percentage of its area covered by
confidently-read Chinese characters and orders a gallery cleanest-first.
Measured on twelve real photographs from the 30 August run: ten clean product
shots scored 0.00–0.83 %, and the poster the client complained about scored
**6.11 %**, reading 立体装饰 / 萌趣刺绣 / 好棉好柔软. Confidence filtering is what
makes that separation — a bare character count reports Chinese on clean shots,
because OCR invents characters out of folds and shadows.

`KDX_MAX_CJK_TEXT_PCT` drops photographs above a threshold; it defaults to 0,
which orders but never drops. Two rules that do not bend: with no tesseract
installed the score is `None` and `None` never filters anything, and the last
photograph is never dropped — an ugly picture beats an empty frame.

**The same number also holds the product** (2026-08-31). Ranking is useless when
every offer has one photograph, which is every offer this channel returns, so
the only decision left is whether a poster deserves a listing at all:
`imagetext.poster_only` reports the best score when even the best photograph is
over the threshold, and `run_product` then holds the product with the
measurement in the reason instead of pushing it. Off unless a threshold is set,
never triggered by an unmeasured photograph, and covered end-to-end in
`verify_pipeline.py` — through `run_product`, not by calling the scorer, so a
pipeline that stopped consulting it would fail the check.

Measured across the whole 30 August catalogue, first photograph of all 151
products: 42 (27.8 %) score above 1 %, 30 (19.9 %) above 2 %, 21 (13.9 %) above
3 %, 16 (10.6 %) above 5 %; the worst is 18.17 %. **5 % is the recommendation** —
it catches both t-shirt posters the client complained about (6.11 % and 5.90 %)
and costs a tenth of the catalogue, while keeping the office-chair shots at
about 5.6 % that carry a small corner caption but are real product photographs.

## Refreshing the price of a product already published

```bash
python3 refresh_prices.py --dry-run --limit 10
python3 refresh_prices.py
```

This became possible on 2026-08-30, when the client's developer made
`/api/v1/products/import` upsert. Control pair against the live endpoint: a
fresh `source_offer_id` answered `imported_count: 1`; the same id at a different
price answered `updated_count: 1`, and the public product page then showed the
new price, the new name and both photographs.

The other half is finding the offer again, which this channel has no lookup for.
An offer comes back from a search of its own photograph — measured on the first
six products of the 30 August run, six of six within two pages and five of six
on the first. `src/relookup.py` does that and refuses a near miss: the rest of
the page is other sellers' listings of a similar thing, and taking one would put
a stranger's price on the client's product. An offer that cannot be found again
keeps the price it has and is counted, because a refresh that silently freezes
every price looks exactly like one that works.

## Still open

1. **How the system checks Temu, SHEIN, AliExpress, Amazon and Noon.** The five
   platforms offer no image search to third parties; Google Lens through SerpApi
   does return merchant and price and is the route being used. The engine
   consumes a list of `CompetitorHit` objects and does not care where they came
   from, so this stays one replaceable seam.
2. **The keyword permission.** `product.search.keywordQuery` exists and is
   ACL-declined. When it is granted, pulling by word becomes direct and the
   word→picture step above is no longer needed. Nothing else in the pipeline
   changes: the source is one seam, chosen by `KDX_SOURCE`.
3. **User authorization for 1688.** `redirect_uri` is mandatory on
   `auth.1688.com/oauth/authorize` and every guessed value is refused. Proven
   with a control pair in a real browser (curl only receives Alibaba's JS
   challenge page): omitting it returns `缺少必要参数` (missing required
   parameter), a wrong one returns `非法请求` (invalid request). Only the value
   registered in the client's own 1688 console will work.
4. **Product detail on 1688.** The channel in use is a search and carries one
   photograph, no SKU table, no description and no weight. Both APIs that would
   carry them - `alibaba.product.get` and
   `product.search.queryProductDetail` - exist and are ACL-declined for this
   appKey. Until one is granted, every product publishes with a single picture
   and no colours or sizes. See `docs/product-detail-permission-ar.md`.
   *(The import endpoint's missing update path, which was item 4 here, was
   fixed by the client's developer on 30 August and is now proved by
   verify_kdx.py against the live endpoint.)*
5. `GET /api/alibaba/categories` on kdx-sa.com answers HTTP 500 with
   `gw.SignatureInvalid` from 1688. The same app key and secret sign correctly
   from `src/aop_client.py`, so the fault is in the Laravel signing or in the
   secret stored on that server.
6. The 1688 server only accepts inbound SSH: ports 80 and 8080 are blocked
   upstream by the host, so nothing web-facing can be served from it.

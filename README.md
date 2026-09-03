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

## The run

```bash
# what the server does every twenty minutes, all day
python3 daily_run.py --channel selected --quota 5

# the same thing without publishing anything
python3 daily_run.py --channel selected --dry-run --quota 5
```

Small and often is the client's choice, made on 2 September: *"make it split
into small batches"*, *"keep it running all day without stopping"*. The day's
size did not change — it is still the 300 points in `src/budget.py`, counted
against his Riyadh day — only how the day is spread. `deploy/kdx-batch.timer`
fires every twenty minutes; batches that arrive after the points are gone print
`nothing to do until midnight` and exit. Two batches cannot overlap: the second
finds the lock, exits 2, and `SuccessExitStatus=2` keeps that out of the failure
count so a real failure still stands out.

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

So the words decide the catalogue, and where they come from is in
`src/wordlist.py`. Two things about that were wrong until 2 September.

**The line between "not banned" and "not sold" is the client's, not a
classifier's.** The words were the tree's `allowed` leaves, and `allowed` only
means it is not on his ban list: 260 of 452 were industry and services —
machine tools, chemicals, used equipment — and one rotation landed on
内衣礼盒装 and put five women's lingerie sets into a Saudi shop. The line now
lives in `data/departments.json`, one row per 1688 department with
`sell: true|false` and, where false, a reason in Arabic. He flips a row; nobody
touches code. He answered on 2 September: the 31 industrial and service
departments are out, underwear stays, and the 21 retail departments are in.
Later the same day he changed his mind about eighteen of them and asked for
agriculture, electrical engineering, metals, textiles and leather, home
renovation, rubber and plastics, electronic components, tools, environment,
industrial machinery, packaging, safety, automotive, printing, telecoms,
machine tools, instruments and building materials by name — so 39 of 52 now
sell. That is what the file is for: it was one edit and no code.

**Asking for leaves asked for the wrong kind of word.** The built tree is two
levels deep, so a depth-2 node is a leaf only when 1688 has nothing under it —
and those are the tail buckets every department ends with: 加工定制,
项目合作, 代理加盟, 库存. The merchandise has children, so it was never a leaf
and never became a word, and seven whole retail departments had no allowed leaf
at all and were invisible: 鞋, 汽车用品, 宠物及园艺, 日用餐厨饮具,
收纳清洁用具, 居家日用品, 个护/家清.

Words are now the *children* of a selling department, leaf or not:

| | words | what they are |
|---|---|---|
| before | 458 | mostly 项目合作 / 代理加盟 / 库存 tail buckets |
| after | 473 | 连衣裙, 女鞋, 台灯夜灯, 餐具, 毛巾, 文胸 … |
| + his 18 | 974 | 电动工具, 传感器, 塑料薄膜, 安防监控设备, 水泥制品 … |

A name is split on 、 and /, a bracketed qualifier is stripped, retired
categories (`停用`) are dropped, and three lists filter what is left:
`TAIL_MARKERS`, `NOISE` (a word so broad the pool returns a random slice), and
`UNSHIPPABLE` — live animals, plants and seed, whole vehicles, antiques
(export-restricted in China), publications, pesticides, LED components. The ban
list applies to a word exactly as it does to a category, which is what stops
圣诞用品 in a department he does sell.

The eighteen industrial departments brought things that cannot reach a customer
in Riyadh at all, and those are refused at category level rather than left to
the editorial line: batteries and power banks (dangerous goods by air, the same
class of risk as the liquids he banned), drones (a GACA permit), whole
vehicles, petrol generators and fuel dispensers, live plants and animals. 整车
is deliberately not on that list — it means "the complete unit" and a bicycle
is sold that way. The known cost is 电池座, an empty battery holder, which is
ordinary merchandise and goes with the cells; he has been told.

**A category name is translated with the ones above it.** `水钻` on its own
came back as "water drills"; under 服饰配件、饰品 > 饰品配件 it is a rhinestone.
And a translation that fails is never written to the cache as though it were
the answer — that is what left 649 of 902 learned categories stuck in Chinese
and put 成人帽 in the shop menu with hats under it. A failure that caches
itself is forever, so `_retranslate` records nothing unless both names come
back free of Chinese, and `resolve` publishes no name a shopper cannot read.
`repair_categories.py` fixes rows already on disk, forty at a time.

The day's slice rotates by a whole slice, not by one word. With the offset being
the date read as a number the window moved a single word a day — twelve words
today, eleven of the same twelve tomorrow.

Not to be confused with `product.keywords.search`, which despite its name is
**not** a keyword search: asked for 连衣裙, 运动鞋 and a nonsense string it
returns the identical 978 rows. It is a fixed list, and nothing here uses it.

## Two photographs the filter said no to, and the shop showed anyway

Both from the same 2 September message, and both a leak rather than a
misjudgement: the filters were right about the photographs and were being asked
about the wrong list.

**The poster filter only ever saw the gallery.** `order_gallery` re-ordered and
pruned `payload["images"]`; the variant blocks carry their own `image` and
`images`, and nothing touched those. So a photograph scored as an advertising
poster was removed from the gallery and published regardless, one level down,
inside the colour it belonged to. Counted over the live catalogue before the
fix: **170 such photographs across 48 of the 230 products**. Re-scored, they
came back 8.9%–20.2% Chinese text against a 5% limit, while the photographs that
had survived into the same galleries scored 0.1%–0.4% — the filter had judged
them correctly and been overruled by the layout.

A colour is still never emptied. When every photograph it owns is over the
limit, the cleanest one stays: a blank frame beside a live price is worse for
the shopper than a caption inside a picture, and it is the trade `order_gallery`
already makes at product level.

**Duplicates were counted by URL.** 1688 serves one photograph from more than
one path, so deduplicating URLs never caught it. Fetched and hashed across 12
published products: **3 of 91 photographs were a byte-identical second copy**.
`PhotoChecker` already holds the bytes for the text scorer, so the fingerprint
costs nothing extra. No fingerprint means keep — the checker drops bodies once
it is over its memory budget, and "I did not look" must never read as "duplicate".

Both suites' photo stubs answered the same six bytes for every URL, which under
content-dedupe makes every photograph in the suite a copy of the first one. That
is a fixture that cannot express the thing being tested, so the fixtures were
fixed and the rule pinned with two URLs that deliberately do share bytes.

## The weight his shop never received

The client photographed his own cart on 2 September: 12.34 SAR of goods, fast
delivery selected, and *free shipping* printed against the total. A second
photograph, after he typed a weight into a product by hand, showed the same cart
charging 28.00 SAR of delivery. His shop prices fast delivery from a product
weight, and it had never been sent one.

Not "sent a wrong one" — sent none. The number lived only inside
`variants[].sizes[]`, and a product with no size axis carried it nowhere at all:
**95 of the 230 live products**. The other 135 carried it somewhere his importer
does not look.

The same measurement answered a second complaint of his in the same breath.
Every one of those 230 products came back `needs_shipment: true`. Not most —
**all of them**. There had never been a free-shipping product in the shop.

> **The paragraph that followed here was wrong, and the correction is measured
> below in "He refused to fill it, and he was right".** It read that nothing in
> reach reports a weight over 2 kg, on the strength of "`unitWeight` for 4
> offers in 20, the largest 1.0 kg". Those twenty offers were all light
> clothing. Over 1,693 offers drawn across 60 departments on purpose, half
> declare a weight and 7.2% of those are over the line. The shop had no
> free-shipping product because the sample I looked at had none, not because
> the catalogue has none.

What remains true from that paragraph: the discovery channel returns no weight
at all, and `alibaba.product.get`, which carries the real shipping weight, is
still `gw.APIACLDecline` on both apps.

### Which key does his importer read it under?

Not a guess, and not yet answered. Type-probed against the live endpoint:

```
CONTROL price = "abc"            -> 422, "must be a number"
CONTROL name_en removed          -> 422, "field is required"
CONTROL needs_shipment = "abc"   -> 422, "must be true or false"
CONTROL images = "abc"           -> 422, "must be an array"
weight, weight_kg, product_weight, shipping_weight, wt, gross_weight,
net_weight, package_weight, weight_grams, weight_g, item_weight, weightKg,
productWeight, shippingWeight, weight_value, unit_weight  = "abc"
                                 -> accepted, all eighteen
```

The four controls are the point: the validator is real and specific, and no
spelling of weight is in it. An absent field under names I invented is not proof
the feature is absent, so the question went to the client rather than a
nineteenth guess — and `KDX_WEIGHT_FIELD` holds the key so his answer costs a
restart, not a release.

He answered on 2 September: `weight`. That is what the code already shipped by
default, so the payload was right and the question was worth asking anyway —
seventeen of the eighteen spellings would have been silently discarded.

### A table written per department, looked up per leaf

His table names departments. 1688 files an offer against a leaf. Measured on the
3,776 offers in the queue that evening:

```
distinct leaf category ids                          712
offers whose ancestry was already known             817   (22%)
offers whose leaf resolved to no department       2,959   (78%)
```

A lookup on the leaf id alone would therefore have missed roughly four products
in five while looking exactly like a table that worked — every one of them
quietly taking the fallback weight. So `_weigh_by_category` asks the whole
ancestry instead, leaf first: a number typed against a leaf is more specific and
wins, a number typed against a department reaches everything beneath it. The
chain is the same one the shop menu is built from, and by the time it is needed
the category walk has already paid for it.

A weight the source actually measured is never overwritten by a table entry, and
a category average stays marked as assumed — it is a policy, not a scale.

### He refused to fill it, and he was right

On 3 September, having been sent the 39 departments to price up:

> "I cannot give you a weight per category, because every main category has
> subcategories, and the subcategories hold big products and small ones — some
> need fast shipping and some need free shipping. […] Let the comparison be done
> by the system, not by hand. We will pass more than 500,000 subcategories; they
> cannot be filed by hand."

So the table stops being the plan. `KDX_CATEGORY_WEIGHTS` still wins where he
types a number — the only reason to type one is to state a real one — but
nothing waits for it. `src/weights.py` works the number out instead.

**Where the number comes from.** The first thing to check was whether the data
carries one at all, and the answer already in this file — "8 of 30" — came from a
30-offer sample of light clothing. Re-measured 3 September over two sweeps of the
pool, 1,693 distinct offers drawn across 60 departments **on purpose** so the
sample was not all one shelf:

```
declared a weight in shippingInfo         851 / 1,693   (50%)
    under unitWeight                      most of them
    under offerSuttleWeight               only ever beside unitWeight
    under plain `weight`                  never once
credible (see the ceiling below)          848
    of those, over the 2 kg line           61   (7.2%)
```

That second line corrects something else this file used to say. Products over
2 kg **do** exist — one leaf holds fifteen 15 kg factory inspection robots. The
shop has never had a free-shipping product because the sample that was looked at
had none in it, not because the catalogue has none. It also sets the expectation
for his free-shipping batch: about **7% of the pool**, not half of it.

**Learning the rest.** For the offers that declare nothing, the weight is the
median of what the *same leaf category* declared — and the category is only asked
when it has at least three measurements **and all of them sit on the same side of
the 2 kg line**. That second condition is his objection turned into a test: a
leaf that really does hold a screw and a toolbox straddles the line, fails it,
and is never asked.

Leave-one-out over the corpus — every declared weight predicted from the other
offers in its own category, and the light/heavy verdict compared with the truth:

```
ungated (any category with 3+ samples)   598 right   14 wrong    97.7%
gated on the category agreeing with itself
                                         544 right    5 wrong    99.1%
categories that straddle the line          8 of 170   - never asked
```

And the five it still gets wrong all fail the same way: **a heavy product called
light**, so the customer is charged carriage on something that should have
shipped free. Not once does it put a heavy product on free shipping at the shop's
expense. That is the direction to fail in, and it is measured, not hoped for.

End to end over those 1,693 offers:

```
848  carry the supplier's own weight
463  are answered by their category
382  fall to the light default                       (23%)
```

**77% of the catalogue gets a weight from real data and nobody typed a number.**
The remaining 23% keep his light-weight policy of 30 August, which is the safe
side: the customer is charged carriage rather than the shop paying it.

`data/category_weights.json` ships that corpus so a fresh machine starts with it
instead of learning from nothing; every batch adds what it sees, including the
offers it drops on a banned category, because their declared weight is just as
true as a published one.

### A declared weight can be a typo, and it fails expensively

Three of the 851 are not weights. Two lots of plastic granulate at 1,000 kg —
a raw material priced by the tonne — and a brass horn at **10,000 kg**, which is
a supplier who typed grams. Over 2 kg the product ships *free* and the shop pays
the carriage, so trusting those costs real money.

Anything above `KDX_MAX_CREDIBLE_WEIGHT_KG` (100 kg, which keeps a believable
100 kg carbon-steel civil-defence valve) is treated as though nothing had been
declared: the category answers instead, and nothing is silently rewritten to a
number the supplier never gave. It gets no vote on its category either, or one
typo would turn a whole leaf into a free-shipping department.

There is no floor beyond "greater than zero". 68 offers declare under 10 g, and
some of them are true — a postage stamp really is a gram — while the ones that
are wrong fail in the safe direction.

## "If the information is unclear, exclude it"

He wrote that on 3 September, and it retires a habit this pipeline has had
since the first night: when a fact was missing, invent a safe one.

> اي منتج سواء الى الشحن السريع ام الشحن المجاني اذا كانت معلومات المنتج غير
> واضحة يتم استبعاد المنتج لا مشكلة هناك منتجات بالمليارات

The weight is the one that changes the catalogue. Of the corpus above, 848
offers carry a supplier weight and a confident category can answer for 463
more; the remaining 382 — 23% — had been getting a blanket "call it light",
which then decided their shipping type, their price and whether they published
at all. A blanket default is not a measurement, and he has just said so. Those
382 are now refused, named `no_weight` in the audit, with an Arabic line
saying both sources were asked and neither answered.

Five other checks travel with it — no title, no photograph, no category, no
purchasable option, and a name that would reach the shop still carrying Chinese
characters. All of them run in `src/completeness.py`, all of them run **before**
the size lookup, the translation and the image search, because those are the
three calls that cost him money and a listing we will not publish must not pay
for any of them.

Two escape hatches, because a check that rejects too much has to be droppable
without a release: `KDX_REQUIRE_COMPLETE=off` waives all of it, and
`KDX_COMPLETENESS_SKIP=no_weight,no_photo` drops individual checks by the same
name that appears in the audit file. `verify_completeness.py` — 39 assertions.

## The comparison, measured end to end

His rule of 3 September makes the five-platform comparison the gate on the
whole free-shipping catalogue: *"if the comparison is done and the product is
not obtained, the product is excluded and not pasted into our shop. This
includes the heavy products."* So the only number that matters is how often the
comparison actually finds a rival — and it was 1.1%.

**Live, from the September audit log:** 5,944 rows published, 65 of them
(1.1%) priced from a rival, 5,879 priced by margin. 233 more rejected as
`heavy_and_unmatched`. From the comparison cache: 296 products compared, 1.03
searches each, 2 with a qualifying hit.

### First: is that the catalogue, or is it me?

40 real products, one Lens search each, every response kept. 2,394 visual
matches, 385 of them on one of the five platforms, **and at least one platform
appears for 39 of the 40 products.** The platforms were being thrown away, not
missed — by a rank rule of my own invention. That fix is its own commit; the
short version is that Lens returns `position` and no similarity score, one
M-VAVE SMK-37 PRO keyboard comes back eleven times at eleven different ranks,
and identity belongs to the words.

### Then: how far does fixing it actually get?

Run down the whole path rather than reasoned about, because fixing the input
and announcing the feature is the mistake this project already made once with
the weight:

| stage | products, of 40 |
|---|---|
| Lens returned something | 40 |
| one of the five platforms appeared | 39 |
| a row that is genuinely the same product — **before** the fix | 1 |
| a row that is genuinely the same product — **after** | **8** |
| that row carried a SAR price in the Lens response | 0 |
| second (Shopping) search bought | 8 |
| ended with a rival's price | **1** |

So identity improves eight-fold and the end-to-end rate goes from about 1.1% to
about 2.5%. Better, and not the transformation the identity number alone would
suggest.

### Why the second search does not rescue it

149 shopping rows across those 8 products, every one classified:

```
120  not on any of the five platforms
 16  on one of the five, but not the one the picture identified
  9  ACCEPTED
  4  words below the bar
```

The 120 are a long tail of small international shops — eBay, Made-in-China,
office-japan.jp, Raptor Supplies, a Nepali gift shop. Widening the platform
list to take them was the obvious next idea and the measurement killed it:
those are not the Saudi market, and a wholesale price from Made-in-China is not
a rival's shelf price.

### The 16, and what he decided about them (3 September)

The second line of that table was worth something, and he took it:

> عند المقارنة في التطبيقات 5 سيقوم النظام بأعتماد السعر الافضل بينهم
> — بعد المقارنة النظام يستقبل ويستخدم السعر الارخص بين 5 تطبيقات

A price may now come from any of the five, not only the one the picture landed
on. Replayed over the shopping responses already bought, at no further cost:

| | before | after |
|---|---|---|
| priced rows accepted | 9 | **17** |
| products ending with a rival's price, of 40 | 1 | **3** |

So the end-to-end rate goes from about 2.5% to about **7.5%**. Seven of the 8
newly accepted rows are plainly the same product. The eighth is the one to
watch, and it is recorded here rather than quietly dropped: for a telescopic
flag pole the cheapest row moves 28.00 → **19.87 SAR**, and since `best_match`
takes the cheapest, *the weakest-matching row is the one that sets the price*.
Its words score is 50.00, sitting exactly on the bar.

The bar was **not** raised unilaterally to exclude it. A Tazweeq telescopic
handheld flagpole is a telescopic handheld flagpole, and a cheap local brand
undercutting an import is what a rival price *is*; inventing a threshold to
remove an inconvenient row is the mistake this module was repaired for a commit
earlier. So the number went to the client with the measurement, and he chose to
tighten it — *"نعم اوافق بشدة اشكرك"*. `KDX_CROSS_PLATFORM_PRICING=off` still
restores the old rule exactly, and `KDX_UNBACKED_TEXT_MIN=50` restores the old
bar.

### Choosing that bar, and why a bar alone was wrong

Sweeping the unbacked bar over the same recorded responses — 8 products that
reached the shopping stage, 16 picture-unbacked rows between them:

| bar | rows | unbacked kept | products priced | flag pole pays |
|---|---|---|---|---|
| 50 | 17 | 8 | 3/8 | 19.87 |
| **55–65** | 16 | 7 | 3/8 | **28.00** |
| 70 | 13 | 4 | **1/8** | 28.00 |

55–65 is a plateau costing exactly the one row in question. 70 is where it
stops being free: the kazoo's best row and the notebook's both score 66.67, so
a bar of 70 throws away two thirds of the coverage the client's other ruling
just bought. Tightening past the plateau undoes (b) by the back door. **60**
sits in the middle of the plateau rather than on the 50.00 boundary the outlier
occupies — `text_score` is coarse here, the real scores being 50.00, 66.67,
75.00, so anything in 51–66 is the same rule.

Then applying 60 broke a test, which is the useful part. The 30 L urn that made
the case for the client's change **scores exactly 50.00** against our title, and
it is picture-unbacked too, so a flat 60 threw away the very row this section
holds up as the change paying off. 50.00 is a class containing both the row
worth dropping and the row worth keeping, and no threshold on that one number
separates them.

What separates them is that the urn **states our capacity** and the flag pole
states no specification at all:

| row | words | ours | theirs | |
|---|---|---|---|---|
| REFURA 30 L urn | 50.00 | `30l 3000w` | `30l` | **kept** |
| KOOLEN 25 L kettle | 25.00 | `30l 3000w` | `25l 1200w` | out |
| Tazweeq flag pole | 50.00 | — | — | out |

So below the bar an unbacked row is kept only if it agrees with us on a
specification — the client's own identity test, the same rule that vetoes the
25 L kettle, standing in for the picture that is missing. `words < 50` is still
a floor nothing crosses by any route. Shipped effect on the 8 products: rows
17 → 16, products priced **unchanged at 3/8**, and exactly one chosen price
moves — the flag pole, 19.87 → 28.00.

The recorded 30 L boiler shows the same change from the other side, and there
it plainly pays: the old rule compared against AliExpress at 520 SAR, because
the picture had never named Amazon or Noon. The cheapest genuine row is a Noon
listing at **329.00** — *"REFURA Water Urn Boiler, 30 Litre Capacity"* — so the
shop was about to undercut a price 191 SAR above the real Saudi shelf. A KOOLEN
**25 L** kettle sits seven riyals below that at 322.09 and is correctly vetoed
on capacity, which is the guard that makes the rest safe.

**The conclusion is about the goods, not the code.** 1688 sells unbranded
generics, and an unbranded generic does not exist as an identifiable listing on
Temu, SHEIN, AliExpress, Amazon or Noon. Where a product has a model number —
PANTUM M7100DW, M-VAVE SMK-37 PRO — the comparison finds it immediately. Where
it is "A5 notebook", there is nothing on the other side to find.

### When every rival is cheaper than our own cost

His other rule of the same day, and it splits on shipping type:

> اذا تمت المقارنة في 5 التطبيقات وكلها تبيع المنتج بخسارة فيتطبق هامش الربح
> الذي ارسلته لك — هذا معتمد في المنتجات الصغيرة التي تحتوي على الشحن السريع

A **light** product is no longer thrown away for being cheaper abroad; it falls
through to the margin, priced from our own cost, and the rival stays on the
audit row as the evidence (`margin_rivals_below_cost`). A **heavy** one still
stops at `would_sell_at_loss`, because heavy is where he says the danger lies.

This branch had fired **zero times in 6,354 real audit rows**, which on its own
cannot tell "never happens" apart from "unreachable". `verify_pipeline.py` §3c
now drives it with a constructed rival and a control proving the fixture really
is a loss, plus a control that an ordinary rival above cost is still undercut.

### The hole this leaves, stated plainly

`Engine.landed_cost_sar` is the 1688 price in riyals and **nothing else** — no
freight, despite the name. So the loss guard protects the goods price only,
which is precisely the risk he described:

> المنتجات الكبيرة هي تحسب بالابعاد وبعض الاحيان يكون سعر الشحن اعلى من سعر المنتج

A bulky, light product is charged on volume and its freight can exceed
everything the engine counts. Today the comparison substitutes for that — a
rival's shelf price has shipping baked in — which is the real reason he will not
publish a heavy product without a match. Closing it needs two numbers from him,
his cost per real kilo and per volumetric kilo, and they are deliberately not
guessed here.

## Photographs at the size his shop will show them

The CDN resizes on request: `<url>_800x800.jpg`. Measured over 25 first
photographs of the 2 September catalogue — every one answered, none was enlarged
(688×688 stayed 688×688; 1433×1920 came back 597×800, so the shape is kept), and
the 25 together fell from **7.4 MB to 3.6 MB**.

The trap is where the small copy is read rather than merely shipped. A first
sample of 30 ordinary photographs showed no poster verdict changing at all — and
proved nothing, because it contained no posters. Repeated on twelve photographs
that actually scored above the 5% line:

```
 8.70 ->  8.53      5.87 ->  5.87     13.24 ->  7.48
 6.43 ->  2.31 X    5.50 ->  3.61 X    5.07 ->  4.34 X
 8.86 ->  8.86     12.62 ->  6.92      7.86 ->  0.31 X
 8.93 ->  8.92     10.72 ->  8.51     29.47 -> 35.70
                                    X = would now be published
```

**Four of twelve posters** would have got through. So every decision — the
poster filter, the reachability check, the content dedupe — is taken on the
full-size photograph, and only the URL that finally ships is swapped, after all
of them. Where the small copy does not answer, the original ships: his importer
copies a picture once and there is no second chance.

`KDX_IMAGE_DISPLAY_PX=0` turns it off and the original URLs ship, which is what
happened before this existed.

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

### Option names, and the Chinese the client found in his own cart

On 2 September the client photographed his shopping cart. The pushchair in it
had one option, and it read `M005-单向推车-黑色-标配款-单手折叠（可坐可趟）`.
Measured across everything published: **238 of 1,415 options, on 20 products,
carried Chinese in the Arabic field.**

The cause was the fallback in `to_kdx_variants` — keep the original label when
the translator returned nothing — which is the right failure and the wrong thing
to publish. It is the same mistake the category cache made a few hours earlier,
one level down: *a failed translation stored as though it were an answer.* When
you fix one of these, grep for its siblings.

Four things now stand between a Chinese label and the shop, in the order they
run:

1. **Declutter first.** 1688 labels arrive carrying ornament —
   `✷☽☽\t5号守门员手套（身高建议130CM左右）✷☽☽`. Measured against the live model:
   with the ornament attached the label comes back unchanged, and stripped of it
   the same model translates the whole thing correctly on the first ask. Symbols
   and format characters go, control characters become a space (a tab was
   holding two words apart), and `℃ ° ± ×` stay — those mean something in a size.
2. **Ask again for what is still Chinese**, by itself. A short list is easier
   than a long one.
3. **Cut the label up and ask for the pieces.** A compound SKU label is a whole
   specification joined with dashes, and the model hands that shape straight
   back. The separator set was built by measurement, re-running the same 234
   stuck labels after each addition: dash alone left 85, adding `~` left 59,
   adding brackets and single spaces left 0. One addition made it *worse* before
   it made it better — see `_AS_ASCII` in `src/enrich.py`: `【 】 、 ，` are
   themselves CJK characters, so a label rebuilt with them still in it is
   correctly judged Chinese and thrown away. They are written out as ASCII. The
   seam where a latin part code runs straight into Chinese — `E2守门员手套蓝色` —
   counts as a separator too, with no character to preserve, so a space is
   written where it was.
4. **Refuse whatever survives.** An option whose name is still Chinese is not
   published. Per option, not per product: a colour that will not translate
   costs that colour and not the other eight, and the refusal is written to the
   audit as `untranslated_option` so the cost is visible. Off when no translator
   is configured at all, because that mode is deliberately untranslated and this
   guard must not turn "no API key" into "no catalogue".

Result on the live model: the 234 labels that had reached the shop in Chinese
all translate, and the batches published after the fix carry no Chinese option
at all. His import inserts and does not update, so the eleven products already
in his shop with Chinese options have to be deleted before they can be
republished — he was given the list.

### What 1688 thinks of our listing volume

`fenxiao.risk.queryGoodsRisk` is **not** the per-product risk check its name
suggests, and the permission list it arrived on made it look like one. It takes
no offer id: only `publishCount` (products listed today) and `onCount` (products
currently on sale), and it answers with one word about the *account* — 无 / 低 /
中 / 高. It cannot back the banned-term filter, which is what the client was told
before it was called; the correction went to him the same day, and
`verify_risk.py` holds it in place with a CONTROL that no offer id is ever sent.

`src/risk.py` reads it once per run, **before** the publishing loop rather than
after — a warning that arrives once the products are up has already cost the
thing it was meant to protect. Medium or high stops the run. A reading that
could not be taken is not a reading of "no risk": a timeout, a business refusal
and a success naming no level all give `None`, which never halts anything, and
neither does a level this module has not heard of. `KDX_IGNORE_RISK=1` lifts the
halt while still taking the reading.

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

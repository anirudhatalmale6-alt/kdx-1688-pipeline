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

## Still open

1. **How the system checks Temu, SHEIN, AliExpress, Amazon and Noon.** None of
   the five offers image search to third parties. The engine is written so the
   comparison source is pluggable — it consumes a list of `CompetitorHit`
   objects and does not care where they came from — but something has to
   produce them.
2. The KDX endpoint contract: URL, auth header, expected JSON. Only
   `FIELD_MAP` and `ENDPOINTS` in `src/kdx_client.py` change when it arrives.
3. Which translation service pays for the Chinese → Arabic/English rewriting.
4. The CNY → SAR rate: fixed number, or fetched daily.

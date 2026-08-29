# شكل المنتج المطلوب في KDX — كل الصور وكل الأسعار
# KDX product shape — every photo, every price

الملف المرجعي: [`samples/kdx_product_with_variants.json`](../samples/kdx_product_with_variants.json)
مُولَّد آلياً من الكود، ومختبر في `verify_variants.py` (24 اختبار).

---

## الفكرة في سطر واحد

`variants` هي وحدة العرض: كل عنصر فيها = صورة واحدة + الأسعار التي تخصها.

```json
"variants": [
  {
    "original": "红色", "en": "Red", "ar": "أحمر",
    "image":  "https://.../red.jpg",
    "images": ["https://.../red.jpg", "https://.../red-back.jpg"],
    "price": 45.90, "price_min": 45.90, "price_max": 47.50,
    "sizes": [
      {"original":"S","en":"S","ar":"صغير","price":45.90,"sku_id":"sku-1","stock":40,"weight":0.42,"needs_shipment":true},
      {"original":"M","en":"M","ar":"وسط","price":47.50,"sku_id":"sku-2","stock":120,"weight":0.45,"needs_shipment":true}
    ]
  }
]
```

عند العرض: اطبع `image`، وتحته `price`. عند اختيار مقاس، بدّل السعر إلى `sizes[].price`.

---

## حقيقة مهمة عن 1688 قبل البناء

في 1688 الصور مرتبطة باللون / الشكل، وليست مرتبطة بالمقاس أبداً. السعر والمخزون
مرتبطان بالـ SKU الكامل (لون × مقاس).

النتيجة العملية: مقاسان من نفس اللون لهما نفس الصورة وسعران مختلفان. هذا ليس خطأً
في السحب، هذا هو تنظيم 1688 نفسه. لهذا السبب `variants` مجمّعة حسب الصورة: حتى لا
يظهر سعر تحت صورة لا تخصه.

لا توجد "صورة لكل مقاس" في 1688. لو احتجتم ذلك فهو غير متاح من المصدر.

---

## ملف نموذجي حقيقي (وليس مكتوباً باليد)

`samples/kdx_payload_example.json` هو **جسم الطلب كاملاً** كما سيصل إلى
`POST /api/v1/products/import`، بما فيه `{"products": [...]}`.

ولّده النظام نفسه بتشغيل حقيقي (`run_pipeline.py --dry-run`): الأسماء والأوصاف
مترجمة بالذكاء الاصطناعي فعلاً، والأسعار محسوبة بقواعدكم من سعر الصرف الحقيقي،
والفئة مستخرجة من شجرة فئات 1688 المبنية. لا يوجد فيه أي حقل كتبته يدوياً.

### كتلة الفئة `category`

تُملأ الآن تلقائياً من شجرة الفئات:

- `main_category` = الفئة الجذر التي ينتمي إليها المنتج
- `sub_category` = الفئة الفعلية المذكورة في إعلان 1688

وإذا كانت الفئة خارج الشجرة المبنية حتى الآن، تصل الكتلة **فارغة** ولا تُملأ
بتخمين. فئة خاطئة في اللوحة لا يلاحظها أحد، أما الفارغة فتُرى وتُصلَّح.

---

## الحقول

| الحقل | المعنى |
|---|---|
| `variants[]` | جديد. وحدة العرض: صورة + أسعارها |
| `variants[].image` | الصورة الرئيسية لهذا اللون |
| `variants[].images[]` | كل صور هذا اللون |
| `variants[].price` | السعر الذي يظهر تحت الصورة (أرخص مقاس فيها) |
| `variants[].price_min` / `price_max` | لعرض نطاق سعري إن رغبتم |
| `variants[].sizes[].price` | السعر النهائي لهذا المقاس بالريال |
| `variants[].sizes[].stock` | المخزون. صفر = لا يُعرض المقاس |
| `variants[].sizes[].sku_id` | معرف الـ SKU من 1688، للتحديثات اللاحقة |
| `variants[].sizes[].needs_shipment` | `true` = شحن سريع (≤ 2 كجم)، `false` = شحن مجاني |
| `price` | جديد المعنى: أرخص سعر في المنتج كله — لبطاقة المنتج |
| `price_min` / `price_max` | جديد. النطاق السعري للمنتج كله |
| `images[]` | كما هو: كل الصور للمعرض |
| `sizes[]` | كما هو تماماً: أسماء المقاسات فقط بدون أسعار |

كل الأسعار بالريال السعودي ونهائية — بعد الهامش أو الخصم التنافسي والتقريب.
السعر الصيني لا يصل إليكم إطلاقاً.

---

## تنبيه واحد مهم على جانب Laravel

قِسته سابقاً على `/api/v1/products/import`: أي حقل غير مذكور في الـ Validator
يُحذف بصمت والردّ يبقى **200**. أي أنكم ستستقبلون نجاحاً كاذباً.

قبل التبديل، أضيفوا إلى الـ Validator:

```php
'variants'                    => 'array',
'variants.*.original'         => 'string',
'variants.*.en'               => 'string',
'variants.*.ar'               => 'string',
'variants.*.image'            => 'string',
'variants.*.images'           => 'array',
'variants.*.price'            => 'numeric',
'variants.*.price_min'        => 'numeric',
'variants.*.price_max'        => 'numeric',
'variants.*.sizes'            => 'array',
'variants.*.sizes.*.original' => 'string',
'variants.*.sizes.*.en'       => 'string',
'variants.*.sizes.*.ar'       => 'string',
'variants.*.sizes.*.price'    => 'numeric',
'variants.*.sizes.*.sku_id'   => 'string',
'variants.*.sizes.*.stock'    => 'integer',
'variants.*.sizes.*.weight'   => 'numeric',
'variants.*.sizes.*.needs_shipment' => 'boolean',
'price_min'                   => 'numeric',
'price_max'                   => 'numeric',
```

اختبار القبول: أرسلوا الملف النموذجي، ثم اقرأوا المنتج من قاعدة البيانات وتأكدوا
أن `variants` موجودة فعلاً. الردّ 200 وحده لا يثبت الحفظ.

---

## التوافق مع الموقع الحالي

`sizes` و `images` و `price` كلها ما زالت موجودة وبنفس الأنواع، فالموقع الحالي لا
ينكسر يوم التبديل. `variants` إضافة فوقها. من يقرأ القديم يستمر، ومن يقرأ الجديد
يحصل على كل الصور وكل الأسعار.

---

## English summary

`variants` is the render unit: one entry = one photo plus the prices that belong
to it. Render `image` with `price` under it; switching size switches to
`sizes[].price`.

1688 attaches photos to the colour axis, never to the size axis, while price and
stock belong to the full colour × size SKU. So two sizes of one colour share a
photo and carry two prices. Per-size photos do not exist at the source.

`price` is now the cheapest price in the offer (product card); `price_min` and
`price_max` give the range. `images` and `sizes` are unchanged, so the current
front end keeps working — `variants` is purely additive.

Laravel silently drops fields missing from the validator and still answers 200,
so add the rules above before switching, and confirm by reading the row back
rather than trusting the status code.

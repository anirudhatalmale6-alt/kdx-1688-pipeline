"""
The per-product audit log the client asked for.

Every product that the pipeline looks at leaves exactly one row, whether it was
published or rejected, with the reason in Arabic. The file is a CSV so it opens
in Excel without anyone installing anything.

Written with append + flush per row on purpose: if the night's run dies at 3am,
the rows written before it died are still on disk and still explain what the
system had decided.
"""

from __future__ import annotations

import csv
import os
import time
from datetime import datetime

import paths

COLUMNS = [
    "timestamp",
    "offer_id",
    "sku_id",
    "decision",
    "reason_code",
    "reason_ar",
    "cost_sar",
    "matched_platform",
    "match_score",
    "competitor_price_sar",
    "final_price_sar",
    "pricing_basis",
    "requires_shipping",
    "shipping_type",
    "points_spent",
]

REASONS_AR = {
    "published": "تم النشر",
    "updated": "تم التحديث",
    "banned_category": "فئة ممنوعة",
    "department_off": "قسم مستبعد بطلب العميل",
    "not_mains_compliant": "لا يعمل على 220 فولت 50/60 هرتز",
    "out_of_stock": "غير متوفر في 1688",
    "heavy_unmatched": "أثقل من 2 كجم وغير موجود في المنصات الخمس",
    "below_cost": "السعر بعد الخصم أقل من التكلفة",
    "no_price": "لا يوجد سعر حقيقي للمنتج",
    "no_images": "لا توجد صور صالحة للمنتج",
}


class AuditLog:
    def __init__(self, path: str | None = None):
        self.path = path or paths.state_path(
            os.path.join("logs", f"audit-{datetime.now().strftime('%Y-%m')}.csv"),
            "KDX_AUDIT_LOG")
        self._ensure_header()

    def _ensure_header(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            with open(self.path, "w", newline="", encoding="utf-8-sig") as handle:
                csv.writer(handle).writerow(COLUMNS)

    def write(self, record, points_spent: int = 0) -> dict:
        row = record.as_dict() if hasattr(record, "as_dict") else dict(record)
        # setdefault is not enough: the engine sets reason_ar to an empty string
        # rather than leaving it out, and an empty reason is the one thing this
        # log must never contain.
        if not row.get("reason_ar"):
            row["reason_ar"] = REASONS_AR.get(row.get("reason_code", ""), "")
        row["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        row["points_spent"] = points_spent

        with open(self.path, "a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writerow({column: row.get(column, "") for column in COLUMNS})
            handle.flush()
        return row

    def counts(self) -> dict:
        """Totals per decision, for the one-line summary at the end of a run."""
        tally: dict = {}
        try:
            with open(self.path, newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    key = row.get("decision", "")
                    tally[key] = tally.get(key, 0) + 1
        except OSError:
            pass
        return tally

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
    # 5 September, with his rate card. cost_sar now has freight inside it, so
    # without these three the log stops being able to explain its own numbers.
    #
    # Appended strictly LAST, after points_spent, even though they would read
    # better next to cost_sar. September's file already holds rows written
    # under the fifteen-column header; anything inserted before the end shifts
    # every one of those rows by three columns the moment the file is upgraded
    # or the upgrade is skipped. Put them at the end and both the old rows and
    # the new ones line up under either header.
    "freight_sar",
    "volume_m3",
    "volume_source",
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
            return
        self._widen_header()

    def _widen_header(self) -> None:
        """
        Add columns that appeared after this month's file was started.

        Only ever widens, and only when the header on disk is a prefix of the
        current one - if it is anything else the file is left alone, because a
        log the client has been reading is evidence, not scratch space, and a
        rewrite that guesses at a mismatch could destroy it. The old file is
        kept beside the new one either way.
        """
        try:
            with open(self.path, newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
        except OSError:
            return
        if not rows:
            return
        header = rows[0]
        if header == COLUMNS or header != COLUMNS[:len(header)]:
            return

        backup = f"{self.path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        os.replace(self.path, backup)
        pad = [""] * (len(COLUMNS) - len(header))
        with open(self.path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            for row in rows[1:]:
                writer.writerow(row + pad[:max(0, len(COLUMNS) - len(row))])

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

"""
Resolve any 1688 category on demand, instead of only the ones we walked.

The pre-built tree holds 1,497 categories - the 49 departments and one level
below. That was enough while the catalogue was skirts. It is not enough for a
general catalogue: a live run on 30 August put twelve office products through,
and every one of them resolved to nothing, because leaf ids like 1045585
(办公椅, office chair) sit below the walked depth.

Two consequences, and the second is the serious one:

  * KDX shows the product with no department. Visible, and merely untidy.
  * `state_of` answers "unknown", and unknown never rejects - it cannot, since
    refusing every unwalked id would refuse the catalogue. So while nearly every
    category is unknown, the category ban filter is effectively switched off and
    only the Chinese title stands between a prohibited product and the shop.
    That is the filter the client asked for by name.

`alibaba.category.get` is granted on this appKey, and it answers with the name,
whether it is a leaf, and `parentIDs`. So any id can be climbed to its root, one
call per category, and the whole chain classified with the same rules the built
tree used. A blocked ancestor blocks the leaf - that is the point.

Every answer is cached to disk, because a category is a fact about 1688, not
about tonight. Nights converge on a few hundred categories and stop calling.
"""

from __future__ import annotations

import json
import os

import catalog
import paths

MAX_CLIMB = 8  # 1688 is four deep; this is a loop guard, not a limit


def cache_path() -> str:
    return paths.state_path("category_cache.json", "KDX_CATEGORY_CACHE")


class LiveIndex:
    """
    A CategoryIndex that asks the gateway about ids it has not seen.

    Presents exactly the three things the pipeline uses - `resolve`, `state_of`
    and `by_id` - so nothing downstream knows the difference.
    """

    ROUTE = None  # built lazily, so importing this module needs no client

    def __init__(self, index, client=None, translate=None, cache: str = "",
                 max_calls: int = 400):
        self.index = index
        self.client = client
        self.translate = translate
        self.cache_file = cache or cache_path()
        self.max_calls = max_calls
        self.calls = 0
        self.failures = 0
        self.learned = self._load()

    # -- disk ------------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self.cache_file, encoding="utf-8") as handle:
                rows = json.load(handle)
        except (OSError, ValueError):
            return {}
        return rows if isinstance(rows, dict) else {}

    def save(self) -> None:
        directory = os.path.dirname(self.cache_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            json.dump(self.learned, handle, ensure_ascii=False, indent=1)

    # -- the gateway -----------------------------------------------------
    def _fetch(self, category_id: str) -> dict | None:
        if self.client is None or self.calls >= self.max_calls:
            return None
        import aop_client
        route = aop_client.ApiRoute(namespace="com.alibaba.product",
                                    api_name="alibaba.category.get")
        self.calls += 1
        try:
            payload = self.client.call(route, {"categoryID": str(category_id)})
        except Exception:  # noqa: BLE001
            # One unreachable category must not stop a night. It stays unknown,
            # which is what it was before this module existed.
            self.failures += 1
            return None
        rows = payload.get("categoryInfo") or []
        if not rows:
            self.failures += 1
            return None
        row = rows[0]
        parents = row.get("parentIDs") or []
        return {"id": str(row.get("categoryID") or category_id),
                "name_zh": str(row.get("name") or ""),
                "is_leaf": bool(row.get("isLeaf")),
                "parent_id": str(parents[0]) if parents else None}

    def _known(self, category_id: str) -> dict | None:
        """The built tree first - it is already translated and already classified."""
        row = self.index.by_id.get(str(category_id))
        if row is not None:
            return {"id": str(row["id"]), "name_zh": row["name_zh"],
                    "name_en": row.get("name_en") or row["name_zh"],
                    "name_ar": row.get("name_ar") or row["name_zh"],
                    "parent_id": (str(row["parent_id"])
                                  if row.get("parent_id") is not None else None),
                    "state": row.get("state", catalog.ALLOWED),
                    "reason": row.get("reason", "")}
        return self.learned.get(str(category_id))

    def _learn(self, category_id: str) -> dict | None:
        row = self._known(category_id)
        if row is not None:
            return row
        fetched = self._fetch(category_id)
        if fetched is None:
            return None
        state, reason, _ = catalog.classify(fetched["name_zh"])
        fetched["state"] = state
        fetched["reason"] = reason
        fetched["name_en"] = fetched["name_zh"]
        fetched["name_ar"] = fetched["name_zh"]
        if self.translate is not None and fetched["name_zh"]:
            try:
                names = self.translate(fetched["name_zh"])
                fetched["name_en"] = names.get("en") or fetched["name_zh"]
                fetched["name_ar"] = names.get("ar") or fetched["name_zh"]
            except Exception:  # noqa: BLE001
                pass  # the Chinese name is a truthful fallback
        self.learned[str(category_id)] = fetched
        # Written through, not at the end of the run. The first live night
        # resolved fourteen products' departments correctly and left no cache
        # file at all, because nothing ever called save() - the checks did, and
        # that is precisely why they did not catch it. A category is a fact
        # about 1688; a night that crashes at product 200 should not throw away
        # the two hundred it had already paid to learn.
        self.save()
        return fetched

    def chain(self, category_id) -> list:
        """Root first, leaf last. Empty when the id cannot be resolved at all."""
        if not category_id:
            return []
        rows, seen = [], set()
        current = str(category_id)
        for _ in range(MAX_CLIMB):
            if current in seen:
                break
            seen.add(current)
            row = self._learn(current)
            if row is None:
                break
            rows.insert(0, row)
            parent = row.get("parent_id")
            if not parent or parent in ("0", "None"):
                break
            current = str(parent)
        return rows

    # -- the interface the pipeline uses ---------------------------------
    @staticmethod
    def _element(row: dict) -> dict:
        return {"id": row["id"], "name_original": row["name_zh"],
                "name_en": row.get("name_en") or row["name_zh"],
                "name_ar": row.get("name_ar") or row["name_zh"]}

    def resolve(self, category_id) -> tuple:
        rows = self.chain(category_id)
        if not rows:
            return None, None
        main = self._element(rows[0])
        sub = self._element(rows[-1]) if len(rows) > 1 else None
        return main, sub

    def state_of(self, category_id) -> str:
        """
        The whole chain decides, not the leaf.

        A prohibited department with an innocent-sounding leaf below it is
        exactly the case the client's ban list is for, and asking only about the
        leaf would let it through.
        """
        rows = self.chain(category_id)
        if not rows:
            return "unknown"
        for row in rows:
            if row.get("state") == catalog.BLOCKED:
                return catalog.BLOCKED
        for row in rows:
            if row.get("state") == catalog.REVIEW:
                return catalog.REVIEW
        return catalog.ALLOWED

    @property
    def by_id(self) -> dict:
        """
        Used by the pipeline only to name a category in a rejection message, so
        the learned rows have to be visible here too - otherwise a product
        rejected on a category we just learned is rejected with a blank name.
        """
        merged = dict(self.learned)
        merged.update(self.index.by_id)
        return merged

    def summary(self) -> dict:
        return {"gateway_calls": self.calls, "failures": self.failures,
                "learned": len(self.learned)}


def build(index, client=None, translate=None):
    """Off unless a client is available - no client means the old behaviour."""
    if client is None:
        return index
    return LiveIndex(index, client=client, translate=translate)

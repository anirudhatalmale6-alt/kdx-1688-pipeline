"""
Stage: category tree.

alibaba.category.get is one of the few 1688 APIs that answers without a user
token, so the whole category tree can be built before OAuth is in place.

categoryID=0 returns the roots; each node reports isLeaf and childCategorys, so
the tree is walked breadth-first with the daily point budget respected.
"""

from __future__ import annotations

import json

from aop_client import AopClient, ApiRoute

CATEGORY_ROUTE = ApiRoute(namespace="com.alibaba.product", api_name="alibaba.category.get")


def fetch_category(client: AopClient, category_id: int | str) -> dict:
    payload = client.call(CATEGORY_ROUTE, {"categoryID": str(category_id)}, authed=False)
    info = payload.get("categoryInfo") or []
    return info[0] if info else {}


def root_categories(client: AopClient) -> list:
    node = fetch_category(client, 0)
    return node.get("childCategorys", [])


def walk(client: AopClient, category_id: int | str, max_depth: int = 2,
         budget: list | None = None) -> dict:
    """
    Breadth-first walk. `budget` is a single-element list used as a mutable
    counter so the caller can cap how many API points this walk may spend.
    """
    if budget is not None and budget[0] <= 0:
        return {}
    node = fetch_category(client, category_id)
    if budget is not None:
        budget[0] -= 1
    if not node or node.get("isLeaf") or max_depth <= 0:
        return node

    children = []
    for child in node.get("childCategorys", []):
        if budget is not None and budget[0] <= 0:
            break
        children.append(walk(client, child["id"], max_depth - 1, budget))
    if children:
        node["children"] = children
    return node


def flatten(node: dict, path: tuple = ()) -> list:
    """Yield (id, name, full_path, is_leaf) for every node in a walked tree."""
    name = node.get("name", "")
    here = path + (name,) if name else path
    rows = [(node.get("categoryID") or node.get("id"), name, " > ".join(here),
             bool(node.get("isLeaf")))]
    for child in node.get("children", []):
        rows.extend(flatten(child, here))
    return rows


if __name__ == "__main__":
    import os
    import sys

    from aop_client import Credentials

    client = AopClient(Credentials(app_key=os.environ["ALI_APP_KEY"],
                                   app_secret=os.environ["ALI_APP_SECRET"]))
    roots = root_categories(client)
    print(json.dumps(roots, ensure_ascii=False, indent=2))
    print(f"\n{len(roots)} root categories", file=sys.stderr)

"""Tech Radar update run.

    python -m scraper.run                       # live run (what GitHub Actions does)
    python -m scraper.run --fixtures tests/fixtures --now 2026-11-02T08:00:00Z --out /tmp/feed.json

Self-maintenance rules:
  * every source is fetched independently; one broken feed never stops the run
  * a source that answers but has published nothing for `stale_after_days` is flagged "stale"
  * if no source can be reached at all, existing data is kept untouched
  * news older than `news_days` and products not seen for `products_days` are pruned
  * the same story arriving twice (same link or same headline) is stored once
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import yaml

from . import extract as ex
from .sources import fetch, normalise
from .trending import build_topics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, "scraper", "config.yaml")
DEFAULT_DATA = os.path.join(ROOT, "docs", "data", "feed.json")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    names = set()
    for src in cfg.get("sources", []):
        if not src.get("name") or not src.get("url"):
            raise SystemExit(f"config error: every source needs a name and url ({src})")
        if src["name"] in names:
            raise SystemExit(f"config error: duplicate source name {src['name']!r}")
        names.add(src["name"])
    return cfg


def load_previous(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", ex.fold_quotes(title).lower())[:140]


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
def detect_product(item: dict) -> dict | None:
    if item["kind"] == "community":
        return None
    title, link = item["title"], item["link"]
    if not ex.is_product_article(title, link):
        return None
    lifecycle, event = ex.detect_status(title)
    if not lifecycle:
        return None
    name = ex.extract_product_name(title)
    if not name:
        return None
    category = item["category"]
    if category == ex.DEFAULT_CATEGORY and name.brand in ex.BRAND_DEFAULT_CATEGORY:
        category = ex.BRAND_DEFAULT_CATEGORY[name.brand]
        item["category"] = category
    if not ex.is_hardware(category, title):
        return None
    price = ex.extract_price(item["_blocks"], name.core) if lifecycle in ("Launched", "On sale") else None
    return {"name": name, "lifecycle": lifecycle, "event": event, "category": category, "price": price}


def merge_product(products: dict, item: dict, found: dict) -> bool:
    """Merge one detected product mention. Returns True if the product is new."""
    pn = found["name"]
    rec = products.get(pn.key)
    is_new = rec is None
    if is_new:
        rec = {"id": pn.key, "name": pn.name, "brand": pn.brand, "category": found["category"],
               "status": found["lifecycle"], "reviewed": False, "deal_seen": None,
               "first_seen": item["published"], "last_seen": item["published"],
               "image": item.get("image", ""), "prices": [], "articles": []}
        products[pn.key] = rec
    if ex.LIFECYCLE_RANK[found["lifecycle"]] > ex.LIFECYCLE_RANK.get(rec["status"], -1):
        rec["status"] = found["lifecycle"]
    if found["event"] == "Review":
        rec["reviewed"] = True
    if found["event"] == "Deal":
        rec["deal_seen"] = max(rec.get("deal_seen") or "", item["published"])
    rec["first_seen"] = min(rec["first_seen"], item["published"])
    rec["last_seen"] = max(rec["last_seen"], item["published"])
    if not rec.get("image") and item.get("image"):
        rec["image"] = item["image"]
    if rec["category"] == ex.DEFAULT_CATEGORY:
        rec["category"] = found["category"]
    if all(a["link"] != item["link"] for a in rec["articles"]):
        rec["articles"].append({"title": item["title"], "link": item["link"], "outlet": item["outlet"],
                                "published": item["published"], "type": found["event"] or found["lifecycle"]})
        rec["articles"] = sorted(rec["articles"], key=lambda a: a["published"], reverse=True)[:12]
    price = found["price"]
    if price:
        kind = {"Deal": "deal", "Review": "review"}.get(found["event"] or "",
                                                       "sale" if found["lifecycle"] == "On sale" else "launch")
        same = [p for p in rec["prices"] if p["currency"] == price.currency]
        latest = max(same, key=lambda p: p["date"]) if same else None
        source_ref = {"outlet": item["outlet"], "link": item["link"], "date": item["published"]}
        if latest and latest["amount"] == price.amount:
            if all(c["outlet"] != item["outlet"] for c in [latest["source"], *latest["confirmed_by"]]):
                latest["confirmed_by"].append(source_ref)
        else:
            rec["prices"].append({"amount": price.amount, "currency": price.currency, "from": price.is_from,
                                  "was": price.previous, "kind": kind, "date": item["published"],
                                  "source": source_ref, "confirmed_by": [], "evidence": price.evidence})
            rec["prices"] = sorted(rec["prices"], key=lambda p: p["date"])[-30:]
    return is_new


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------
def source_status(src: dict, prev: dict, res, now: datetime, kept: int, newest: str | None,
                  cfg: dict, fixture_mode: bool) -> dict:
    """Health record for one feed. `newest` is the newest item the feed returned,
    counted before age filtering, so a feed that only serves old stories is seen as stale."""
    st = {k: prev.get(k) for k in ("last_ok", "newest_item", "etag", "last_modified", "items_last_run")}
    st.update({"name": src["name"], "url": src["url"], "kind": src.get("kind", "news"),
               "region": src.get("region", "global"), "last_checked": iso(now)})
    fails = prev.get("consecutive_failures", 0) or 0
    if fixture_mode and res.error == "no fixture":
        st.update({"status": "pending", "last_error": "", "consecutive_failures": 0, "last_checked": None})
        return st
    health = cfg.get("health", {})
    if res.ok:
        fails = 0
        st.update({"last_ok": iso(now), "last_error": "", "etag": res.etag or None,
                   "last_modified": res.last_modified or None})
        if not res.not_modified:
            st["items_last_run"] = kept
            if newest:
                st["newest_item"] = newest
    else:
        fails += 1
        st["last_error"] = res.error
    st["consecutive_failures"] = fails
    newest_seen = st.get("newest_item")
    if not res.ok:
        st["status"] = "failing" if fails >= health.get("failing_after_runs", 3) else "error"
    elif newest_seen and now - parse_iso(newest_seen) > timedelta(days=health.get("stale_after_days", 5)):
        st["status"] = "stale"
    else:
        st["status"] = "ok"
    return st


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Update Tech Radar data")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--data", default=DEFAULT_DATA, help="existing data file to merge with")
    ap.add_argument("--out", default=None, help="where to write (default: same as --data)")
    ap.add_argument("--fixtures", default=None, help="read feeds from <dir>/<source-slug>.xml instead of the web")
    ap.add_argument("--now", default=None, help="pretend the current time is this ISO timestamp")
    ap.add_argument("--preview", action="store_true", help="mark the output as a preview")
    ap.add_argument("--news-days", type=int, default=None, help="override retention.news_days")
    ap.add_argument("--note", default="", help="message shown at the top of the site")
    args = ap.parse_args(argv)

    started = time.time()
    cfg = load_config(args.config)
    now = parse_iso(args.now) if args.now else datetime.now(timezone.utc).replace(microsecond=0)
    out_path = args.out or args.data
    prev = load_previous(args.data)
    prev_sources = {s["name"]: s for s in prev.get("sources", [])}
    retention = cfg.get("retention", {})
    news_days = args.news_days or retention.get("news_days", 21)
    fixture_mode = args.fixtures is not None
    sources = [s for s in cfg["sources"] if s.get("enabled", True)]
    timeout = cfg.get("fetch", {}).get("timeout_seconds", 20)

    with ThreadPoolExecutor(max_workers=cfg.get("fetch", {}).get("parallel", 6)) as pool:
        futures = {s["name"]: pool.submit(fetch, s["url"], prev_sources.get(s["name"], {}), timeout,
                                          args.fixtures, slug(s["name"])) for s in sources}
        results = {name: fut.result() for name, fut in futures.items()}

    cutoff = now - timedelta(days=news_days)
    incoming, statuses = [], []
    # Direct sources first so their copy wins over an aggregator's copy of the same story.
    for src in sorted(sources, key=lambda s: bool(s.get("aggregator"))):
        res = results[src["name"]]
        items, newest = [], None
        if res.ok and not res.not_modified:
            for entry in res.entries[: src.get("max_items", 60)]:
                item = normalise(entry, src, now)
                if not item:
                    continue
                newest = max(newest or item["published"], item["published"])
                if ex.is_sponsored(item["title"], item["link"]) or parse_iso(item["published"]) < cutoff:
                    continue
                items.append(item)
        statuses.append(source_status(src, prev_sources.get(src["name"], {}), res, now, len(items), newest,
                                      cfg, fixture_mode))
        incoming.extend(items)

    reachable = [r for r in results.values() if r.ok]
    print(f"{len(reachable)}/{len(results)} sources reachable, {len(incoming)} items fetched")
    if not reachable and not fixture_mode:
        print("No source could be reached. Keeping existing data unchanged.")
        prev.setdefault("meta", {})["last_failed_run"] = iso(now)
        prev["sources"] = statuses
        _write(out_path, prev)
        return 0

    # ---- merge news -------------------------------------------------------
    old_news = [n for n in prev.get("news", []) if parse_iso(n["published"]) >= cutoff]
    by_id = {n["id"]: n for n in old_news}
    seen_titles = {title_key(n["title"]) for n in old_news}
    products = {p["id"]: p for p in prev.get("products", [])}
    fresh, new_products = [], 0
    for item in incoming:
        if item["id"] in by_id:
            old = by_id[item["id"]]
            if item["kind"] == "community":  # points and comments keep moving
                old["points"], old["comments"] = item.get("points", 0), item.get("comments", 0)
            continue
        tk = title_key(item["title"])
        if tk in seen_titles:
            continue
        seen_titles.add(tk)
        item["category"] = ex.classify(item["title"], item["summary"], item["link"]) \
            if item["kind"] != "community" else ex.classify(item["title"])
        found = detect_product(item)
        if found:
            item["product_id"] = found["name"].key
            new_products += merge_product(products, item, found)
        fresh.append(item)
        by_id[item["id"]] = item

    news = sorted(by_id.values(), key=lambda n: n["published"], reverse=True)
    news = news[: retention.get("max_news_items", 1200)]
    for n in news:
        n.pop("_blocks", None)

    product_cutoff = iso(now - timedelta(days=retention.get("products_days", 120)))
    product_list = sorted((p for p in products.values() if p["last_seen"] >= product_cutoff),
                          key=lambda p: p["last_seen"], reverse=True)[: retention.get("max_products", 600)]

    trending_cfg = cfg.get("trending", {})
    topics = build_topics([n for n in news if n["kind"] != "community"], now,
                          window_hours=trending_cfg.get("window_hours", 72),
                          min_outlets=trending_cfg.get("min_outlets", 2))

    site = cfg.get("site", {})
    region = site.get("region", "IN")
    data = {
        "meta": {
            "title": site.get("title", "Tech Radar"),
            "generated_at": iso(now),
            "region": region,
            "currency": cfg.get("currency_by_region", {}).get(region, "INR"),
            "stores": cfg.get("stores", {}).get(region, []),
            "amazon_tag": (cfg.get("affiliate") or {}).get("amazon_tag", ""),
            "show_images": site.get("show_images", True),
            "update_every_hours": site.get("update_every_hours", 4),
            "trending_window_hours": trending_cfg.get("window_hours", 72),
            "min_outlets": trending_cfg.get("min_outlets", 2),
            "news_days": news_days,
            "products_days": retention.get("products_days", 120),
            "note": args.note,
            "last_failed_run": (prev.get("meta") or {}).get("last_failed_run"),
            "preview": bool(args.preview),
            "counts": {"news": sum(1 for n in news if n["kind"] != "community"),
                       "community": sum(1 for n in news if n["kind"] == "community"),
                       "products": len(product_list), "topics": len(topics),
                       "new_items": len(fresh), "new_products": new_products},
            "sources_ok": sum(1 for s in statuses if s["status"] == "ok"),
            "sources_total": len(statuses),
            "run_seconds": round(time.time() - started, 1),
        },
        "topics": topics,
        "products": product_list,
        "news": news,
        "sources": statuses,
    }
    _write(out_path, data)
    print(f"{len(fresh)} new items, {new_products} new products, {len(topics)} big stories, "
          f"{data['meta']['sources_ok']}/{len(statuses)} sources healthy -> {out_path}")
    for st in statuses:
        if st["status"] not in ("ok", "pending"):
            print(f"  ! {st['name']}: {st['status']} {st.get('last_error') or ''}".rstrip())
    return 0


def _write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())

"""Group headlines about the same event and rank them by independent coverage.

A story counts as big when several *different outlets* cover it within the
window. Words are weighted by rarity (IDF), so "Binance" or "X500" link
headlines together while "launch", "price" or "India" do not.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta

from .extract import fold_quotes

STOP = set("""
a an the and or but of to in on for with at by from as is are was were be been being it its this that these
those new now after before over under into out up down about more most than then here there what why how who
when where which will can could may might should would has have had get gets got set says said say report
reports reportedly allegedly just first last next year years week weeks today day days month months india
indian global price prices priced launch launched launches launching specifications specs feature features
review reviews hands best top vs rs inr usd update updates available availability sale sales offer offers
deal deals official officially announce announces announced unveil unveils unveiled tipped leak leaked leaks
spotted tease teased expected rumour rumor come comes brings adds user users via know everything need thing
things check full list heres you your our we they their them his her one two three four five not no yes all
any some also still only even much many big biggest largest record makes make made goes go going way time
times amid against off per like look looks looking help helps could while during until since across around
gets getting got sees seen show shows shown take takes taking around reveal reveals revealed launching soon
cheaper better faster upgrade upgrades support supports supported rolls rolling roll out start starts started
plans plan said company companies brand brands model models device devices series lineup variant variants
""".split())
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")


def tokens(text: str, limit: int | None = None) -> list[str]:
    out = []
    for w in _TOKEN_RE.findall(fold_quotes(text).lower()):
        w = re.sub(r"'s$", "", w).strip("'.")
        w = re.sub(r"^(\d+(?:\.\d+)?)(?:bn|b|billion)$", r"\1", w)
        if len(w) < 2 or w in STOP or re.fullmatch(r"20\d\d", w):
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if len(w) > 5 and w.endswith("ed"):
            w = w[:-2]
        if w in STOP:
            continue
        out.append(w)
        if limit and len(out) >= limit:
            break
    return out


def _vector(item: dict) -> dict[str, float]:
    vec: dict[str, float] = {}
    for t in tokens(item.get("summary", ""), limit=30):
        vec[t] = 1.0
    for t in tokens(item["title"]):
        vec[t] = 2.0
    return vec


def _norm_outlet(name: str) -> str:
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    return {"ndtvgadgets360": "gadgets360", "gadgets360com": "gadgets360"}.get(n, n)


def build_topics(news: list[dict], now: datetime, window_hours: int = 72, min_outlets: int = 2,
                 threshold: float = 0.34, limit: int = 25) -> list[dict]:
    since = now - timedelta(hours=window_hours)
    pool = [n for n in news if datetime.fromisoformat(n["published"].replace("Z", "+00:00")) >= since]
    pool.sort(key=lambda n: n["published"])
    if len(pool) < 2:
        return []
    vectors = [_vector(n) for n in pool]
    df = Counter(t for v in vectors for t in v)
    total = len(pool)
    idf = {t: math.log((total + 1) / (c + 1)) + 1 for t, c in df.items()}

    def weight(v: dict[str, float]) -> float:
        return sum(w * idf[t] for t, w in v.items()) or 1.0

    clusters: list[dict] = []
    for item, vec in zip(pool, vectors):
        title_tokens = {t for t, w in vec.items() if w >= 2}
        best, best_sim = None, 0.0
        for cl in clusters:
            shared = set(vec) & set(cl["vec"])
            if not shared & (title_tokens | cl["title_tokens"]):
                continue
            strong = [t for t in shared if df[t] < total * 0.25]
            if len(strong) < 2 and not any(re.search(r"[a-z]\d|\d[a-z]", t) for t in strong):
                continue
            num = sum(min(vec[t], cl["vec"][t]) * idf[t] for t in shared)
            sim = num / min(weight(vec), weight(cl["vec"]))
            if sim > best_sim:
                best, best_sim = cl, sim
        if best is not None and best_sim >= threshold:
            best["items"].append(item)
            if len(best["items"]) <= 4:  # keep the profile anchored to the first reports
                for t, w in vec.items():
                    best["vec"][t] = max(best["vec"].get(t, 0), w)
                best["title_tokens"] |= title_tokens
        else:
            clusters.append({"items": [item], "vec": dict(vec), "title_tokens": set(title_tokens)})

    topics = []
    for cl in clusters:
        outlets = {}
        for it in cl["items"]:
            outlets.setdefault(_norm_outlet(it["outlet"]), it["outlet"])
        if len(outlets) < min_outlets:
            continue
        items = sorted(cl["items"], key=lambda n: n["published"], reverse=True)
        newest = datetime.fromisoformat(items[0]["published"].replace("Z", "+00:00"))
        hours_old = (now - newest).total_seconds() / 3600
        points = sum(it.get("points", 0) for it in items)
        score = len(outlets) * 10 + min(points, 1500) / 50 + (6 if hours_old < 12 else 3 if hours_old < 24 else 0)
        # Label: the headline sharing the most vocabulary with the others.
        label_item = max(items, key=lambda it: sum(1 for t in _vector(it) if t in cl["vec"] and df[t] > 1))
        cats = Counter(it.get("category", "Tech News") for it in items)
        topics.append({
            "id": label_item["id"],
            "label": label_item["title"],
            "category": cats.most_common(1)[0][0],
            "outlets": sorted(outlets.values(), key=str.lower),
            "score": round(score, 1),
            "first_seen": items[-1]["published"],
            "last_seen": items[0]["published"],
            "items": [{"id": it["id"], "title": it["title"], "link": it["link"], "outlet": it["outlet"],
                       "published": it["published"]} for it in items[:12]],
        })
    topics.sort(key=lambda t: t["score"], reverse=True)
    return topics[:limit]

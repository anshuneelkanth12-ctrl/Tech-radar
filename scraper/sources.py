"""Fetch and parse RSS/Atom feeds into plain dict items.

* Polite: one request per feed per run, conditional GET (ETag / Last-Modified),
  an honest User-Agent, short timeouts and a single retry.
* RSS and Atom are parsed with the standard library so that every field keeps
  its meaning (feedparser merges media:description into the summary, which would
  mix photo captions with article text). feedparser is the fallback for
  malformed feeds.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .extract import clean_title, strip_html

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
}
_REPO = os.environ.get("GITHUB_REPOSITORY", "personal-tech-radar")
USER_AGENT = (f"TechRadarBot/1.0 (+https://github.com/{_REPO}; personal research aggregator; "
              "fetches each feed once every few hours)")


@dataclass
class FetchResult:
    ok: bool
    not_modified: bool = False
    entries: list[dict] = field(default_factory=list)
    error: str = ""
    etag: str = ""
    last_modified: str = ""
    http_status: int = 0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def fetch(url: str, state: dict, timeout: int = 20, fixture_dir: str | None = None,
          fixture_name: str | None = None) -> FetchResult:
    if fixture_dir is not None:
        path = os.path.join(fixture_dir, f"{fixture_name}.xml")
        if not os.path.exists(path):
            return FetchResult(ok=False, error="no fixture")
        with open(path, "rb") as fh:
            return _parse_result(fh.read(), 200, "", "")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    }
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    last_error = ""
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if r.status_code == 304:
                return FetchResult(ok=True, not_modified=True, http_status=304,
                                   etag=state.get("etag", ""), last_modified=state.get("last_modified", ""))
            if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                last_error = f"HTTP {r.status_code}"
                time.sleep(4)
                continue
            if r.status_code >= 400:
                return FetchResult(ok=False, error=f"HTTP {r.status_code}", http_status=r.status_code)
            return _parse_result(r.content, r.status_code, r.headers.get("ETag", ""),
                                 r.headers.get("Last-Modified", ""))
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            if attempt == 0:
                time.sleep(3)
    return FetchResult(ok=False, error=last_error or "request failed")


def _parse_result(body: bytes, status: int, etag: str, last_modified: str) -> FetchResult:
    try:
        entries = parse_feed(body)
    except Exception as exc:  # noqa: BLE001 - a broken feed must never stop the run
        return FetchResult(ok=False, error=f"unreadable feed ({type(exc).__name__})", http_status=status)
    if not entries:
        return FetchResult(ok=False, error="feed had no items", http_status=status)
    return FetchResult(ok=True, entries=entries, etag=etag, last_modified=last_modified, http_status=status)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _fix_entities(body: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in _XML_ENTITIES:
            return m.group(0)
        char = html.unescape(m.group(0))
        return f"&#{ord(char[0])};" if char != m.group(0) and len(char) == 1 else ""
    return re.sub(r"&([A-Za-z][A-Za-z0-9]*);", repl, body)


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_feed(body: bytes) -> list[dict]:
    declared = re.match(rb"<\?xml[^>]*encoding=[\"']([A-Za-z0-9_.:-]+)", body.lstrip()[:200])
    encoding = declared.group(1).decode("ascii") if declared else "utf-8"
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text.lstrip("\ufeff \n\r\t"))
    try:
        root = ET.fromstring(_fix_entities(text))
    except ET.ParseError:
        return _parse_with_feedparser(body)
    tag = root.tag.split("}")[-1].lower()
    if tag == "feed":
        return _parse_atom(root)
    channel = root.find("channel") if tag == "rss" else root
    items = channel.findall("item") if channel is not None else []
    if not items:  # RSS 1.0 (RDF)
        items = [el for el in root.iter() if el.tag.split("}")[-1] == "item"]
    return [_parse_rss_item(it) for it in items]


def _parse_rss_item(it: ET.Element) -> dict:
    media_desc, image = "", ""
    for mc in it.findall("media:content", NS) + it.findall("media:group/media:content", NS):
        url, typ, medium = mc.get("url", ""), mc.get("type", ""), mc.get("medium", "")
        if url and not image and (typ.startswith("image") or medium == "image" or re.search(r"\.(jpe?g|png|webp)", url, re.I)):
            image = url
        media_desc = media_desc or _text(mc.find("media:description", NS))
    if not image:
        th = it.find("media:thumbnail", NS)
        if th is not None:
            image = th.get("url", "")
    if not image:
        enc = it.find("enclosure")
        if enc is not None and enc.get("type", "").startswith("image"):
            image = enc.get("url", "")
    media_desc = media_desc or _text(it.find("media:description", NS))
    src = it.find("source")
    link = _text(it.find("link"))
    guid = _text(it.find("guid"))
    if not link and guid.startswith("http"):
        link = guid
    return {
        "title": _text(it.find("title")),
        "link": link,
        "guid": guid,
        "published": _date(_text(it.find("pubDate")) or _text(it.find("dc:date", NS))),
        "description": _text(it.find("description")),
        "content": _text(it.find("content:encoded", NS)),
        "media_description": media_desc,
        "image": image,
        "publisher": _text(src) if src is not None else "",
        "comments": _text(it.find("comments")),
    }


def _parse_atom(root: ET.Element) -> list[dict]:
    out = []
    for e in root.findall("atom:entry", NS):
        link = ""
        for l in e.findall("atom:link", NS):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href", "")
                break
        thumb = e.find("media:group/media:thumbnail", NS)
        out.append({
            "title": _text(e.find("atom:title", NS)),
            "link": link,
            "guid": _text(e.find("atom:id", NS)),
            "published": _date(_text(e.find("atom:published", NS)) or _text(e.find("atom:updated", NS))),
            "description": _text(e.find("atom:summary", NS)) or _text(e.find("media:group/media:description", NS)),
            "content": _text(e.find("atom:content", NS)),
            "media_description": "",
            "image": thumb.get("url", "") if thumb is not None else "",
            "publisher": "",
            "comments": "",
        })
    return out


def _parse_with_feedparser(body: bytes) -> list[dict]:
    import feedparser  # imported lazily: only needed for malformed feeds

    parsed = feedparser.parse(body)
    out = []
    for e in parsed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        contents = [c.get("value", "") for c in e.get("content", [])]
        image = ""
        for mc in e.get("media_content", []) + e.get("media_thumbnail", []):
            if mc.get("url"):
                image = mc["url"]
                break
        src = e.get("source")
        out.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "guid": e.get("id", ""),
            "published": datetime(*t[:6], tzinfo=timezone.utc) if t else None,
            "description": contents[0] if contents else e.get("summary", ""),
            "content": contents[-1] if len(contents) > 1 else "",
            "media_description": "",
            "image": image,
            "publisher": src.get("title", "") if isinstance(src, dict) else "",
            "comments": e.get("comments", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Normalising
# ---------------------------------------------------------------------------
_TRACKING = re.compile(r"^(?:utm_|fbclid|gclid|mc_|ref$|ref_|cmpid|ncid|taid|sr_share)", re.I)


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)])
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, ""))


def item_id(url: str, title: str) -> str:
    return hashlib.sha1((canonical_url(url) or title).encode("utf-8")).hexdigest()[:12]


def _shorten(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "\u2026"


_HN_POINTS = re.compile(r"Points:\s*(\d+)", re.I)
_HN_COMMENTS = re.compile(r"#\s*Comments:\s*(\d+)", re.I)


def normalise(entry: dict, source: dict, now: datetime) -> dict | None:
    title = clean_title(entry.get("title"))
    link = (entry.get("link") or "").strip()
    if not title or not link.startswith("http"):
        return None
    outlet = source["name"]
    publisher = entry.get("publisher", "").strip()
    if source.get("aggregator") and publisher:
        outlet = publisher
        suffix = f" - {publisher}"
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    published = entry.get("published") or now
    if published > now:  # feeds with wrong timezones
        published = now
    description = strip_html(entry.get("description"))
    content = strip_html(entry.get("content"))
    media = strip_html(entry.get("media_description"))
    summary_src = description or content
    if source.get("kind") == "community" or re.match(r"^Article URL:", summary_src or ""):
        summary_src = ""
    summary = _shorten(summary_src, source.get("summary_chars", 260))
    if summary and summary.lower().startswith(title.lower()[:40]):
        summary = ""
    item = {
        "id": item_id(link, title),
        "title": title,
        "link": canonical_url(link),
        "source": source["name"],
        "outlet": outlet,
        "region": source.get("region", "global"),
        "kind": source.get("kind", "news"),
        "published": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "image": entry.get("image", "") if entry.get("image", "").startswith("https://") else "",
    }
    if source.get("kind") == "community":
        raw = entry.get("description") or ""
        p, c = _HN_POINTS.search(raw), _HN_COMMENTS.search(raw)
        item["points"] = int(p.group(1)) if p else 0
        item["comments"] = int(c.group(1)) if c else 0
        item["discussion"] = entry.get("comments", "")
    # Text used for extraction only; never stored or published.
    item["_blocks"] = [("title", title), ("media", media), ("description", description),
                       ("content", content[:5000])]
    return item

"""RSSHub/newsnow supplemental news fetcher.

This module augments the existing yfinance news flow with a curated set of
RSSHub feeds. It keeps the output compact and source-linked so the News
Analyst can cite concrete articles instead of relying on opaque summaries.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests


DEFAULT_RSSHUB_BASE_URL = "https://rss.cnnewsnow.com"


@dataclass(frozen=True)
class RssFeed:
    name: str
    route: str
    category: str
    keywords: tuple[str, ...] = ()


RSSHUB_FEEDS: tuple[RssFeed, ...] = (
    RssFeed("Bloomberg Markets", "/bloomberg/markets", "Markets and macro"),
    RssFeed("Tencent Finance", "/qq/finance", "Chinese finance"),
    RssFeed("WallstreetCN AI", "/wallstreetcn/ai", "AI and technology"),
    RssFeed("TechCrunch Latest", "/techcrunch/latest", "AI and technology"),
    RssFeed("MIT Technology Review", "/technologyreview", "AI and technology"),
    RssFeed("FastBull Recommend", "/fastbull/recommend", "Fast market alerts"),
    RssFeed("FastBull Central Banks", "/fastbull/center_bank", "Rates and central banks"),
    RssFeed("FastBull Stocks", "/fastbull/stock", "Equities"),
    RssFeed("Al Jazeera Middle East", "/aljazeera/middle-east", "Geopolitics"),
    RssFeed("Foreign Policy", "/foreignpolicy", "Geopolitics and policy"),
    # Second-wave sources that are useful but a bit broader/noisier.
    RssFeed("The Diplomat", "/thediplomat", "Asia policy"),
    RssFeed("Xinhua World", "/xinhua/world", "China official/global"),
    RssFeed("Tencent World", "/qq/world", "Chinese global news"),
    RssFeed("Sina World", "/sina/world", "Chinese global news"),
    RssFeed("FastBull Trump", "/fastbull/trump", "US policy"),
    RssFeed("FastBull Russia Ukraine", "/fastbull/russia_ukraine", "Geopolitics"),
)


COMPANY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "NVDA": (
        "nvda", "nvidia", "英伟达", "gpu", "cuda", "blackwell", "rubin",
        "gb200", "h100", "h200", "ai chip", "ai accelerator", "算力",
        "芯片", "半导体", "数据中心",
    ),
    "AAPL": ("aapl", "apple", "苹果", "iphone", "app store", "services", "ios"),
    "MSFT": ("msft", "microsoft", "微软", "azure", "copilot", "windows"),
    "GOOG": ("goog", "googl", "alphabet", "google", "谷歌", "gemini", "search"),
    "GOOGL": ("goog", "googl", "alphabet", "google", "谷歌", "gemini", "search"),
    "AMZN": ("amzn", "amazon", "亚马逊", "aws", "prime", "e-commerce"),
    "META": ("meta", "facebook", "instagram", "whatsapp", "metaverse", "threads"),
    "TSLA": ("tsla", "tesla", "特斯拉", "ev", "electric vehicle", "robotaxi"),
    "AMD": ("amd", "advanced micro devices", "mi300", "mi350", "gpu", "cpu"),
    "INTC": ("intc", "intel", "英特尔", "foundry", "cpu", "semiconductor"),
    "AVGO": ("avgo", "broadcom", "博通", "vmware", "semiconductor"),
}


MACRO_KEYWORDS: tuple[str, ...] = (
    "fed", "federal reserve", "interest rate", "rates", "inflation",
    "treasury", "bond yield", "dollar", "earnings season", "tariff",
    "export control", "sanction", "oil", "middle east", "china", "taiwan",
    "美联储", "利率", "通胀", "美债", "美元", "财报季", "关税",
    "出口管制", "制裁", "油价", "中东", "中国", "台湾",
)


def _base_url() -> str:
    return os.getenv("RSSHUB_BASE_URL", DEFAULT_RSSHUB_BASE_URL).rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "TradingAgents RSSHub fetcher (+https://github.com/maxlixiang/TradingAgents)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    }


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _child_text(element: ElementTree.Element, names: Iterable[str]) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
    for child in element:
        tag = child.tag.split("}", 1)[-1].lower()
        if tag in {n.lower().split("}")[-1] for n in names} and child.text:
            return child.text.strip()
    return ""


def _entry_link(element: ElementTree.Element) -> str:
    link = _child_text(element, ("link",))
    if link:
        return link
    for child in element:
        tag = child.tag.split("}", 1)[-1].lower()
        if tag == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return ""


def _parse_feed(xml_text: str, source_url: str) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml_text)
    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    items = []
    for entry in entries:
        title = _strip_html(_child_text(entry, ("title", "{http://www.w3.org/2005/Atom}title")))
        summary = _strip_html(_child_text(entry, ("description", "summary", "content", "{http://www.w3.org/2005/Atom}summary")))
        link = _entry_link(entry)
        pub_date = (
            _child_text(entry, ("pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"))
        )
        items.append({
            "title": title or "Untitled",
            "summary": summary,
            "link": urljoin(source_url, link) if link else source_url,
            "published": pub_date,
        })
    return items


def _keywords_for_ticker(ticker: str) -> tuple[str, ...]:
    ticker_upper = ticker.upper()
    return tuple(dict.fromkeys((
        ticker_upper.lower(),
        f"${ticker_upper.lower()}",
        *COMPANY_KEYWORDS.get(ticker_upper, ()),
    )))


def _score_item(text: str, company_keywords: tuple[str, ...]) -> tuple[int, list[str]]:
    lowered = text.lower()
    matches = []
    score = 0
    for keyword in company_keywords:
        if keyword and keyword.lower() in lowered:
            matches.append(keyword)
            score += 4
    for keyword in MACRO_KEYWORDS:
        if keyword.lower() in lowered:
            matches.append(keyword)
            score += 1
    return score, matches[:8]


def get_rsshub_news(ticker: str, curr_date: str, look_back_days: int = 7, limit: int = 30) -> str:
    """Fetch and rank curated RSSHub/newsnow items for the News Analyst."""
    try:
        current_dt = datetime.strptime(curr_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        current_dt = datetime.now(timezone.utc)
    start_dt = current_dt - timedelta(days=look_back_days)
    company_keywords = _keywords_for_ticker(ticker)

    fetched: list[dict[str, object]] = []
    errors: list[str] = []
    seen_links: set[str] = set()

    for feed in RSSHUB_FEEDS:
        url = f"{_base_url()}{feed.route}"
        try:
            response = requests.get(url, headers=_headers(), timeout=12)
            response.raise_for_status()
            entries = _parse_feed(response.text, url)
        except Exception as exc:
            errors.append(f"{feed.name}: {type(exc).__name__}: {exc}")
            continue

        for entry in entries[:20]:
            link = str(entry.get("link") or url)
            if link in seen_links:
                continue
            published = _parse_date(str(entry.get("published") or ""))
            if published and not (start_dt <= published <= current_dt + timedelta(days=1)):
                continue
            title = str(entry.get("title") or "")
            summary = str(entry.get("summary") or "")
            score, matches = _score_item(f"{title} {summary}", company_keywords)
            if feed.category in {"Markets and macro", "Rates and central banks", "Fast market alerts", "Geopolitics", "Geopolitics and policy"}:
                score += 1
            if score <= 0:
                continue
            seen_links.add(link)
            fetched.append({
                "source": feed.name,
                "category": feed.category,
                "title": title,
                "summary": summary[:360],
                "link": link,
                "published": published.strftime("%Y-%m-%d %H:%M UTC") if published else "unknown",
                "score": score,
                "matches": matches,
            })

    fetched.sort(key=lambda item: (int(item["score"]), str(item["published"])), reverse=True)
    selected = fetched[:limit]

    header = [
        f"## RSSHub News Supplement for {ticker.upper()}",
        f"Window: {start_dt.strftime('%Y-%m-%d')} to {current_dt.strftime('%Y-%m-%d')}",
        f"Base URL: {_base_url()}",
        f"Fetched feeds: {len(RSSHUB_FEEDS)}; selected items: {len(selected)}",
    ]
    if not selected:
        if errors:
            return "\n".join(header + ["\nNo relevant RSSHub items selected.", "\nFetch errors:", *[f"- {e}" for e in errors[:8]]])
        return "\n".join(header + ["\nNo relevant RSSHub items selected."])

    by_category: dict[str, list[dict[str, object]]] = {}
    for item in selected:
        by_category.setdefault(str(item["category"]), []).append(item)

    lines = header
    for category, items in by_category.items():
        lines.append(f"\n### {category}")
        for item in items:
            matches = ", ".join(str(m) for m in item["matches"]) or "macro/context"
            lines.append(
                f"- [{item['source']}] {item['title']}\n"
                f"  Published: {item['published']} | Score: {item['score']} | Matches: {matches}\n"
                f"  Link: {item['link']}"
            )
            if item["summary"]:
                lines.append(f"  Summary: {item['summary']}")
    if errors:
        lines.append("\n### Fetch errors")
        lines.extend(f"- {error}" for error in errors[:8])
    return "\n".join(lines)

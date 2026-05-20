"""Supplemental fundamentals data sources.

These helpers intentionally augment, rather than replace, the existing
yfinance fundamentals tools. They return compact Markdown blocks suitable
for prompt injection and degrade to explanatory placeholders on failures.
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

import requests

from .alpha_vantage_common import API_BASE_URL, get_api_key


_SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_SEC_COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_DEFAULT_IR_KEYWORDS = (
    "financial results",
    "quarter",
    "earnings",
    "revenue",
    "presentation",
    "webcast",
    "annual report",
    ".pdf",
)

_COMPANY_IR_SOURCES: dict[str, dict[str, Any]] = {
    "NVDA": {
        "name": "NVIDIA Investor Relations",
        "pages": {
            "Quarterly results and presentations": "https://investor.nvidia.com/financial-info/quarterly-results/default.aspx",
            "Official financial-results releases": "https://nvidianews.nvidia.com/news?q=financial%20results",
            "Investor presentations and earnings events": "https://investor.nvidia.com/events-and-presentations/events-and-presentations/default.aspx",
            "Presentation library": "https://investor.nvidia.com/events-and-presentations/presentations/default.aspx",
            "Press releases": "https://investor.nvidia.com/news/press-releases/default.aspx",
        },
        "rss_page": "https://investor.nvidia.com/investor-resources/rss/default.aspx",
        "keywords": _DEFAULT_IR_KEYWORDS + ("nvidia announces", "financial-results"),
    },
    "AAPL": {
        "name": "Apple Investor Relations",
        "pages": {
            "Financial reports": "https://investor.apple.com/investor-relations/default.aspx",
            "SEC filings": "https://investor.apple.com/sec-filings/default.aspx",
            "Events and presentations": "https://investor.apple.com/events/default.aspx",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("results",),
    },
    "MSFT": {
        "name": "Microsoft Investor Relations",
        "pages": {
            "Earnings releases": "https://www.microsoft.com/en-us/Investor/earnings",
            "SEC filings": "https://www.microsoft.com/en-us/Investor/sec-filings",
            "Events and presentations": "https://www.microsoft.com/en-us/Investor/events",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("results",),
    },
    "GOOG": {
        "name": "Alphabet Investor Relations",
        "pages": {
            "Earnings": "https://abc.xyz/investor/",
            "SEC filings": "https://abc.xyz/investor/sec-filings/",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("alphabet", "google"),
    },
    "GOOGL": {
        "alias": "GOOG",
    },
    "AMZN": {
        "name": "Amazon Investor Relations",
        "pages": {
            "Quarterly results": "https://ir.aboutamazon.com/quarterly-results/default.aspx",
            "SEC filings": "https://ir.aboutamazon.com/sec-filings/default.aspx",
            "Events and presentations": "https://ir.aboutamazon.com/events-and-presentations/default.aspx",
            "News releases": "https://ir.aboutamazon.com/news-release/default.aspx",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("amazon.com announces",),
    },
    "META": {
        "name": "Meta Investor Relations",
        "pages": {
            "Financials": "https://investor.atmeta.com/financials/default.aspx",
            "SEC filings": "https://investor.atmeta.com/sec-filings/default.aspx",
            "Events": "https://investor.atmeta.com/events-and-presentations/default.aspx",
            "Press releases": "https://investor.atmeta.com/news/default.aspx",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("meta reports",),
    },
    "TSLA": {
        "name": "Tesla Investor Relations",
        "pages": {
            "Quarterly disclosure": "https://ir.tesla.com/#quarterly-disclosure",
            "SEC filings": "https://ir.tesla.com/sec-filings",
            "Events and presentations": "https://ir.tesla.com/#events-and-presentations",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("update", "deck"),
    },
    "AMD": {
        "name": "AMD Investor Relations",
        "pages": {
            "Financial information": "https://ir.amd.com/financial-information",
            "SEC filings": "https://ir.amd.com/sec-filings",
            "Events and presentations": "https://ir.amd.com/news-events/ir-calendar",
            "News releases": "https://ir.amd.com/news-events/press-releases",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("amd reports",),
    },
    "INTC": {
        "name": "Intel Investor Relations",
        "pages": {
            "Results and filings": "https://www.intc.com/financial-info/financial-results",
            "SEC filings": "https://www.intc.com/financial-info/sec-filings",
            "Events and presentations": "https://www.intc.com/news-events/ir-calendar",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("intel reports",),
    },
    "AVGO": {
        "name": "Broadcom Investor Relations",
        "pages": {
            "Financial information": "https://investors.broadcom.com/financial-information",
            "SEC filings": "https://investors.broadcom.com/sec-filings",
            "News releases": "https://investors.broadcom.com/news-releases",
        },
        "keywords": _DEFAULT_IR_KEYWORDS + ("broadcom announces",),
    },
}


def _sec_user_agent() -> str:
    return os.getenv(
        "SEC_USER_AGENT",
        "TradingAgents research tool contact@example.com",
    )


def _headers() -> dict[str, str]:
    return {
        "User-Agent": _sec_user_agent(),
        "Accept-Encoding": "gzip, deflate",
    }


def _get_json(url: str, *, params: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    response = requests.get(url, params=params, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    return response.json()


def _get_text(url: str, *, timeout: float = 15.0) -> str:
    response = requests.get(url, headers=_headers(), timeout=timeout)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def _strip_html(value: str, limit: int = 2200) -> str:
    text = re.sub(
        r"(?is)<script.*?</script>|<style.*?</style>|<ix:header.*?</ix:header>|<ix:hidden.*?</ix:hidden>",
        " ",
        value,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _sec_document_excerpt(value: str, form: str, limit: int = 1800) -> str:
    text = _strip_html(value, limit=200000)
    if form in {"10-K", "10-Q"}:
        patterns = [
            r"\bItem\s+1\.\s+Business\b",
            r"\bItem\s+2\.\s+Management.?s Discussion",
            r"\bManagement.?s Discussion and Analysis\b",
            r"\bRisk Factors\b",
        ]
    else:
        patterns = [
            r"\bItem\s+2\.02\b",
            r"\bResults of Operations and Financial Condition\b",
            r"\bItem\s+7\.01\b",
            r"\bRegulation FD Disclosure\b",
        ]
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
        if matches:
            match = matches[1] if len(matches) > 1 else matches[0]
            excerpt = text[match.start(): match.start() + limit]
            return excerpt + ("..." if len(text) > match.start() + limit else "")
    return text[:limit] + ("..." if len(text) > limit else "")


def _compact_dict(data: dict[str, Any], keys: list[str]) -> str:
    lines = []
    for key in keys:
        value = data.get(key)
        if value not in (None, "", "None"):
            lines.append(f"- {key}: {value}")
    return "\n".join(lines) if lines else "- No selected fields available."


def _latest_reports(data: dict[str, Any], key: str, count: int = 2) -> list[dict[str, Any]]:
    reports = data.get(key)
    if not isinstance(reports, list):
        return []
    return reports[:count]


def get_alpha_vantage_fundamentals_summary(ticker: str, curr_date: str | None = None) -> str:
    """Return compact Alpha Vantage overview, statements, and earnings data."""
    try:
        api_key = get_api_key()
    except Exception as exc:
        return f"## Alpha Vantage Fundamentals\n<unavailable: {exc}>"

    def call(function: str) -> dict[str, Any]:
        try:
            payload = _get_json(
                API_BASE_URL,
                params={
                    "function": function,
                    "symbol": ticker.upper(),
                    "apikey": api_key,
                    "source": "trading_agents",
                },
            )
        except Exception as exc:
            return {"_error": f"{type(exc).__name__}: {exc}"}
        if isinstance(payload, dict) and ("Information" in payload or "Note" in payload):
            message = payload.get("Information") or payload.get("Note")
            return {"_error": message}
        return payload if isinstance(payload, dict) else {"_error": "Non-JSON response"}

    sections = [f"## Alpha Vantage Fundamentals Supplement for {ticker.upper()}"]
    if curr_date:
        sections.append(f"Simulation date: {curr_date}")

    overview = call("OVERVIEW")
    if overview.get("_error"):
        sections.append(f"\n### Company Overview\n<unavailable: {overview['_error']}>")
    else:
        overview_keys = [
            "Name", "Sector", "Industry", "MarketCapitalization", "PERatio",
            "ForwardPE", "PEGRatio", "PriceToBookRatio", "EVToRevenue",
            "EVToEBITDA", "EPS", "RevenueTTM", "GrossProfitTTM",
            "ProfitMargin", "OperatingMarginTTM", "ReturnOnEquityTTM",
            "QuarterlyRevenueGrowthYOY", "QuarterlyEarningsGrowthYOY",
            "AnalystTargetPrice", "DividendYield", "Beta",
        ]
        sections.append("\n### Company Overview\n" + _compact_dict(overview, overview_keys))

    statement_specs = [
        ("INCOME_STATEMENT", "quarterlyReports", "Income Statement", [
            "fiscalDateEnding", "reportedCurrency", "totalRevenue", "grossProfit",
            "operatingIncome", "netIncome", "ebitda", "dilutedEPS",
        ]),
        ("BALANCE_SHEET", "quarterlyReports", "Balance Sheet", [
            "fiscalDateEnding", "reportedCurrency", "totalAssets",
            "totalCurrentAssets", "cashAndCashEquivalentsAtCarryingValue",
            "totalLiabilities", "totalShareholderEquity", "shortLongTermDebtTotal",
        ]),
        ("CASH_FLOW", "quarterlyReports", "Cash Flow", [
            "fiscalDateEnding", "reportedCurrency", "operatingCashflow",
            "capitalExpenditures", "cashflowFromInvestment",
            "cashflowFromFinancing", "dividendPayout",
        ]),
    ]
    for function, report_key, label, keys in statement_specs:
        payload = call(function)
        if payload.get("_error"):
            sections.append(f"\n### {label}\n<unavailable: {payload['_error']}>")
            continue
        reports = _latest_reports(payload, report_key)
        if curr_date:
            reports = [r for r in reports if r.get("fiscalDateEnding", "") <= curr_date]
        if not reports:
            sections.append(f"\n### {label}\n<no reports available>")
            continue
        rows = []
        for report in reports:
            rows.append(_compact_dict(report, keys))
        sections.append(f"\n### {label} - latest reported quarters\n" + "\n\n".join(rows))

    earnings = call("EARNINGS")
    if not earnings.get("_error"):
        quarterly = _latest_reports(earnings, "quarterlyEarnings", 4)
        if curr_date:
            quarterly = [r for r in quarterly if r.get("reportedDate", "") <= curr_date]
        if quarterly:
            rows = [
                _compact_dict(r, ["fiscalDateEnding", "reportedDate", "reportedEPS", "estimatedEPS", "surprise", "surprisePercentage"])
                for r in quarterly
            ]
            sections.append("\n### Earnings surprise history\n" + "\n\n".join(rows))

    return "\n".join(sections)


def _resolve_cik(ticker: str) -> str | None:
    ticker_upper = ticker.upper()
    known = {
        "NVDA": "0001045810",
    }
    if ticker_upper in known:
        return known[ticker_upper]
    data = _get_json(_SEC_COMPANY_TICKERS)
    if isinstance(data, dict):
        for entry in data.values():
            if isinstance(entry, dict) and entry.get("ticker", "").upper() == ticker_upper:
                return f"{int(entry['cik_str']):010d}"
    return None


def get_sec_edgar_latest_filings_summary(ticker: str, curr_date: str | None = None) -> str:
    """Fetch latest 10-K, 10-Q, and 8-K links and document excerpts from SEC EDGAR."""
    try:
        cik = _resolve_cik(ticker)
        if not cik:
            return f"## SEC EDGAR Filings\n<unavailable: could not resolve CIK for {ticker.upper()}>"

        submissions = _get_json(_SEC_SUBMISSIONS.format(cik=cik))
        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        accession_numbers = recent.get("accessionNumber") or []
        primary_documents = recent.get("primaryDocument") or []

        wanted = {"10-K", "10-Q", "8-K"}
        selected: dict[str, dict[str, str]] = {}
        for form, date, accession, document in zip(forms, filing_dates, accession_numbers, primary_documents):
            if form not in wanted or form in selected:
                continue
            if curr_date and date > curr_date:
                continue
            accession_clean = accession.replace("-", "")
            link = _SEC_ARCHIVES.format(
                cik=str(int(cik)),
                accession=accession_clean,
                document=document,
            )
            selected[form] = {
                "date": date,
                "accession": accession,
                "document": document,
                "link": link,
            }
            if wanted.issubset(selected):
                break

        if not selected:
            return f"## SEC EDGAR Filings for {ticker.upper()}\n<no recent 10-K, 10-Q, or 8-K filings found>"

        sections = [f"## SEC EDGAR Latest Filings for {ticker.upper()}"]
        for form in ("10-K", "10-Q", "8-K"):
            item = selected.get(form)
            if not item:
                continue
            try:
                excerpt = _sec_document_excerpt(_get_text(item["link"]), form, limit=1800)
            except Exception as exc:
                excerpt = f"<document excerpt unavailable: {type(exc).__name__}: {exc}>"
            sections.append(
                f"\n### {form} filed {item['date']}\n"
                f"- Accession: {item['accession']}\n"
                f"- Document: {item['document']}\n"
                f"- Link: {item['link']}\n"
                f"- Excerpt: {excerpt}"
            )
        return "\n".join(sections)
    except Exception as exc:
        return f"## SEC EDGAR Filings\n<unavailable: {type(exc).__name__}: {exc}>"


def _extract_links(page_url: str, html_text: str, keywords: tuple[str, ...], limit: int = 8) -> list[dict[str, str]]:
    links = []
    seen = set()
    anchor_pattern = re.compile(r"(?is)<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>")
    for match in anchor_pattern.finditer(html_text):
        href = html.unescape(match.group("href"))
        label = _strip_html(match.group("label"), limit=240)
        haystack = f"{href} {label}".lower()
        if not any(keyword in haystack for keyword in keywords):
            continue
        url = urljoin(page_url, href)
        if "{{" in url or "}}" in url or "{{" in label or "}}" in label:
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append({"title": label or url, "url": url})
        if len(links) >= limit:
            break
    return links


def _parse_rss_titles(feed_url: str, limit: int = 5) -> list[dict[str, str]]:
    try:
        text = _get_text(feed_url)
        root = ElementTree.fromstring(text)
    except Exception:
        return []
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date = item.findtext("pubDate") or ""
        if title or link:
            items.append({"title": title.strip(), "url": link.strip(), "date": pub_date.strip()})
        if len(items) >= limit:
            break
    return items


def _resolve_ir_source(ticker: str) -> dict[str, Any] | None:
    source = _COMPANY_IR_SOURCES.get(ticker.upper())
    if source and "alias" in source:
        return _COMPANY_IR_SOURCES.get(source["alias"])
    return source


def get_company_ir_events(ticker: str, curr_date: str | None = None) -> str:
    """Fetch official company IR links for earnings, presentations, and RSS.

    This is a registry-driven best-effort source. SEC EDGAR remains the
    universal official fallback for US-listed companies when a ticker has
    no configured IR page or when a page changes shape.
    """
    ticker_upper = ticker.upper()
    source = _resolve_ir_source(ticker_upper)
    if not source:
        return (
            f"## Company Investor Relations for {ticker_upper}\n"
            "<no configured company IR source; use SEC EDGAR filings as the official fallback>"
        )

    name = source["name"]
    keywords = tuple(source.get("keywords") or _DEFAULT_IR_KEYWORDS)
    result = [f"## {name} Supplement for {ticker_upper}"]
    if curr_date:
        result.append(f"Simulation date: {curr_date}")

    pages = source.get("pages") or {}
    seen_urls: set[str] = set()
    for section, url in pages.items():
        result.append(f"\n### {section}")
        result.append(f"- Source page: {url}")
        try:
            page_html = _get_text(url)
            links = _extract_links(url, page_html, keywords, limit=10)
        except Exception as exc:
            result.append(f"- <unavailable: {type(exc).__name__}: {exc}>")
            continue
        useful_links = []
        for link in links:
            if link["url"] in seen_urls:
                continue
            seen_urls.add(link["url"])
            useful_links.append(link)
        if useful_links:
            result.extend(f"- {item['title']}: {item['url']}" for item in useful_links)
        else:
            result.append("- <no matching earnings, filing, presentation, or report links found>")

    rss_page = source.get("rss_page")
    if rss_page:
        result.append("\n### Official RSS feeds")
        try:
            rss_html = _get_text(rss_page)
            rss_links = _extract_links(rss_page, rss_html, ("rss",), limit=8)
        except Exception as exc:
            result.append(f"- <RSS page unavailable: {type(exc).__name__}: {exc}>")
            rss_links = []
        if not rss_links:
            result.append("- <no RSS links found>")
        for rss in rss_links:
            items = _parse_rss_titles(rss["url"])
            if not items:
                result.append(f"- {rss['title']}: {rss['url']}")
                continue
            result.append(f"\n#### Recent {rss['title']} items")
            result.extend(
                f"- {item.get('date', '')} {item['title']}: {item['url']}"
                for item in items
            )

    return "\n".join(result)


def get_nvidia_ir_events(ticker: str, curr_date: str | None = None) -> str:
    """Backward-compatible alias for the generic IR fetcher."""
    return get_company_ir_events(ticker, curr_date)

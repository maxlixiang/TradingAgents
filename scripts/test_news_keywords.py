"""Print RSSHub/newsnow keyword expansion for one or more tickers.

Examples:
    python scripts/test_news_keywords.py
    python scripts/test_news_keywords.py XOM ORCL CAT NFLX TSLA
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tradingagents.dataflows.news_keywords import NewsKeywordSet, build_news_keywords


DEFAULT_TICKERS = ("XOM", "ORCL", "CAT", "NFLX", "TSLA")
PROFILE_KEYS = ("shortName", "longName", "displayName", "industry", "sector")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect generated RSSHub/newsnow keywords.")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols to inspect.")
    parser.add_argument(
        "--no-yfinance",
        action="store_true",
        help="Skip the direct yfinance profile probe and only print generated keywords.",
    )
    args = parser.parse_args()

    tickers = tuple(ticker.upper() for ticker in (args.tickers or DEFAULT_TICKERS))
    for ticker in tickers:
        keyword_set = build_news_keywords(ticker)
        print(f"\n=== {ticker} ===")
        if not args.no_yfinance:
            _print_yfinance_profile(ticker)
        _print_keyword_set(keyword_set)


def _print_yfinance_profile(ticker: str) -> None:
    print("yfinance_profile:")
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        return

    found = False
    for key in PROFILE_KEYS:
        value = info.get(key)
        if value:
            found = True
            print(f"  {key}: {value}")
    if not found:
        print("  No profile fields returned.")


def _print_keyword_set(keyword_set: NewsKeywordSet) -> None:
    print("keywords:")
    _print_terms("ticker_terms", keyword_set.ticker_terms)
    _print_terms("company_terms", keyword_set.company_terms)
    _print_terms("product_terms", keyword_set.product_terms)
    _print_terms("industry_terms", keyword_set.industry_terms)
    _print_terms("peer_terms", keyword_set.peer_terms)
    _print_terms("all_terms", keyword_set.all_terms)


def _print_terms(label: str, terms: Iterable[str]) -> None:
    values = tuple(terms)
    rendered = ", ".join(values) if values else "(none)"
    print(f"  {label}: {rendered}")


if __name__ == "__main__":
    main()

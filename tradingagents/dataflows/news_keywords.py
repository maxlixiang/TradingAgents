"""Keyword expansion for RSSHub/newsnow relevance filtering."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG


KEYWORD_DATABASE_PATH = Path(__file__).with_name("news_keywords.json")
CACHE_FILENAME = "news_keywords_cache.json"
KEYWORD_FIELDS = ("company_terms", "product_terms", "industry_terms", "peer_terms")
PROFILE_KEYS = ("shortName", "longName", "displayName", "industry", "sector")


@dataclass(frozen=True)
class NewsKeywordSet:
    ticker_terms: tuple[str, ...]
    company_terms: tuple[str, ...]
    product_terms: tuple[str, ...]
    industry_terms: tuple[str, ...]
    peer_terms: tuple[str, ...]
    all_terms: tuple[str, ...]


INDUSTRY_KEYWORD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("semiconductor", ("semiconductor", "chip", "半导体", "芯片", "晶圆", "先进封装", "AI芯片")),
    ("chip", ("semiconductor", "chip", "半导体", "芯片")),
    ("memory", ("memory", "NAND", "DRAM", "闪存", "存储", "内存", "存储芯片", "存储器")),
    ("storage", ("storage", "SSD", "flash storage", "闪存", "存储", "固态硬盘", "数据中心存储")),
    ("hardware", ("hardware", "设备", "硬件", "供应链")),
    ("software", ("software", "cloud", "软件", "云服务", "订阅")),
    ("automotive", ("automotive", "EV", "汽车", "电动车", "自动驾驶")),
    ("automobile", ("automotive", "EV", "汽车", "电动车", "自动驾驶")),
    ("bank", ("bank", "银行", "利率", "信贷")),
    ("cloud", ("cloud", "AI infrastructure", "云计算", "云服务", "数据中心")),
    ("construction", ("construction", "infrastructure", "工程机械", "基建", "建筑")),
    ("database", ("database", "ERP", "enterprise software", "数据库", "企业软件")),
    ("energy", ("energy", "oil", "natural gas", "LNG", "能源", "原油", "天然气", "液化天然气")),
    ("entertainment", ("media", "streaming", "subscription", "content", "媒体", "流媒体", "订阅", "内容")),
    ("industrial", ("industrial", "machinery", "equipment", "工业", "机械", "设备")),
    ("media", ("media", "streaming", "advertising", "媒体", "流媒体", "广告")),
    ("mining", ("mining", "commodity", "矿山", "大宗商品")),
    ("oil", ("energy", "oil", "natural gas", "refining", "能源", "原油", "天然气", "炼化")),
    ("streaming", ("streaming", "subscription", "advertising tier", "流媒体", "订阅", "广告套餐")),
)


def build_news_keywords(ticker: str) -> NewsKeywordSet:
    """Build layered English/Chinese keyword hints for a ticker."""
    ticker_upper = (ticker or "").strip().upper()
    ticker_lower = ticker_upper.lower()
    ticker_terms = tuple(
        term for term in (ticker_upper, f"${ticker_upper}", ticker_lower, f"${ticker_lower}") if term
    )

    curated = _curated_keywords_for_ticker(ticker_upper)
    dynamic = _dynamic_keywords_for_ticker(ticker_upper, allow_generate=not curated.all_terms)

    company_terms = _unique_terms((*curated.company_terms, *dynamic.company_terms))
    industry_terms = _unique_terms((*curated.industry_terms, *dynamic.industry_terms))
    product_terms = _unique_terms((
        *curated.product_terms,
        *dynamic.product_terms,
        *_product_terms_from_industry(industry_terms),
    ))
    peer_terms = _unique_terms((*curated.peer_terms, *dynamic.peer_terms))

    non_ticker_terms = _unique_terms((*company_terms, *product_terms, *industry_terms, *peer_terms))
    all_terms = tuple(dict.fromkeys((*ticker_terms, *non_ticker_terms)))

    return NewsKeywordSet(
        ticker_terms=ticker_terms,
        company_terms=company_terms,
        product_terms=product_terms,
        industry_terms=industry_terms,
        peer_terms=peer_terms,
        all_terms=all_terms,
    )


@lru_cache(maxsize=512)
def _curated_keywords_for_ticker(ticker_upper: str) -> NewsKeywordSet:
    return _keyword_set_from_mapping(_load_curated_keyword_database().get(ticker_upper, {}))


@lru_cache(maxsize=512)
def _dynamic_keywords_for_ticker(ticker_upper: str, allow_generate: bool) -> NewsKeywordSet:
    cached = _load_keyword_cache().get(ticker_upper)
    if cached:
        return _keyword_set_from_mapping(cached)
    if not allow_generate:
        return _empty_keyword_set()

    generated = _generate_dynamic_keywords_for_ticker(ticker_upper)
    if generated.all_terms:
        _write_keyword_cache_entry(ticker_upper, generated)
    return generated


@lru_cache(maxsize=1)
def _load_curated_keyword_database() -> dict[str, dict[str, tuple[str, ...]]]:
    raw = _read_json_object(KEYWORD_DATABASE_PATH)
    database: dict[str, dict[str, tuple[str, ...]]] = {}
    for ticker, payload in raw.items():
        if isinstance(ticker, str) and isinstance(payload, dict):
            database[ticker.upper()] = _normalize_keyword_payload(payload)
    return database


def _load_keyword_cache() -> dict[str, dict[str, tuple[str, ...]]]:
    raw = _read_json_object(_keyword_cache_path())
    cache: dict[str, dict[str, tuple[str, ...]]] = {}
    for ticker, payload in raw.items():
        if isinstance(ticker, str) and isinstance(payload, dict):
            cache[ticker.upper()] = _normalize_keyword_payload(payload)
    return cache


def _write_keyword_cache_entry(ticker_upper: str, keyword_set: NewsKeywordSet) -> None:
    cache_path = _keyword_cache_path()
    payload = _load_keyword_cache()
    payload[ticker_upper] = {
        "company_terms": keyword_set.company_terms,
        "product_terms": keyword_set.product_terms,
        "industry_terms": keyword_set.industry_terms,
        "peer_terms": keyword_set.peer_terms,
    }

    serializable = {
        ticker: {field: list(values) for field, values in fields.items()}
        for ticker, fields in sorted(payload.items())
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _generate_dynamic_keywords_for_ticker(ticker_upper: str) -> NewsKeywordSet:
    profile = _profile_for_ticker(ticker_upper)
    if not profile:
        return _empty_keyword_set()

    company_terms = _profile_name_terms(profile)
    industry_terms = _profile_industry_terms(profile)
    product_terms = _product_terms_from_industry(industry_terms)

    return NewsKeywordSet(
        ticker_terms=(),
        company_terms=company_terms,
        product_terms=product_terms,
        industry_terms=industry_terms,
        peer_terms=(),
        all_terms=_unique_terms((*company_terms, *product_terms, *industry_terms)),
    )


def _profile_for_ticker(ticker_upper: str) -> dict[str, str]:
    """Best-effort yfinance profile lookup; failures simply return no dynamic terms."""
    try:
        import yfinance as yf

        info = yf.Ticker(ticker_upper).info or {}
    except Exception:
        return {}

    profile: dict[str, str] = {}
    for key in PROFILE_KEYS:
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            profile[key] = value.strip()
    return profile


def _profile_name_terms(profile: dict[str, str]) -> tuple[str, ...]:
    terms: list[str] = []
    for key in ("shortName", "longName", "displayName"):
        value = profile.get(key)
        if value:
            terms.extend(_name_keyword_variants(value))
    return _unique_terms(terms)


def _profile_industry_terms(profile: dict[str, str]) -> tuple[str, ...]:
    terms: list[str] = []
    for key in ("industry", "sector"):
        value = profile.get(key)
        if value:
            terms.append(value)
            terms.extend(_industry_aliases(value))
    return _unique_terms(terms)


def _product_terms_from_industry(industry_terms: tuple[str, ...]) -> tuple[str, ...]:
    joined = " ".join(industry_terms).lower()
    terms: list[str] = []
    if "memory" in joined or "storage" in joined or "存储" in joined or "闪存" in joined:
        terms.extend(("NAND", "DRAM", "flash", "SSD", "固态硬盘", "存储芯片"))
    elif "semiconductor" in joined or "chip" in joined or "半导体" in joined or "芯片" in joined:
        terms.extend(("GPU", "AI chip", "AI accelerator", "晶圆", "先进封装"))
    return _unique_terms(terms)


def _industry_aliases(value: str) -> list[str]:
    lowered = value.lower()
    aliases: list[str] = []
    for needle, terms in INDUSTRY_KEYWORD_ALIASES:
        if needle in lowered:
            aliases.extend(terms)
    return aliases


def _name_keyword_variants(value: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return []

    variants = [cleaned]
    suffix_pattern = re.compile(
        r"\b(incorporated|inc\.?|corp\.?|corporation|company|co\.?|ltd\.?|limited|plc|class\s+[a-z])\b",
        flags=re.IGNORECASE,
    )
    base = suffix_pattern.sub(" ", cleaned)
    base = re.sub(r"[,.\s]+", " ", base).strip()
    if base and base.lower() != cleaned.lower():
        variants.append(base)

    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", base) if len(token) >= 3]
    variants.extend(tokens[:4])
    return variants[:8]


def _keyword_set_from_mapping(payload: dict[str, Any]) -> NewsKeywordSet:
    company_terms = tuple(payload.get("company_terms", ()))
    product_terms = tuple(payload.get("product_terms", ()))
    industry_terms = tuple(payload.get("industry_terms", ()))
    peer_terms = tuple(payload.get("peer_terms", ()))
    return NewsKeywordSet(
        ticker_terms=(),
        company_terms=company_terms,
        product_terms=product_terms,
        industry_terms=industry_terms,
        peer_terms=peer_terms,
        all_terms=_unique_terms((*company_terms, *product_terms, *industry_terms, *peer_terms)),
    )


def _normalize_keyword_payload(payload: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    return {field: _unique_terms(_coerce_terms(payload.get(field))) for field in KEYWORD_FIELDS}


def _coerce_terms(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str))
    if isinstance(value, tuple):
        return tuple(str(item) for item in value if isinstance(item, str))
    return ()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _keyword_cache_path() -> Path:
    cache_dir = Path(str(DEFAULT_CONFIG.get("data_cache_dir") or Path.home() / ".tradingagents" / "cache"))
    return cache_dir / CACHE_FILENAME


def _empty_keyword_set() -> NewsKeywordSet:
    return NewsKeywordSet(
        ticker_terms=(),
        company_terms=(),
        product_terms=(),
        industry_terms=(),
        peer_terms=(),
        all_terms=(),
    )


def _unique_terms(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        term = re.sub(r"\s+", " ", str(value or "")).strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return tuple(terms)

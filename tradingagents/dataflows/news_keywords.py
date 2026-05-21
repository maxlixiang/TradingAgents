"""Keyword expansion for RSSHub/newsnow relevance filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class NewsKeywordSet:
    ticker_terms: tuple[str, ...]
    company_terms: tuple[str, ...]
    product_terms: tuple[str, ...]
    industry_terms: tuple[str, ...]
    peer_terms: tuple[str, ...]
    all_terms: tuple[str, ...]


CHINESE_COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "AAPL": ("苹果",),
    "AMD": ("超威半导体", "超微半导体"),
    "AMZN": ("亚马逊",),
    "AVGO": ("博通",),
    "GOOG": ("谷歌",),
    "GOOGL": ("谷歌",),
    "INTC": ("英特尔",),
    "CAT": ("卡特彼勒",),
    "META": ("Meta", "Facebook"),
    "MSFT": ("微软",),
    "NFLX": ("奈飞", "网飞"),
    "NVDA": ("英伟达", "辉达"),
    "ORCL": ("甲骨文",),
    "SNDK": ("闪迪", "SanDisk"),
    "TSLA": ("特斯拉",),
    "XOM": ("埃克森美孚",),
}


STATIC_COMPANY_TERMS: dict[str, tuple[str, ...]] = {
    "AMD": ("Advanced Micro Devices",),
    "GOOG": ("Alphabet", "Google"),
    "GOOGL": ("Alphabet", "Google"),
    "CAT": ("Caterpillar", "Caterpillar Inc"),
    "META": ("Meta Platforms", "Facebook", "Instagram", "WhatsApp"),
    "NFLX": ("Netflix", "Netflix Inc"),
    "NVDA": ("NVIDIA", "Nvidia Corporation"),
    "ORCL": ("Oracle", "Oracle Corporation"),
    "SNDK": ("SanDisk", "Sandisk Corporation", "San Disk"),
    "TSLA": ("Tesla", "Tesla Inc"),
    "XOM": ("Exxon Mobil", "ExxonMobil", "Exxon Mobil Corporation"),
}


TICKER_PRODUCT_TERMS: dict[str, tuple[str, ...]] = {
    "AAPL": ("iPhone", "App Store", "iOS", "Mac", "services"),
    "AMD": ("GPU", "CPU", "MI300", "MI350", "Instinct", "EPYC", "Ryzen"),
    "AMZN": ("AWS", "Prime", "e-commerce", "cloud computing"),
    "AVGO": ("VMware", "ASIC", "networking chip", "AI accelerator"),
    "GOOG": ("Gemini", "Search", "YouTube", "Google Cloud"),
    "GOOGL": ("Gemini", "Search", "YouTube", "Google Cloud"),
    "INTC": ("foundry", "CPU", "data center", "Gaudi"),
    "CAT": ("excavator", "bulldozer", "construction equipment", "mining equipment", "engine", "发电机", "挖掘机", "推土机"),
    "META": ("Facebook", "Instagram", "WhatsApp", "Threads", "metaverse", "AI"),
    "MSFT": ("Azure", "Copilot", "Windows", "Office", "cloud"),
    "NFLX": ("streaming", "subscription", "advertising tier", "content", "series", "film", "流媒体", "订阅", "广告套餐", "剧集", "影视内容"),
    "NVDA": (
        "GPU",
        "CUDA",
        "Blackwell",
        "Rubin",
        "GB200",
        "H100",
        "H200",
        "AI chip",
        "AI accelerator",
        "算力",
        "数据中心",
    ),
    "ORCL": ("Oracle Cloud", "OCI", "database", "ERP", "NetSuite", "Fusion Cloud", "AI infrastructure", "数据库", "云服务", "企业软件"),
    "SNDK": (
        "NAND",
        "DRAM",
        "flash",
        "flash storage",
        "memory",
        "storage",
        "SSD",
        "solid state drive",
        "闪存",
        "存储",
        "内存",
        "固态硬盘",
        "存储芯片",
        "存储器",
    ),
    "TSLA": ("EV", "electric vehicle", "robotaxi", "FSD", "Model Y", "Megapack"),
    "XOM": ("oil", "natural gas", "LNG", "upstream", "downstream", "refining", "Permian", "Guyana", "原油", "天然气", "液化天然气", "炼油"),
}


TICKER_INDUSTRY_TERMS: dict[str, tuple[str, ...]] = {
    "AMD": ("semiconductor", "chip", "半导体", "芯片", "AI芯片"),
    "AVGO": ("semiconductor", "chip", "半导体", "芯片", "ASIC"),
    "CAT": ("industrial", "machinery", "construction", "mining", "infrastructure", "工业", "机械", "工程机械", "矿山", "基建"),
    "INTC": ("semiconductor", "foundry", "半导体", "芯片", "晶圆代工"),
    "NFLX": ("media", "entertainment", "streaming", "subscription", "媒体", "娱乐", "流媒体", "订阅"),
    "NVDA": ("semiconductor", "chip", "半导体", "芯片", "晶圆", "先进封装", "GPU", "AI芯片"),
    "ORCL": ("software", "cloud", "database", "enterprise software", "软件", "云计算", "数据库", "企业软件"),
    "SNDK": ("memory", "storage", "semiconductor", "半导体", "芯片", "NAND", "DRAM", "闪存", "存储"),
    "TSLA": ("automotive", "automobile", "汽车", "电动车", "自动驾驶"),
    "XOM": ("energy", "oil and gas", "integrated oil", "refining", "能源", "石油天然气", "油气", "炼化"),
}


TICKER_PEER_TERMS: dict[str, tuple[str, ...]] = {
    "AMD": ("NVIDIA", "Intel", "TSMC", "台积电", "GPU", "AI accelerator"),
    "AVGO": ("NVIDIA", "Marvell", "TSMC", "台积电", "ASIC"),
    "INTC": ("AMD", "NVIDIA", "TSMC", "台积电", "foundry"),
    "NVDA": ("AMD", "Broadcom", "AVGO", "TSMC", "台积电", "HBM", "SK Hynix", "Micron"),
    "CAT": ("Deere", "Komatsu", "Cummins", "Volvo", "小松", "迪尔", "矿山资本开支", "基建投资"),
    "NFLX": ("Disney", "DIS", "Warner Bros Discovery", "WBD", "Amazon Prime Video", "YouTube", "HBO Max", "迪士尼"),
    "ORCL": ("Microsoft", "Azure", "Amazon Web Services", "AWS", "Google Cloud", "SAP", "Salesforce", "Snowflake"),
    "SNDK": ("Micron", "Samsung", "Kioxia", "SK Hynix", "NAND", "DRAM", "铠侠", "美光", "三星"),
    "TSLA": ("BYD", "比亚迪", "Rivian", "Lucid", "Panasonic", "宁德时代", "CATL", "lithium", "锂电池"),
    "XOM": ("Chevron", "CVX", "Shell", "BP", "ConocoPhillips", "OPEC", "WTI", "Brent", "雪佛龙", "欧佩克"),
}


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
    profile = _profile_for_ticker(ticker_upper)

    company_terms = _unique_terms((
        *STATIC_COMPANY_TERMS.get(ticker_upper, ()),
        *CHINESE_COMPANY_ALIASES.get(ticker_upper, ()),
        *_profile_name_terms(profile),
    ))
    industry_terms = _unique_terms((
        *TICKER_INDUSTRY_TERMS.get(ticker_upper, ()),
        *_profile_industry_terms(profile),
    ))
    product_terms = _unique_terms((
        *TICKER_PRODUCT_TERMS.get(ticker_upper, ()),
        *_product_terms_from_industry(industry_terms),
    ))
    peer_terms = _unique_terms(TICKER_PEER_TERMS.get(ticker_upper, ()))
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


@lru_cache(maxsize=256)
def _profile_for_ticker(ticker_upper: str) -> dict[str, str]:
    """Best-effort yfinance profile lookup; failures simply return no dynamic terms."""
    try:
        import yfinance as yf

        info = yf.Ticker(ticker_upper).info or {}
    except Exception:
        return {}

    profile: dict[str, str] = {}
    for key in ("shortName", "longName", "displayName", "industry", "sector"):
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
    return tuple(terms)


def _profile_industry_terms(profile: dict[str, str]) -> tuple[str, ...]:
    terms: list[str] = []
    for key in ("industry", "sector"):
        value = profile.get(key)
        if value:
            terms.append(value)
            terms.extend(_industry_aliases(value))
    return tuple(terms)


def _product_terms_from_industry(industry_terms: tuple[str, ...]) -> tuple[str, ...]:
    joined = " ".join(industry_terms).lower()
    terms: list[str] = []
    if "memory" in joined or "storage" in joined or "存储" in joined or "闪存" in joined:
        terms.extend(("NAND", "DRAM", "flash", "SSD", "固态硬盘", "存储芯片"))
    elif "semiconductor" in joined or "chip" in joined or "半导体" in joined or "芯片" in joined:
        terms.extend(("GPU", "AI chip", "AI accelerator", "晶圆", "先进封装"))
    return tuple(terms)


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

"""Non-interactive TradingAgents analysis runner."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.services.report_writer import save_report_to_disk


DEFAULT_ANALYSTS = ("market", "social", "news", "fundamentals")
ANALYST_ALIASES = {
    "market": "market",
    "technical": "market",
    "social": "social",
    "sentiment": "social",
    "news": "news",
    "fundamental": "fundamentals",
    "fundamentals": "fundamentals",
}
CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


@dataclass(frozen=True)
class AnalysisJobResult:
    ticker: str
    analysis_date: str
    decision: str
    save_path: Path
    complete_report_path: Path
    final_state: dict[str, Any]


def normalize_ticker(ticker: str) -> str:
    normalized = (ticker or "").strip().upper()
    safe_ticker_component(normalized)
    return normalized


def detect_asset_type(ticker: str) -> str:
    return "crypto" if ticker.upper().endswith(CRYPTO_SUFFIXES) else "stock"


def normalize_analysts(analysts: str | Iterable[str] | None, asset_type: str = "stock") -> list[str]:
    if analysts is None:
        raw_items = list(DEFAULT_ANALYSTS)
    elif isinstance(analysts, str):
        raw_items = [item.strip() for item in analysts.split(",") if item.strip()]
    else:
        raw_items = [str(item).strip() for item in analysts if str(item).strip()]

    normalized: list[str] = []
    for item in raw_items:
        key = ANALYST_ALIASES.get(item.lower())
        if key is None:
            allowed = ", ".join(sorted(ANALYST_ALIASES))
            raise ValueError(f"Unknown analyst '{item}'. Allowed values: {allowed}")
        if key not in normalized:
            normalized.append(key)

    if asset_type == "crypto":
        normalized = [item for item in normalized if item != "fundamentals"]
    if not normalized:
        raise ValueError("At least one analyst must be selected.")
    return [item for item in DEFAULT_ANALYSTS if item in normalized]


def normalize_depth(depth: str | int | None) -> int:
    if depth is None or depth == "":
        return 1
    if isinstance(depth, int):
        value = depth
    else:
        aliases = {"shallow": 1, "quick": 1, "medium": 2, "deep": 3}
        lowered = str(depth).strip().lower()
        value = aliases.get(lowered, None)
        if value is None:
            value = int(lowered)
    if value < 1:
        raise ValueError("Depth must be >= 1.")
    return value


def normalize_date(analysis_date: str | None) -> str:
    if not analysis_date:
        return dt.datetime.now().strftime("%Y-%m-%d")
    parsed = dt.datetime.strptime(analysis_date, "%Y-%m-%d").date()
    if parsed > dt.datetime.now().date():
        raise ValueError("Analysis date cannot be in the future.")
    return parsed.strftime("%Y-%m-%d")


def run_analysis_job(
    ticker: str,
    analysis_date: str | None = None,
    analysts: str | Iterable[str] | None = None,
    depth: str | int | None = None,
    asset_type: str | None = None,
    output_language: str | None = None,
    llm_provider: str | None = None,
    backend_url: str | None = None,
    quick_think_llm: str | None = None,
    deep_think_llm: str | None = None,
    results_dir: str | Path | None = None,
) -> AnalysisJobResult:
    """Run a TradingAgents analysis without any interactive prompts."""
    normalized_ticker = normalize_ticker(ticker)
    normalized_date = normalize_date(analysis_date)
    normalized_asset_type = asset_type or detect_asset_type(normalized_ticker)
    selected_analysts = normalize_analysts(analysts, normalized_asset_type)
    normalized_depth = normalize_depth(depth)

    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = normalized_depth
    config["max_risk_discuss_rounds"] = normalized_depth
    if output_language:
        config["output_language"] = output_language
    if llm_provider:
        config["llm_provider"] = llm_provider
    if backend_url:
        config["backend_url"] = backend_url
    if quick_think_llm:
        config["quick_think_llm"] = quick_think_llm
    if deep_think_llm:
        config["deep_think_llm"] = deep_think_llm

    graph = TradingAgentsGraph(
        selected_analysts,
        config=config,
        debug=False,
    )
    final_state, decision = graph.propagate(
        normalized_ticker,
        normalized_date,
        asset_type=normalized_asset_type,
    )

    root = Path(results_dir) if results_dir else Path.cwd() / "reports"
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = root / f"{normalized_ticker}_{timestamp}"
    complete_report_path = save_report_to_disk(final_state, normalized_ticker, save_path)

    return AnalysisJobResult(
        ticker=normalized_ticker,
        analysis_date=normalized_date,
        decision=decision,
        save_path=save_path,
        complete_report_path=complete_report_path,
        final_state=final_state,
    )

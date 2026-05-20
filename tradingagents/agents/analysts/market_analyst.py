from datetime import datetime, timedelta
from typing import Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_indicators,
    get_language_instruction,
    get_stock_data,
)


DEFAULT_MARKET_AUDIT_INDICATORS = [
    "close_10_ema",
    "close_50_sma",
    "close_200_sma",
    "macd",
    "rsi",
    "boll",
    "boll_ub",
    "boll_lb",
]


def create_market_analyst(llm):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        instrument_context = build_instrument_context(
            state["company_of_interest"], asset_type
        )

        tools = [
            get_stock_data,
            get_indicators,
        ]

        system_message = (
            """You are a trading assistant tasked with analyzing financial markets. Your role is to select the **most relevant indicators** for a given market condition or trading strategy from the following list. The goal is to choose up to **8 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.

- Select indicators that provide diverse and complementary information. Avoid redundancy (e.g., do not select both rsi and stochrsi). Also briefly explain why they are suitable for the given market context. When you tool call, please use the exact name of the indicators provided above as they are defined parameters, otherwise your call will fail. Please make sure to call get_stock_data first to retrieve the CSV that is needed to generate indicators. Then use get_indicators with the specific indicator names. Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."""
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content
            if "Market 原始行情与指标表" not in report:
                report = (
                    report.rstrip()
                    + "\n\n"
                    + _build_market_audit_appendix(
                        ticker=state["company_of_interest"],
                        current_date=current_date,
                        messages=state["messages"],
                        report=report,
                    )
                )

        return {
            "messages": [result],
            "market_report": report,
        }

    return market_analyst_node


def _build_market_audit_appendix(
    *,
    ticker: str,
    current_date: str,
    messages: list[Any],
    report: str,
) -> str:
    """Build a compact appendix with the market data injected through tools."""
    start_date = _days_back(current_date, 30)
    audit_indicators = _extract_market_audit_indicators(messages, report)
    stock_block = _safe_tool_call(
        lambda: get_stock_data.func(ticker, start_date, current_date),
        f"OHLCV data unavailable for {ticker} from {start_date} to {current_date}.",
    )
    indicator_block = _safe_tool_call(
        lambda: get_indicators.func(
            ticker,
            ",".join(audit_indicators),
            current_date,
            30,
        ),
        f"Technical indicators unavailable for {ticker} as of {current_date}.",
    )

    return (
        "## Market 原始行情与指标表\n\n"
        "以下为本轮 Market Analyst 使用的结构化行情与技术指标审计信息。"
        "行情源和指标源遵循当前配置的 data vendor，通常优先使用 yfinance，并在可用时按配置回退。\n\n"
        f"- Ticker: `{ticker}`\n"
        f"- Window: `{start_date}` to `{current_date}`\n"
        f"- Indicator set: `{', '.join(audit_indicators)}`\n"
        "- Indicator selection source: actual `get_indicators` tool calls when available; "
        "otherwise report text scan plus default audit set.\n\n"
        "### OHLCV\n\n"
        f"{stock_block}\n\n"
        "### Technical Indicators\n\n"
        f"{indicator_block}"
    )


def _extract_market_audit_indicators(messages: list[Any], report: str) -> list[str]:
    allowed_order = DEFAULT_MARKET_AUDIT_INDICATORS + ["macds", "macdh", "atr", "vwma", "mfi"]
    allowed = set(allowed_order)
    found: list[str] = []

    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", "")
            if name != "get_indicators":
                continue
            args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
            indicator_value = ""
            if isinstance(args, dict):
                indicator_value = str(args.get("indicator") or "")
            else:
                indicator_value = str(args or "")
            for item in indicator_value.split(","):
                indicator = item.strip().lower()
                if indicator in allowed and indicator not in found:
                    found.append(indicator)

    lowered_report = report.lower()
    for indicator in allowed_order:
        if re_search_indicator(indicator, lowered_report) and indicator not in found:
            found.append(indicator)

    for indicator in DEFAULT_MARKET_AUDIT_INDICATORS:
        if indicator not in found:
            found.append(indicator)

    return found[:10]


def re_search_indicator(indicator: str, lowered_text: str) -> bool:
    aliases = {
        "close_10_ema": ("close_10_ema", "10 ema", "10日均线"),
        "close_50_sma": ("close_50_sma", "50 sma", "50日均线"),
        "close_200_sma": ("close_200_sma", "200 sma", "200日均线"),
        "macd": ("macd",),
        "macds": ("macds", "macd signal"),
        "macdh": ("macdh", "macd histogram", "macd柱"),
        "rsi": ("rsi",),
        "boll": ("boll", "布林带中轨"),
        "boll_ub": ("boll_ub", "布林带上轨"),
        "boll_lb": ("boll_lb", "布林带下轨"),
        "atr": ("atr", "平均真实波幅"),
        "vwma": ("vwma",),
        "mfi": ("mfi", "money flow index"),
    }
    return any(alias in lowered_text for alias in aliases.get(indicator, (indicator,)))


def _safe_tool_call(fetcher, fallback: str) -> str:
    try:
        return fetcher()
    except Exception as exc:
        return f"{fallback}\n\nError: {exc}"


def _days_back(current_date: str, days: int) -> str:
    return (datetime.strptime(current_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")

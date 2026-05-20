from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_alpha_vantage_fundamentals_summary,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
    get_company_ir_events,
    get_sec_edgar_latest_filings_summary,
)
from tradingagents.dataflows.config import get_config


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            get_alpha_vantage_fundamentals_summary,
            get_sec_edgar_latest_filings_summary,
            get_company_ir_events,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information about a company. "
            "Build a comprehensive, source-aware report covering company profile, valuation, profitability, balance-sheet strength, cash generation, earnings quality, recent SEC filing context, management guidance, and investor-relations events. "
            "Use yfinance tools as the baseline for company fundamentals and financial statements. "
            "Use `get_alpha_vantage_fundamentals_summary` as a structured cross-check and supplement for ratios, recent statements, and earnings surprises. "
            "Use `get_sec_edgar_latest_filings_summary` to ground the report in the latest 10-K, 10-Q, and 8-K filings, including links for auditability. "
            "Use `get_company_ir_events` to collect configured company IR earnings-release, presentation, official news, and RSS links; if no company-specific IR source is configured, rely on SEC EDGAR as the official fallback. "
            "Separate hard financial facts from management commentary, analyst expectations, and your own inference. "
            "Flag source conflicts explicitly rather than smoothing them over. "
            "Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for baseline company analysis; `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for yfinance statements; `get_alpha_vantage_fundamentals_summary` for Alpha Vantage structured fundamentals; `get_sec_edgar_latest_filings_summary` for SEC links and excerpts; and `get_company_ir_events` for configured official company IR releases, events, presentations, and RSS feeds."
            + get_language_instruction(),
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

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node

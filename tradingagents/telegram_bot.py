"""Telegram long-polling front end for TradingAgents."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tradingagents.services.analysis_runner import (
    DEFAULT_ANALYSTS,
    detect_asset_type,
    normalize_analysts,
    normalize_date,
    normalize_depth,
    normalize_ticker,
    run_analysis_job,
)
from tradingagents.services.report_writer import find_latest_report
from tradingagents.llm_clients.model_catalog import get_model_options


MAX_TELEGRAM_FILE_BYTES = 45 * 1024 * 1024
ANALYST_LABELS = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
LANGUAGE_OPTIONS = (
    ("Chinese", "中文"),
    ("English", "English"),
)
DEPTH_OPTIONS = (
    ("1", "Shallow - 快速研究"),
    ("2", "Medium - 中等研究"),
    ("3", "Deep - 深度研究"),
)
LLM_PROVIDERS = (
    ("deepseek", "DeepSeek", "https://api.deepseek.com"),
    ("openai", "OpenAI", "https://api.openai.com/v1"),
    ("google", "Google", None),
    ("anthropic", "Anthropic", "https://api.anthropic.com/"),
    ("xai", "xAI", "https://api.x.ai/v1"),
    ("qwen", "Qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ("glm", "GLM", "https://open.bigmodel.cn/api/paas/v4/"),
    ("minimax", "MiniMax", "https://api.minimax.io/v1"),
    ("ollama", "Ollama", os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1"),
)
USER_SESSIONS: dict[int, dict[str, Any]] = {}


@dataclass
class BotJobState:
    lock: asyncio.Lock
    current_ticker: str | None = None
    current_started_at: float | None = None
    latest_report: Path | None = None

    def is_running(self) -> bool:
        return self.lock.locked()


JOB_STATE = BotJobState(lock=asyncio.Lock())


def _allowed_user_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
    ids: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        ids.add(int(item))
    return ids


def _is_allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in _allowed_user_ids())


async def _deny_if_needed(update: Update) -> bool:
    if _is_allowed(update):
        return False
    if update.effective_message:
        await update.effective_message.reply_text("无权限使用此机器人。")
    return True


def _default_analysts() -> str:
    return os.getenv("TELEGRAM_DEFAULT_ANALYSTS", ",".join(DEFAULT_ANALYSTS))


def _default_depth() -> str:
    return os.getenv("TELEGRAM_DEFAULT_DEPTH", "1")


def _default_language() -> str:
    return os.getenv("TELEGRAM_DEFAULT_OUTPUT_LANGUAGE", os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "Chinese"))


def _default_llm_provider() -> str:
    return os.getenv("TELEGRAM_DEFAULT_LLM_PROVIDER", os.getenv("TRADINGAGENTS_LLM_PROVIDER", "deepseek")).lower()


def _default_quick_model() -> str:
    return os.getenv("TELEGRAM_DEFAULT_QUICK_THINK_LLM", os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", "deepseek-v4-flash"))


def _default_deep_model() -> str:
    return os.getenv("TELEGRAM_DEFAULT_DEEP_THINK_LLM", os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", "deepseek-v4-flash"))


def _provider_backend_url(provider: str) -> str | None:
    for key, _, url in LLM_PROVIDERS:
        if key == provider:
            return url
    return None


def _reports_dir() -> Path:
    return Path(os.getenv("TELEGRAM_REPORTS_DIR", "reports"))


def _format_elapsed(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def _user_id(update: Update) -> int | None:
    user = update.effective_user
    return user.id if user else None


def _new_session() -> dict[str, Any]:
    return {
        "step": "ticker",
        "ticker": None,
        "asset_type": None,
        "analysis_date": normalize_date(None),
        "language": _default_language(),
        "analysts": set(DEFAULT_ANALYSTS),
        "depth": normalize_depth(_default_depth()),
        "llm_provider": _default_llm_provider(),
        "quick_model": _default_quick_model(),
        "deep_model": _default_deep_model(),
    }


def _session_summary(session: dict[str, Any]) -> str:
    analysts = ",".join(item for item in DEFAULT_ANALYSTS if item in session["analysts"])
    return (
        f"股票代码：{session['ticker']}\n"
        f"资产类型：{session['asset_type']}\n"
        f"分析日期：{session['analysis_date']}\n"
        f"输出语言：{session['language']}\n"
        f"分析师：{analysts}\n"
        f"研究深度：{session['depth']}\n"
        f"LLM Provider：{session['llm_provider']}\n"
        f"Quick Model：{session['quick_model']}\n"
        f"Deep Model：{session['deep_model']}"
    )


def _date_keyboard() -> InlineKeyboardMarkup:
    today = normalize_date(None)
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"✅ 使用今天 {today}", callback_data=f"date:{today}")],
            [InlineKeyboardButton("✏️ 手动输入日期", callback_data="date:custom")],
        ]
    )


def _language_keyboard(selected: str) -> InlineKeyboardMarkup:
    rows = []
    for value, label in LANGUAGE_OPTIONS:
        prefix = "✅" if value == selected else "⬜"
        rows.append([InlineKeyboardButton(f"{prefix} {label}", callback_data=f"lang:{value}")])
    return InlineKeyboardMarkup(rows)


def _analysts_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for key in DEFAULT_ANALYSTS:
        prefix = "✅" if key in selected else "⬜"
        rows.append([InlineKeyboardButton(f"{prefix} {ANALYST_LABELS[key]}", callback_data=f"analyst:{key}")])
    rows.append([InlineKeyboardButton("完成选择", callback_data="analyst:done")])
    return InlineKeyboardMarkup(rows)


def _depth_keyboard(selected: int) -> InlineKeyboardMarkup:
    rows = []
    for value, label in DEPTH_OPTIONS:
        prefix = "✅" if int(value) == selected else "⬜"
        rows.append([InlineKeyboardButton(f"{prefix} {label}", callback_data=f"depth:{value}")])
    return InlineKeyboardMarkup(rows)


def _llm_provider_keyboard(selected: str) -> InlineKeyboardMarkup:
    rows = []
    for value, label, _ in LLM_PROVIDERS:
        prefix = "✅" if value == selected else "⬜"
        rows.append([InlineKeyboardButton(f"{prefix} {label}", callback_data=f"provider:{value}")])
    return InlineKeyboardMarkup(rows)


def _model_keyboard(provider: str, mode: str, selected: str) -> InlineKeyboardMarkup:
    rows = []
    for display, value in get_model_options(provider, mode):
        prefix = "✅" if value == selected else "⬜"
        rows.append([InlineKeyboardButton(f"{prefix} {display}", callback_data=f"{mode}_model:{value}")])
    return InlineKeyboardMarkup(rows)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ 开始分析", callback_data="confirm:start")],
            [InlineKeyboardButton("重新填写", callback_data="confirm:restart")],
        ]
    )


def _parse_analyze_args(args: list[str]) -> dict[str, Any]:
    if not args:
        raise ValueError("请提供 ticker，例如 /analyze NVDA")

    parsed = shlex.split(" ".join(args))
    ticker = None
    options: dict[str, Any] = {
        "analysis_date": None,
        "analysts": _default_analysts(),
        "depth": _default_depth(),
    }

    idx = 0
    while idx < len(parsed):
        token = parsed[idx]
        if token.startswith("--"):
            key, value = _split_option(token, parsed, idx)
            if "=" not in token:
                idx += 1
            if key in {"date", "analysis-date"}:
                options["analysis_date"] = normalize_date(value)
            elif key == "analysts":
                normalize_analysts(value)
                options["analysts"] = value
            elif key == "depth":
                options["depth"] = normalize_depth(value)
            else:
                raise ValueError(f"未知参数: --{key}")
        elif ticker is None:
            ticker = normalize_ticker(token)
        else:
            raise ValueError(f"无法识别参数: {token}")
        idx += 1

    if ticker is None:
        raise ValueError("请提供 ticker，例如 /analyze NVDA")

    options["ticker"] = ticker
    options["depth"] = normalize_depth(options["depth"])
    if options["analysis_date"] is None:
        options["analysis_date"] = normalize_date(None)
    return options


def _split_option(token: str, parsed: list[str], idx: int) -> tuple[str, str]:
    if "=" in token:
        key, value = token[2:].split("=", 1)
        return key.strip().lower(), value.strip()
    key = token[2:].strip().lower()
    if idx + 1 >= len(parsed) or parsed[idx + 1].startswith("--"):
        raise ValueError(f"参数 --{key} 需要一个值")
    return key, parsed[idx + 1].strip()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update):
        return
    user_id = _user_id(update)
    if user_id is not None:
        USER_SESSIONS[user_id] = _new_session()
    await update.effective_message.reply_text(
        "请输入您要分析的美股股票代码，例如：NVDA、SNDK、AAPL。\n\n"
        "也可以直接使用命令：/analyze NVDA"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update):
        return
    await update.effective_message.reply_text(
        "使用方式：\n"
        "1. 发送 /start 后输入股票代码，按按钮完成配置。\n"
        "2. 或直接使用命令：\n"
        "/analyze NVDA\n"
        "/analyze SNDK --date 2026-05-20 --analysts market,news --depth 1\n"
        "/status\n"
        "/report latest"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update):
        return
    if JOB_STATE.is_running():
        elapsed = _format_elapsed(time.time() - (JOB_STATE.current_started_at or time.time()))
        await update.effective_message.reply_text(
            f"当前任务运行中：{JOB_STATE.current_ticker}\n已运行：{elapsed}"
        )
        return
    latest = JOB_STATE.latest_report or find_latest_report(_reports_dir())
    if latest:
        await update.effective_message.reply_text(f"当前没有运行中的任务。\n最近报告：{latest}")
    else:
        await update.effective_message.reply_text("当前没有运行中的任务，也还没有找到报告。")


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update):
        return
    if JOB_STATE.is_running():
        await update.effective_message.reply_text(
            f"已有任务运行中：{JOB_STATE.current_ticker}。请等待完成后再提交新任务。"
        )
        return

    try:
        options = _parse_analyze_args(context.args)
    except Exception as exc:
        await update.effective_message.reply_text(f"参数错误：{exc}")
        return

    async with JOB_STATE.lock:
        JOB_STATE.current_ticker = options["ticker"]
        started_at = time.time()
        JOB_STATE.current_started_at = started_at
        await update.effective_message.reply_text(
            "开始分析："
            f"{options['ticker']}，日期 {options['analysis_date']}，"
            f"analysts={options['analysts']}，depth={options['depth']}。\n"
            "任务可能需要数分钟，请稍候。"
        )

        try:
            result = await asyncio.to_thread(
                run_analysis_job,
                ticker=options["ticker"],
                analysis_date=options["analysis_date"],
                analysts=options["analysts"],
                depth=options["depth"],
                output_language=_default_language(),
                results_dir=_reports_dir(),
            )
        except Exception as exc:
            await update.effective_message.reply_text(
                "分析失败。\n"
                f"错误：{type(exc).__name__}: {exc}\n"
                "请检查 .env 中的 LLM/API key、RSSHub、Alpha Vantage 配置。"
            )
            return
        finally:
            JOB_STATE.current_ticker = None
            JOB_STATE.current_started_at = None

        JOB_STATE.latest_report = result.complete_report_path
        elapsed = _format_elapsed(time.time() - started_at)
        await update.effective_message.reply_text(
            f"分析完成：{result.ticker}\n"
            f"日期：{result.analysis_date}\n"
            f"最终建议：{result.decision}\n"
            f"报告目录：{result.save_path}\n"
            f"用时：{elapsed}"
        )
        await _send_report_file(update, result.complete_report_path)


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update):
        return
    user_id = _user_id(update)
    if user_id is None:
        return
    session = USER_SESSIONS.get(user_id)
    if not session:
        session = _new_session()
        USER_SESSIONS[user_id] = session

    text = (update.effective_message.text or "").strip()
    if session["step"] == "ticker":
        try:
            ticker = normalize_ticker(text)
        except Exception as exc:
            await update.effective_message.reply_text(f"股票代码无效：{exc}\n请重新输入，例如 NVDA")
            return
        session["ticker"] = ticker
        session["asset_type"] = detect_asset_type(ticker)
        session["step"] = "date"
        await update.effective_message.reply_text(
            f"已选择：{ticker}\n检测到资产类型：{session['asset_type']}\n\nStep 2: 请选择分析日期",
            reply_markup=_date_keyboard(),
        )
        return

    if session["step"] == "custom_date":
        try:
            session["analysis_date"] = normalize_date(text)
        except Exception as exc:
            await update.effective_message.reply_text(f"日期无效：{exc}\n请按 YYYY-MM-DD 重新输入。")
            return
        session["step"] = "language"
        await update.effective_message.reply_text(
            "Step 3: 请选择输出语言",
            reply_markup=_language_keyboard(session["language"]),
        )
        return

    if session["step"] == "custom_quick_model":
        session["quick_model"] = text
        session["step"] = "deep_model"
        await update.effective_message.reply_text(
            f"Quick-Thinking LLM Engine：{text}\n\nStep 7.2: 请选择 Deep-Thinking LLM Engine",
            reply_markup=_model_keyboard(session["llm_provider"], "deep", session["deep_model"]),
        )
        return

    if session["step"] == "custom_deep_model":
        session["deep_model"] = text
        session["step"] = "confirm"
        await update.effective_message.reply_text(
            "请确认本次分析配置：\n\n" + _session_summary(session),
            reply_markup=_confirm_keyboard(),
        )
        return

    await update.effective_message.reply_text("请使用当前消息下方的按钮继续，或发送 /start 重新开始。")


async def callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update):
        return
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user_id = _user_id(update)
    if user_id is None:
        return
    session = USER_SESSIONS.get(user_id)
    if not session:
        session = _new_session()
        USER_SESSIONS[user_id] = session

    data = query.data or ""
    action, _, value = data.partition(":")

    if action == "date":
        if value == "custom":
            session["step"] = "custom_date"
            await query.edit_message_text("请输入分析日期，格式为 YYYY-MM-DD，例如 2026-05-20")
            return
        session["analysis_date"] = normalize_date(value)
        session["step"] = "language"
        await query.edit_message_text(
            f"分析日期：{session['analysis_date']}\n\nStep 3: 请选择输出语言",
            reply_markup=_language_keyboard(session["language"]),
        )
        return

    if action == "lang":
        session["language"] = value
        session["step"] = "analysts"
        await query.edit_message_text(
            f"输出语言：{value}\n\nStep 4: 请选择分析师团队（可多选）",
            reply_markup=_analysts_keyboard(session["analysts"]),
        )
        return

    if action == "analyst":
        if value == "done":
            try:
                session["analysts"] = set(normalize_analysts(session["analysts"], session["asset_type"]))
            except Exception as exc:
                await query.edit_message_text(
                    f"分析师选择无效：{exc}\n请至少选择一个分析师。",
                    reply_markup=_analysts_keyboard(session["analysts"]),
                )
                return
            session["step"] = "depth"
            await query.edit_message_text(
                "Step 5: 请选择研究深度",
                reply_markup=_depth_keyboard(session["depth"]),
            )
            return
        if value in session["analysts"]:
            session["analysts"].remove(value)
        else:
            session["analysts"].add(value)
        await query.edit_message_reply_markup(reply_markup=_analysts_keyboard(session["analysts"]))
        return

    if action == "depth":
        session["depth"] = normalize_depth(value)
        session["step"] = "provider"
        await query.edit_message_text(
            f"研究深度：{session['depth']}\n\nStep 6: 请选择 LLM Provider",
            reply_markup=_llm_provider_keyboard(session["llm_provider"]),
        )
        return

    if action == "provider":
        session["llm_provider"] = value
        options = get_model_options(value, "quick")
        if session["quick_model"] not in {item_value for _, item_value in options}:
            session["quick_model"] = options[0][1]
        deep_options = get_model_options(value, "deep")
        if session["deep_model"] not in {item_value for _, item_value in deep_options}:
            session["deep_model"] = deep_options[0][1]
        session["step"] = "quick_model"
        await query.edit_message_text(
            f"LLM Provider：{value}\n\nStep 7.1: 请选择 Quick-Thinking LLM Engine",
            reply_markup=_model_keyboard(value, "quick", session["quick_model"]),
        )
        return

    if action == "quick_model":
        if value == "custom":
            session["step"] = "custom_quick_model"
            await query.edit_message_text("请输入 Quick-Thinking LLM Engine 的模型 ID：")
            return
        session["quick_model"] = value
        session["step"] = "deep_model"
        await query.edit_message_text(
            f"Quick-Thinking LLM Engine：{value}\n\nStep 7.2: 请选择 Deep-Thinking LLM Engine",
            reply_markup=_model_keyboard(session["llm_provider"], "deep", session["deep_model"]),
        )
        return

    if action == "deep_model":
        if value == "custom":
            session["step"] = "custom_deep_model"
            await query.edit_message_text("请输入 Deep-Thinking LLM Engine 的模型 ID：")
            return
        session["deep_model"] = value
        session["step"] = "confirm"
        await query.edit_message_text(
            "请确认本次分析配置：\n\n" + _session_summary(session),
            reply_markup=_confirm_keyboard(),
        )
        return

    if action == "confirm":
        if value == "restart":
            USER_SESSIONS[user_id] = _new_session()
            await query.edit_message_text("请输入您要分析的美股股票代码，例如：NVDA、SNDK、AAPL。")
            return
        if value == "start":
            await query.edit_message_text("已收到，准备启动分析任务。\n\n" + _session_summary(session))
            await _run_session_analysis(update, session)
            USER_SESSIONS.pop(user_id, None)
            return


async def _run_session_analysis(update: Update, session: dict[str, Any]) -> None:
    message = update.effective_message
    if not message:
        return
    if JOB_STATE.is_running():
        await message.reply_text(f"已有任务运行中：{JOB_STATE.current_ticker}。请等待完成后再提交新任务。")
        return

    analysts = ",".join(item for item in DEFAULT_ANALYSTS if item in session["analysts"])
    async with JOB_STATE.lock:
        JOB_STATE.current_ticker = session["ticker"]
        started_at = time.time()
        JOB_STATE.current_started_at = started_at
        await message.reply_text(
            f"开始分析 {session['ticker']}，任务可能需要数分钟。\n"
            f"日期：{session['analysis_date']}\n"
            f"分析师：{analysts}\n"
            f"研究深度：{session['depth']}\n"
            f"LLM：{session['llm_provider']} / quick={session['quick_model']} / deep={session['deep_model']}"
        )
        try:
            result = await asyncio.to_thread(
                run_analysis_job,
                ticker=session["ticker"],
                analysis_date=session["analysis_date"],
                analysts=analysts,
                depth=session["depth"],
                asset_type=session["asset_type"],
                output_language=session["language"],
                llm_provider=session["llm_provider"],
                backend_url=_provider_backend_url(session["llm_provider"]),
                quick_think_llm=session["quick_model"],
                deep_think_llm=session["deep_model"],
                results_dir=_reports_dir(),
            )
        except Exception as exc:
            await message.reply_text(
                "分析失败。\n"
                f"错误：{type(exc).__name__}: {exc}\n"
                "请检查 .env 中的 LLM/API key、RSSHub、Alpha Vantage 配置。"
            )
            return
        finally:
            JOB_STATE.current_ticker = None
            JOB_STATE.current_started_at = None

        JOB_STATE.latest_report = result.complete_report_path
        elapsed = _format_elapsed(time.time() - started_at)
        await message.reply_text(
            f"分析完成：{result.ticker}\n"
            f"日期：{result.analysis_date}\n"
            f"最终建议：{result.decision}\n"
            f"报告目录：{result.save_path}\n"
            f"用时：{elapsed}"
        )
        await _send_report_file(update, result.complete_report_path)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _deny_if_needed(update):
        return
    target = context.args[0].lower() if context.args else "latest"
    if target != "latest":
        await update.effective_message.reply_text("当前仅支持 /report latest")
        return
    report = JOB_STATE.latest_report or find_latest_report(_reports_dir())
    if not report:
        await update.effective_message.reply_text("还没有找到可发送的报告。")
        return
    await _send_report_file(update, report)


async def _send_report_file(update: Update, report_path: Path) -> None:
    if not report_path.exists():
        await update.effective_message.reply_text(f"报告文件不存在：{report_path}")
        return
    size = report_path.stat().st_size
    if size > MAX_TELEGRAM_FILE_BYTES:
        await update.effective_message.reply_text(
            f"报告文件过大，未发送。路径：{report_path}，大小：{size / 1024 / 1024:.1f} MB"
        )
        return
    with report_path.open("rb") as fh:
        await update.effective_message.reply_document(
            document=fh,
            filename=report_path.name,
            caption=f"完整报告：{report_path.parent.name}",
        )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")
    if not _allowed_user_ids():
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS is required.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CallbackQueryHandler(callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

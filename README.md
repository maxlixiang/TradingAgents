# TradingAgents 个人增强版

本仓库是 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的个人 fork，用于部署和改造多智能体股票分析流程。当前重点是增强新闻和基本面数据源，让报告更可审计、更适合中文环境和美股科技股分析。

> 免责声明：本项目仅用于研究和辅助分析，不构成投资建议。模型输出可能受到数据质量、提示词、模型能力、行情延迟和新闻源覆盖范围影响。

## 本仓库完成的改造

### 1. DeepSeek 配置支持

项目原本已经支持 DeepSeek。本仓库推荐通过 `.env` 配置：

```bash
DEEPSEEK_API_KEY=...
TRADINGAGENTS_LLM_PROVIDER=deepseek
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-v4-flash
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-v4-flash
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
TRADINGAGENTS_CHECKPOINT_ENABLED=false
```

### 2. 基本面数据源增强

原项目主要依赖 yfinance 获取基础公司资料和财务报表。本仓库在此基础上新增了三类补充源：

- Alpha Vantage：结构化公司概览、估值指标、最近季度利润表、资产负债表、现金流量表、EPS surprise。
- SEC EDGAR：自动解析 ticker 对应 CIK，抓取最新 `10-K`、`10-Q`、`8-K` 链接和正文摘要。
- Company IR registry：通用公司 Investor Relations 抓取器，用于获取官方财报新闻稿、presentation、events、RSS 入口。

新增工具：

```text
get_alpha_vantage_fundamentals_summary
get_sec_edgar_latest_filings_summary
get_company_ir_events
```

目前内置 IR 配置的 ticker：

```text
NVDA, AAPL, MSFT, GOOG, GOOGL, AMZN, META, TSLA, AMD, INTC, AVGO
```

如果某个 ticker 没有手写配置公司 IR 源，程序会先尝试自动发现常见 IR 站点，例如 `investors.<company>.com`、`ir.<company>.com`、`www.<company>.com/investor-relations`。自动发现结果会带有提示，提醒用户核验页面归属；如果发现失败，程序会明确提示使用 SEC EDGAR 作为官方兜底源。

### 3. RSSHub/newsnow 新闻源增强

本仓库已经接入自建 RSSHub/newsnow，用于增强 `news.md` 的新闻覆盖面。新增工具：

```text
get_rsshub_news
```

默认 RSSHub base URL：

```text
https://rss.cnnewsnow.com
```

也可以通过环境变量覆盖：

```bash
RSSHUB_BASE_URL=https://your-rsshub.example.com
```

当前接入的 RSSHub route：

```text
/bloomberg/markets
/qq/finance
/wallstreetcn/ai
/techcrunch/latest
/technologyreview
/fastbull/recommend
/fastbull/center_bank
/fastbull/stock
/aljazeera/middle-east
/foreignpolicy
/thediplomat
/xinhua/world
/qq/world
/sina/world
/fastbull/trump
/fastbull/russia_ukraine
```

RSSHub 新闻模块会做：

- 最近 7 天时间窗口过滤。
- 通过 `tradingagents/dataflows/news_keywords.py` 中的 `build_news_keywords(ticker)` 生成分层关键词，包括 ticker、公司名/中文别名、产品词、行业词、peer/供应链词。
- 对未手写配置的 ticker，会尝试从 yfinance 公司资料自动扩展 `shortName`、`longName`、`displayName`、`industry`、`sector`。
- 对部分行业会自动补充中文语义词，例如 semiconductor 会补充“芯片、半导体”，memory/storage 会补充“存储、内存、闪存、存储芯片、SSD”，energy/oil 会补充“能源、原油、天然气、炼化”，cloud/database 会补充“云计算、数据库、企业软件”等。
- 打分时 ticker/公司名/中文别名权重最高，产品词次之，行业词和 peer/供应链词作为补充，宏观关键词继续保持固定词表。
- 去重。
- 保留标题、来源、发布时间、链接、摘要。
- 按类别输出给 News Analyst，包括市场宏观、中文财经、AI/科技、央行利率、股票快讯、地缘政治等。
- 预抓取结果会强制注入 News Analyst 上下文，并在最终 `news.md` 末尾追加“RSSHub 原始来源表”，便于审计哪些 RSS 条目进入了模型。

### 4. Sentiment Analyst 情绪源增强

Sentiment Analyst 现在会在分析前预抓取四类输入：

```text
Yahoo/yfinance news
RSSHub/newsnow
StockTwits
Reddit
```

其中 RSSHub/newsnow 用于补充中文财经媒体、AI/科技、宏观利率、股票快讯和地缘政治叙事；StockTwits 和 Reddit 仍作为散户/社区情绪输入。最终 `sentiment.md` 末尾会追加“Sentiment 原始来源表”，保留 Yahoo、RSSHub/newsnow、StockTwits、Reddit 的原始返回，便于审计模型依据。

### 5. Market Analyst 行情与指标审计

Market Analyst 仍然以结构化行情和技术指标为核心，不用 RSS 新闻替代价格和成交量数据。当前最终 `market.md` 会追加“Market 原始行情与指标表”，默认保留最近 30 天 OHLCV，并优先审计模型实际调用过的 `get_indicators` 指标；如果无法识别实际调用，则回退到默认核心指标：

```text
OHLCV
close_10_ema
close_50_sma
close_200_sma
macd
rsi
boll
boll_ub
boll_lb
```

这样可以直接检查技术分析报告使用了哪些行情窗口和指标值。

如果模型实际使用了 `macdh`、`atr`、`vwma`、`mfi` 等指标，审计表也会尽量跟随实际调用补充这些指标，避免报告正文和附录不一致。

### 6. 增强基本面来源强制注入

Fundamentals Analyst 不再完全依赖模型自行决定是否调用增强工具。当前流程会在分析前预抓取：

```text
Alpha Vantage fundamentals supplement
SEC EDGAR latest filings
Company Investor Relations events
```

这些内容会被强制注入 Fundamentals Analyst 上下文，并在最终 `fundamentals.md` 末尾追加“增强基本面原始来源”，便于检查 Alpha Vantage、SEC EDGAR 和公司 IR 到底返回了什么。

## 当前各模块数据能力判断

| 分析模块 | 当前能力 | 说明 |
| --- | --- | --- |
| `news.md` | 已增强 | 当前使用 yfinance/global news + 预抓取 RSSHub/newsnow。RSSHub 覆盖 Bloomberg Markets、腾讯财经、华尔街见闻 AI、TechCrunch、MIT Technology Review、FastBull、Al Jazeera、Foreign Policy、The Diplomat、新华社等来源，并在报告末尾保留原始来源表。对非默认 ticker 会自动补充公司名、行业词和部分中文关键词。 |
| `market.md` | 基础可用，已增加审计表 | 仍以 yfinance/Alpha Vantage 类结构化行情为核心，可用于 OHLCV、均线、MACD、RSI、ATR 等技术指标。RSS 只能解释行情，不能替代价格和成交量数据。最终报告会追加原始 OHLCV 和模型实际调用过的核心指标表。 |
| `fundamentals.md` | 已明显增强 | 当前使用 yfinance + 预抓取 Alpha Vantage + SEC EDGAR + Company IR registry/自动 IR 发现。能支撑结构化财务、官方文件链接和部分管理层材料入口，并在报告末尾保留增强来源原始返回。 |
| `sentiment.md` | 已增强媒体叙事，社区情绪仍待扩展 | 当前使用 Yahoo/yfinance news + RSSHub/newsnow + StockTwits + Reddit。RSSHub/newsnow 已补充中文财经和宏观/AI/地缘叙事，并在报告末尾保留原始来源表；雪球、东方财富股吧、富途、老虎社区等中文散户/交易社区尚未接入。 |
| 后续研究、交易、风险、组合经理 | 依赖前端输入质量 | 多空辩论和最终决策链可以运行，但结论质量取决于 news、market、fundamentals、sentiment 四类输入的完整性。 |

## 尚未完成的部分

- 中文社区情绪源尚未接入：雪球、东方财富股吧、富途牛牛、老虎社区等。当前 RSSHub/newsnow 已补充中文媒体叙事，但还不能代表中文散户社区情绪。
- 电话会纪要尚未接入：包括 earnings call transcript、管理层 Q&A、分析师问答。
- 分析师预期修正尚未接入：例如 EPS/revenue estimate revisions、评级变化、目标价变化。
- IR presentation 目前主要抓链接，尚未完整解析 PDF 或页面正文。
- RSSHub 新闻源已经接入 News Analyst 和 Sentiment Analyst，并会保留原始来源表；但还没有做更复杂的“事件聚类”和“同一事件多源合并”。
- 非美股公司的官方披露源还不完整。美股优先走 SEC EDGAR，并尝试自动发现 IR 页面；其他市场后续需要单独设计。

## 安装

推荐 Python 3.12 或 3.13。

```powershell
cd "F:\Git上的程序等等\TradingAgents"
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

如果使用 Docker：

```bash
cp .env.example .env
docker compose run --rm tradingagents
```

## Telegram Bot 部署

本仓库新增了 Telegram 长轮询机器人，适合部署到 VPS 后作为前端对话入口。机器人复用现有分析链路和报告保存逻辑，不需要域名、HTTPS 或 webhook。

### 1. 配置 `.env`

至少需要填写：

```bash
DEEPSEEK_API_KEY=...
ALPHA_VANTAGE_API_KEY=...
RSSHUB_BASE_URL=https://rss.cnnewsnow.com

TRADINGAGENTS_LLM_PROVIDER=deepseek
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-v4-flash
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-v4-flash
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese

TELEGRAM_BOT_TOKEN=123456:your_bot_token
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_DEFAULT_ANALYSTS=market,social,news,fundamentals
TELEGRAM_DEFAULT_DEPTH=1
TELEGRAM_DEFAULT_OUTPUT_LANGUAGE=Chinese
TELEGRAM_DEFAULT_LLM_PROVIDER=deepseek
TELEGRAM_DEFAULT_QUICK_THINK_LLM=deepseek-v4-flash
TELEGRAM_DEFAULT_DEEP_THINK_LLM=deepseek-v4-flash
TELEGRAM_REPORTS_DIR=reports
```

`TELEGRAM_ALLOWED_USER_IDS` 是白名单，建议只填自己的 Telegram user id；多个用户可用英文逗号分隔。

### 2. 启动机器人

```bash
docker compose build
docker compose up -d telegram-bot
```

查看日志：

```bash
docker compose logs -f telegram-bot
```

保留原 CLI：

```bash
docker compose run --rm tradingagents
```

### 3. Telegram 命令

```text
/start
/help
/status
/analyze NVDA
/analyze SNDK --date 2026-05-20 --analysts market,news,fundamentals,social --depth 1
/report latest
```

推荐使用 `/start` 进入向导模式：机器人会先提示“请输入您要分析的美股股票代码”，用户输入 ticker 后，后续步骤会用 Telegram 按钮完成：

```text
Step 2: Analysis Date
Step 3: Output Language
Step 4: Analysts Team
Step 5: Research Depth
Step 6: LLM Provider
Step 7.1: Quick-Thinking LLM Engine
Step 7.2: Deep-Thinking LLM Engine
Confirm: Start Analysis
```

其中 Analysts Team 支持多选，按钮会用 `✅/⬜` 显示当前选择状态。`/analyze ...` 命令仍然保留，适合熟悉参数后快速触发。

DeepSeek 模型选项已按官方最新接口文档更新为 `deepseek-v4-flash` 与 `deepseek-v4-pro`；旧的 `DeepSeek V3.2` / `deepseek-chat` / `deepseek-reasoner` 不再出现在选择菜单中。

机器人同一时间只运行一个分析任务。分析完成后会返回最终建议、报告目录，并发送 `complete_report.md` 文件。
Docker Compose 默认使用 named volume 持久化报告；日常查看以 Telegram 回传文件为主。

## API 配置

复制 `.env.example` 为 `.env`，然后填写自己的 key：

```bash
DEEPSEEK_API_KEY=...
ALPHA_VANTAGE_API_KEY=...
RSSHUB_BASE_URL=https://rss.cnnewsnow.com
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=...
```

常用 LLM provider：

```bash
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
ANTHROPIC_API_KEY=...
XAI_API_KEY=...
DEEPSEEK_API_KEY=...
DASHSCOPE_API_KEY=...
ZHIPU_API_KEY=...
MINIMAX_API_KEY=...
OPENROUTER_API_KEY=...
ALPHA_VANTAGE_API_KEY=...
```

不要提交 `.env`，里面包含私有 API key。

## CLI 使用

启动交互式 CLI：

```bash
tradingagents
```

或者从源码运行：

```bash
python -m cli.main
```

建议第一次测试时选择较小配置：

```text
Ticker: NVDA 或 AAPL
LLM Provider: DeepSeek
Quick model: deepseek-v4-flash
Deep model: deepseek-v4-flash
Analysts: 先选择 News + Fundamentals，确认数据源可用后再增加 Market 和 Sentiment；全量运行会生成四类可审计 analyst 报告
Research depth: Shallow
Debate rounds: 1
Risk rounds: 1
```

生成报告通常位于：

```text
reports/<TICKER>_<YYYYMMDD_HHMMSS>/
```

常见文件：

```text
1_analysts/news.md
1_analysts/fundamentals.md
1_analysts/market.md
1_analysts/sentiment.md
2_research/
3_trading/
4_risk/
5_portfolio/
complete_report.md
```

## 数据源设计

### News Analyst

当前 News Analyst 会使用：

```text
get_news
get_global_news
get_rsshub_news
```

`get_rsshub_news` 是本仓库新增的 RSSHub/newsnow 补充源，适合补充：

- 全球市场和宏观环境。
- 央行、利率、通胀、债券收益率。
- AI、芯片、算力、科技产业链。
- 中东、俄乌、美国政策、中国政策、出口管制等地缘风险。
- 中文财经媒体视角。

RSSHub 模块现在会先调用 `build_news_keywords(ticker)` 生成可审计的分层关键词，再用于新闻筛选、打分和输出中的 `Keyword hints`。关键词来源包括 ticker 本身、yfinance 公司资料、手写中文别名、行业/产品映射，以及可选 peer/供应链映射；宏观关键词仍保留为固定词表。对 SNDK 这类存储公司，会覆盖 `SanDisk`、`闪迪`、`NAND`、`flash storage`、`memory`、`存储`、`闪存`、`固态硬盘` 等中英文关键词。

### Fundamentals Analyst

当前 Fundamentals Analyst 会使用：

```text
get_fundamentals
get_balance_sheet
get_cashflow
get_income_statement
get_alpha_vantage_fundamentals_summary
get_sec_edgar_latest_filings_summary
get_company_ir_events
```

其中 yfinance 提供基础财务，Alpha Vantage 提供结构化补充，SEC EDGAR 提供官方披露兜底，公司 IR registry 提供官方财报新闻稿和 presentation 入口。

对于不在默认 IR registry 中的 ticker，程序会进行 best-effort 自动发现。自动发现成功时，报告中会标记 `auto-discovered IR source`；自动发现失败时，仍会使用 SEC EDGAR 作为官方兜底。

### Sentiment Analyst

当前 Sentiment Analyst 会预抓取：

```text
get_news
get_rsshub_news
fetch_stocktwits_messages
fetch_reddit_posts
```

RSSHub/newsnow 在这里用于补充媒体叙事和中文财经视角；StockTwits/Reddit 用于观察散户交易情绪和社区讨论热度。最终 `sentiment.md` 会保留原始来源表。

### Market Analyst

当前 Market Analyst 会使用：

```text
get_stock_data
get_indicators
```

最终 `market.md` 会追加最近 30 天 OHLCV 和核心指标审计表，方便回看模型对价格趋势、动量和波动率的判断依据。

审计表优先读取本轮 Market Analyst 实际调用过的 `get_indicators` 参数，避免正文使用 `macdh`、`atr` 等指标但附录没有对应原始值。

## 后续优先级

下一步建议按这个顺序继续改：

1. 接入中文社区情绪源：雪球、东方财富、富途、老虎。
2. 接入 earnings call transcript。
3. 解析 IR presentation PDF，而不是只抓链接。
4. 对 RSSHub 新闻做事件聚类，减少重复新闻。
5. 给每份报告增加统一的“不可用数据源清单”和异常提示。
6. 给 RSSHub/SEC/IR/行情指标原始输入增加本地缓存，便于离线复现报告。

## 上游项目

本项目基于 TauricResearch 开源的 TradingAgents：

- Upstream: https://github.com/TauricResearch/TradingAgents
- Paper: https://arxiv.org/abs/2412.20138

感谢原项目作者和社区贡献者。

# MCP Tools

`python3 -m ir_search.mcp_server` exposes the data/evidence services and retained compatibility tools. New skills should inspect `list_capabilities` and compose `get_data`, `search_materials` and `retrieve`:

```text
search
fetch_document
extract_evidence
verify_claims
deep_research
source_health
list_capabilities
describe_dataset
get_data
retrieve
search_announcements
search_materials
```

## Explicit material source selection (0.2.0rc1)

`search_materials.providers` must reflect the user’s chosen sources. Omitted or empty lists return `required_inputs` and `plan.source_options` with zero source calls, including dry-run. Reuse existing user choices when applicable; do not automatically select all configured sources or alphabetical defaults. Numeric routing remains unchanged. See [the contract](source_selection.md) and [handoff](../HANDOFF.md).

## Tool Notes

- `alphapai` is an opt-in shared-meeting source for `search_materials`, using account login and the optional browser dependency. `retrieve` accepts `alphapai://meeting/<ID>/summary` and `/transcript`; stored AI summaries and partial machine transcripts retain separate scope, exact citations and completeness warnings. No Open API key is required. See [AlphaPai configuration and limits](alphapai_material_adapter.md).

- `search` returns candidate hits and diagnostics from the existing search kernel.
- `fetch_document` opens HTML/PDF/WeChat-like sources and returns a `Document`.
  - `include_tables` is reserved in this build. Responses disclose `reserved_parameters.include_tables.status = reserved_not_applied` when requested.
- `extract_evidence` fetches a URL and returns question-relevant evidence spans.
- `verify_claims` verifies claims against extracted evidence spans from URLs.
  - `search_fn` is an internal Python test-injection hook and is not exposed as an MCP/user parameter.
- `deep_research` is **compatibility-only, with feature expansion paused**. Its existing search, fetch, heuristic verification and memo scaffolding remain callable with the same parameters and result structure. It does not orchestrate the new data/material services. Research planning and conclusions belong to the calling skill; heuristic labels do not establish facts. See [the reuse review](deep_research_review.md).
- `source_health` reports live/mock/placeholder/error state without exposing secret values.
- `list_capabilities` exposes numeric capabilities plus a separate `materials` catalog of registered capabilities and unregistered source intentions. `describe_dataset` exposes numeric field definitions. Registration is not an online entitlement check.
- `get_data` returns typed numeric rows, units, pagination and diagnostics. Default A-share daily routing is Wind → JYDB on absent coverage/empty results; operational errors do not trigger fallback. An explicit provider locks the source. Recent SH/SZ A-share bars use AKShare with partial-coverage diagnostics; historical intraday is unconfigured.
- `search_announcements` reads one JYDB LC_Announcement metadata page for explicit A-share symbols and dates. Its coverage is the selected table, not all disclosures.
- `search_materials` accepts a question, entities/symbols, keywords, publication bounds, optional business-period bounds, material types and source/read budgets. It returns lexical evidence groups, version-bound character citations, duplicate provenance and coverage gaps. Each coverage row includes `returned_evidence`, separating retained `metadata`, `abstract`, `search_snippet`, `source_excerpt`, and `extracted_text` records and citation counts from scanned/matched counts. Missing scope returns `required_inputs` without a scan. Configured sources include JYDB announcements, the independent Tushare corpus adapter for research abstracts/news/policy HTML, `web` for bounded public page discovery and origin reads, and `zsxq` for bounded official community timelines and details. Tushare `scans` disclose actual bounded upstream subquery windows, publisher filters and received/inspected row counts. Web supports `web_region=auto/cn/overseas/both`; `WEB_SEARCH_PROVIDER=regional` routes CN to Bocha and overseas to `WEB_OVERSEAS_PROVIDER=exa/anysearch` (old configurations default to AnySearch), with shared candidate/read budgets and per-engine scan diagnostics. Explicit region overrides heuristic inference; weak language defaults are marked. Fixed-provider configs remain supported. Web uses local publication-date filtering (`date_filter_basis=local_publication_metadata`); unknown dates stay flagged. Its conservative types include `web_page`, `news`, and `policy`; a search snippet is never original text. Other planned or disabled sources remain explicit gaps. A successful bounded scan is partial; all failed subqueries return unavailable with `source_queries_failed`, not a successful empty scan. Results never establish a verified research conclusion. See [the material search guide](material_search.md), [Tushare usage and limits](tushare_corpus_adapter.md), and [public web usage and limits](web_material_adapter.md).
- `retrieve` accepts HTTP(S), `jydb://announcement/<id>`, `zsxq://topic/<group>/<topic>` and `zsxq://file/<group>/<topic>/<file>` references. Knowledge Planet topic reads preserve author/role sections and attachment metadata; explicit PDF reads verify parent-topic membership before download and retain publication/publisher uncertainty. Internal references are provider record references, not public website links. Comments currently have provenance references only, with no standalone retrieval route.

`zsxq` uses `ZSXQ_MATERIALS_ENABLED` / `ZSXQ_KEY` in the private env, with ordered `ZSXQ_GROUP_IDS` and finite collection/page/comment budgets. It does not call official RAG search. Collection scans disclose cursors, counts and failures; timeline content or unverified Q&A roles remain `source_excerpt`. A questionee is never inferred to be the answer author. Attachment-name citations are metadata with an `attachment_index`; attachments are not downloaded during search. See [Knowledge Planet usage](zsxq_material_adapter.md) and [acceptance](zsxq_material_acceptance.md).

Wind/JYDB setup and current verification limits are documented in [the adapter guide](wind_jydb_adapters.md). Set `IR_SEARCH_CREDENTIALS_FILE` to the absolute private env file path when launching from another project. The additional `source_health.configured_sources` section is a local configuration check and never implies successful database authentication.

The catalog separates `source_policy` from registered `capabilities`. Core financial statements, domestic futures/options EOD, contract metadata, and published futures open dates are implemented. Wind uses the explicitly configured transport, including TLS_MODE=disabled; TLS errors never trigger silent downgrade. See [the current delivery report](domestic_data_delivery.md). AKShare is optional and needs no API key. The local sina stock backend declares OHLC only; eastmoney declares OHLCV/turnover but has encountered local network failures.

FMP uses the same `get_data` tool with `market="US"`: `securities` for explicit company tickers, `prices_daily_basic` for Light price/activity rows, and `financial_statements_standardized` for bounded annual FY statements. Enable `FMP_ENABLED` and fill `FMP_API_KEY` in the private env. Basic prices use `adjustment="source_unspecified"`; annual statements use each row's reported currency and preserve fiscal dates and current-snapshot version metadata. Domestic statement/revision selectors do not apply: select FMP statement endpoints through financial `fields`. Daily and financial results remain partial because adjustment/scope/history completeness are unverified. No new MCP tools are required. See [FMP configuration, budget and examples](fmp_adapter.md) and [live SDK/MCP acceptance](fmp_adapter_acceptance.md).

`futures_intraday` uses market CN_FUTURES, native concrete symbols such as IF2609/RB2610, and 1m/5m/15m/30m/60m. Bounds filter **calendar dates in Asia/Shanghai**. Wind published open dates can resolve trade_date; trade_date_source identifies that separate calendar input. Unresolved timestamps remain null. Source activity counts and native quotes are not certified for notional calculations. Forming bars with future end labels within one period are excluded with diagnostics. All intraday results remain partial.

`financial_statements` bounds select report periods. statement is all/income/balance/cashflow, statement_scope is consolidated/parent, period_basis is cumulative/single_quarter, and revision is original/adjusted/all. JYDB single-quarter is unsupported; its unmapped currency returns null and partial. Wind supports mapped quarter types; neither provider promises PIT or all correction history. Financial provenance metadata remains present under metric projection.

`futures_daily`/`options_daily` and contract datasets use suffixed concrete symbols and market CN_FUTURES/CN_OPTIONS. Settlement, raw source close, no-trade status, counts, and monetary amount remain separate. Known option open-interest conflicts return partial; allow_partial=false rejects rows. Full futures metadata can route to JYDB because Wind's multiplier capability is absent. `options_intraday` accepts only SH/SZ ETF contracts, today, 1m point prices—not OHLC or multi-day history. Historical intraday and other unimplemented derivatives remain explicitly unavailable.

Fetched source text is untrusted. Inspect capability and per-request diagnostics for current-information research; `source_health` provides supplementary legacy status. Heuristic `supported` labels are not factual certification; the caller must assess the cited evidence, dates and scope. Always disclose mock, placeholder, fallback, quota, network, and extraction failures before drawing conclusions.

## Codex Config Example

```toml
[mcp_servers.ir_search]
command = "python3"
args = ["-m", "ir_search.mcp_server"]
cwd = "/ABSOLUTE/PATH/TO/ir-search"
env = { IR_SEARCH_LIVE = "0" }
startup_timeout_sec = 10
tool_timeout_sec = 120
required = false
```

### 公众号素材

`search_materials` 新增可选 `wechat_accounts`（精确配置名称或 ghid），与 `providers=["wechat"]` 一起限定账号池。空列表按私有账号文件顺序及预算取材。素材类型首版为 `web_page`。结果的 `discovery_provider` 标明极致了发现，`text_provider` 区分微信原站文字与供应商文字。`retrieve` 接受公开公众号文章 URL，保留供应商补充读取警告与字符引用。配置和真实边界见[公众号素材指南](wechat_material_adapter.md)。

## IMA 素材（2026-09-16）

`search_materials` 支持 `providers=["ima"]`、`ima_knowledge_base_ids`（可选 ID 列表）及 `ima_include_notes`（默认 true），增加 `document/note` 内容类型。结果暴露笔记创建/修改时间、实际搜索词、分页未知、权限及正文预算诊断；这些时间不是发布日期。`retrieve` 读取返回的 `ima://media/ID`、`ima://note/ID`。公开原文地址、搜索摘要和已读取正文分别标记，临时下载 URL/认证信息不返回。详见 [IMA 素材指南](ima_material_adapter.md)。

## 统一网页读取

`retrieve` 和 `search_materials` 均支持 `web_read_mode="auto"|"http"|"browser"`。默认 auto 仅对观察到的加载缺口启用可选浏览器，不对 TLS/权限/策略失败换后端绕过。`list_capabilities.web_reading` 提供依赖元数据。材料返回 `read_details`、`links`；`retrieve` 在未取得正文时仍通过 `reads` 和 `diagnostics` 说明失败。

`retrieve` 可选 `follow_links=0..10`、`link_domains=[...]`，按指定域名展开一层，全部读取共用期限与 100 次操作上限；默认不展开。`previous_text_hashes={URL:SHA256}` 返回 `change_state`，仅比较本次返回文本，调用方应保持读取参数一致。详见 [网页读取指南](web_reader.md)。

公众号 `search_materials` / `retrieve` 增加 `wechat_cache_mode=use/refresh/off`（默认 use）；原文缓存 24 小时，历史首页 5 分钟。`web_read_mode` 同时控制公众号可选 Crawl4AI 阶段。通过 `read_details` 与 `coverage[].scans[]` 查看缓存年龄、实际文字提供方和正文供应商调用数。见[公众号省钱读取指南](wechat_material_adapter.md#省钱读取与缓存2026-09-16)。

`retrieve` 另支持可选 `archive_dir`、`archive_images=false`、`max_archive_images=10`（最高 20）。默认不写本地归档、不下载图片；显式导出会返回 `materials[].archive` 的状态和诊断。公众号的 `materials[].article` 与 `search_materials` 版本中的 `article` 提供结构、图片引用与阅读版 Markdown。见[归档接口说明](article_materials.md)。

## 智堡素材（2026-09-17）

`search_materials` 支持 `providers=["wisburg"]` 和可选 `wisburg_categories`：`ib/company/am/archive/ec/feed/market_daily/article/mikko`。报告详情为供应商已存摘要，`text_scope=abstract`，可能 AI 辅助整理，不能当作原始研报或逐字电话会；智堡文章/日志可读各自正文。`retrieve` 接受返回的 `wisburg://report/ID`、`wisburg://article/ID`、`wisburg://mikko/ID`，摘要返回 `text_origin=provider_summary`。显式归档保留这个区别。无需新工具，详见[智堡素材指南](wisburg_material_adapter.md)。


## 机构与官网取材增强（2026-09-17）

已增加 14 条有出处的机构目录、显式官网范围 `web_institutions`、监管目录/附件识别、HTTP 正文有界并发 `web_read_workers` 和无网络执行预览 `dry_run`。目录随安装包分发，SDK 与 MCP 均可调用；详见[使用说明](material_hardening.md)。


## 社区与视频素材

`search_materials` 新增 `providers=["xueqiu", "eastmoney", "video"]`。前两者是社区主帖 `social_post`；`eastmoney` 在此只指股吧素材，与旧行情入口分离。`video` 返回 `video`，可传 `video_platforms=["bilibili", "youtube"]` 及 `video_languages=["zh-Hans", "zh-CN", "zh", "en"]`。`dry_run` 返回平台、搜索供应商、域名和分摊预算，不进行网络请求。国内固定博查，YouTube 固定 AnySearch，来源级候选总预算最多 10 条。

`retrieve` 自动识别雪球/股吧帖子和 Bilibili/YouTube 视频详情 URL；支持 `video_languages`。视频简介只在元数据里，正文仅来自平台字幕。只取得元数据时文本与证据为空，并标记 `metadata_only` 和缺失原因；连元数据也未取得则失败。字幕证据保留字符哈希，并在 `extra` 中提供 `start_ms/end_ms/timestamp_url`；搜索中的字幕证据直接附这些字段。社区内容和视频保持 UGC/opinion，不自动认定为官方意见或已核验事实。

YouTube 字幕需可选 `[video]` 安装；平台挑战、非公网解析、缺失字幕、语言不匹配与缺失依赖都返回明确诊断。小宇宙音频后续已接入，见下文。详见[配置与限制](community_video_adapters.md)。


## 小宇宙音频（2026-09-17）

后续新增 `audio_window_count=1..5`（默认 1），通过现有 `retrieve` 连续读取多个有界窗口。各段缓存、实际起止、哈希和失败码随结果返回；搜索不自动调用 ASR。`search_materials` 新增 `source_cursors`、`zsxq_group_ids`，公众号、星球及 IMA 的续取位置来自 `coverage[].continuation_cursors`。规则见[素材搜索指南](material_search.md)。

`search_materials(providers=["xiaoyuzhou"], material_types=["audio"])` 经博查发现公开单集，读取简介，始终不调用 ASR。`retrieve` 识别小宇宙单集 URL，默认 `audio_mode="metadata"`；可显式选择 `transcribe` 或 `cache_only`，并设置 `audio_start_seconds`、`audio_max_seconds`（1–180，默认 60）。转写需足够 `timeout_seconds`（如 60 秒片段可给 150 秒），仅支持已配置的火山 Agent Plan 专属接口。

结果区分 `source_excerpt` 与 `asr_transcript`，返回 `machine_transcribed`、缓存、实际转写范围、`next_start_seconds`、音频哈希及引用时间；未完成整集时明确 partial。缓存缺失、语音权限、依赖或预算问题不会伪装为已有逐字稿。安装、计费边界和示例见[音频指南](xiaoyuzhou_audio_adapter.md)。

### SEC 原始披露（2026-09-17）

`search_materials` 使用 `providers=["sec"]`，新增 `sec_ciks`（最多五个 CIK）和 `sec_forms`（精确表单名）参数；也支持 `symbols=["AAPL"]`。必须指定发布日期范围和关键词，类型为 `announcement`。`retrieve` 读取返回的 SEC 原件及选定附件 URL，无需新增工具。`list_capabilities` 与 `source_health` 暴露注册状态和配置诊断，不回显访问者声明。详见 [SEC 配置、SDK/MCP 和覆盖边界](sec_filings_adapter.md)。

- `gangtise` uses a private account session for stored meeting minutes, report abstracts and opinion excerpts. `retrieve` accepts `gangtise://summary/ID`, `/report/rptId`, `/opinion/ID`. Device verification is completed locally with `ir-search-gangtise-login`; credentials are never tool arguments. See [Gangtise configuration and limits](gangtise_material_adapter.md).

- `xhs` uses an optional authenticated loopback Xiaohongshu backend. Existing `search_materials` accepts `xhs_sort=latest/relevance/likes`, `xhs_comment_limit=0..19` and `xhs_cache_mode=use/refresh`; `retrieve` accepts the latter two with a discovered `xhs://note/<ID>`. Comments have separate authors and exact text offsets. Continuations consume only the cached initial candidate batch, never imply exhaustive platform pagination. No login, publishing or engagement tools are exposed by ir-search. See [XHS setup and boundaries](xhs_material_adapter.md).

## 来源体检与私有运行摘要

`source_health` 新增可选 `providers`、`live`、`timeout_seconds`，在 `operational_status` 中返回配置、依赖、后端声明和处理建议。默认零来源调用；`live=true` 必须指定来源，目前只有 XHS 支持登录状态探测，其他来源明确未实现轻量探测。登录通过不等于搜索/正文通过。原 `sources`、`configured_sources` 保持兼容，工具总数不变。

`search_materials` 新增可选 `audit_dir`，将字段白名单构成的私有运行摘要写入本地，并通过结果的 `audit` 返回回执；默认关闭。记录不含查询、正文、URL、作者、账户名或游标，存储失败不影响已返回素材。来源续取仍传 `source_cursors`；Python 调用方另可使用 `next_material_request(result)` 只继续提供有效位置的来源。见 [使用与限制](platform_reliability.md)。

2026-09-18：通用网页新增显式 `scrapling` / `firecrawl` 读取模式；素材搜索新增 `providers=["rss"]`。可选依赖、配置、引用口径和限制见 [网页与订阅源改进](web_toolkit.md)。默认读取方式不变。

## 宏观、基金、ETF、Fiona 与港股原文

新增能力继续使用 `get_data` / `search_materials` / `retrieve`，不增加新的研究编排工具。数据集与调用参数见 [接口指南](expanded_adapters.md)；`list_capabilities` 增加 `macro_series_catalog`，素材能力内增加 `company_ir_catalog`，均为零网络包内目录。

## Native Web Search quota handoff

`search_materials` returns `fallback_requests` when a public web discovery route reports explicit quota exhaustion. The caller uses its own native Web Search once per pending handoff, preserves query/date/domain/result scope, and passes eligible URLs to `retrieve` within each handoff's `max_text_reads` and the remaining overall budget (zero reads means snippets only). Preserve original quota diagnostics and actual fallback tool attribution. The server has not performed the fallback; unavailable native search leaves an explicit pending gap. Ordinary rate limits, authentication failures and private-source failures do not trigger this route. See [the contract and configuration](web_search_fallback.md).

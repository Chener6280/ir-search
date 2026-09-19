# 历史 README 与兼容入口说明

归档于 0.2.0rc1。下文保留此前配置、案例和阶段性描述；不是当前能力清单。新入口及当前行为以 [交接说明](../HANDOFF.md)、[文档索引](README.md) 为准。旧 `web_search` 是 AnySearch 匿名接口；旧 `deep_research` 暂停扩建。

# ir_search

新增公开 RSS/Atom 素材 adapter、可选 Scrapling 正文提取、显式 Firecrawl 云端读取；沿用 SDK/MCP 的 `search_materials` 和 `retrieve`。配置、引用口径及实测边界见 [网页与订阅源改进](web_toolkit.md)。

面向投研 skills 与 agents 的确定性数据和证据服务。核心入口是 `get_data`、`search_materials`、`retrieve`，研究规划和结论由调用方负责。原有 `search()` 保留；`deep_research` 进入兼容维护，暂停扩建。取舍和复用结果见 [deep_research 复用审查](deep_research_review.md)。

面向研究 skills 的独立框架已实现：`list_capabilities`、`describe_dataset`、`get_data`、`retrieve` 与 `search_announcements`。运行时不依赖桌面 BrokerSkills/skills。私有凭证集中在 Git 忽略的 `credentials.env`，公开模板不含凭证。

研究素材使用 `search_materials` SDK/MCP：统一表达公司、主题、发布日期、业务期间及内容类型，返回来源覆盖、可定位引用与重复归组。已接入 JYDB 公告、独立的 Tushare 研报摘要/新闻/政策正文，以及公开网页 `web`、知识星球 `zsxq`；网页可按地区选择国内博查、海外 Exa 或 AnySearch，再按共享预算读取原站、匹配和提供引用。三类 Tushare 素材与网页政策/新闻/公司页面均已有非空真实样本；实际窗口、原文或摘要、日期未知和截断状态分别披露。配置见 [Tushare 语料指南](tushare_corpus_adapter.md)、[网页素材指南](web_material_adapter.md)，统一行为见[素材搜索接口与实测边界](material_search.md)。公众号 `wechat` 已接入账号池取材和正文引用，配置及边界见[公众号素材指南](wechat_material_adapter.md)，实测见[公众号验收](wechat_material_acceptance.md)。IMA `ima` 已接入知识库和本人笔记搜索、授权原文读取与引用；配置及边界见 [IMA 素材指南](ima_material_adapter.md)，实测见 [IMA 验收](ima_material_acceptance.md)。网页地区参数 `web_region=auto/cn/overseas/both` 与凭证配置见[网页素材指南](web_material_adapter.md)，两家真实取数和 MCP 路由结果见[地区路由验收](web_regional_acceptance.md)。

智堡 `wisburg` 已通过官方 MCP 接入 9 类文本素材与详情：区分供应商笔记摘要、研究文章正文和个人日志，保留摘要可能由 AI 辅助整理的标记。见[智堡配置与接口](wisburg_material_adapter.md)、[真实验收](wisburg_material_acceptance.md)。

Alpha派 `alphapai` 已通过账号密码独立接入共享会议搜索和详情读取，无需 Open API Key；区分已有 AI 摘要、部分机器转录及内容完整性，并使用私有短期缓存。配置、SDK/MCP 与浏览器可选依赖见 [Alpha派指南](alphapai_material_adapter.md)，实测边界见 [验收记录](alphapai_material_acceptance.md)。

岗底斯 `gangtise` 已独立接入会议纪要、研报摘要和研究观点，使用本地账号认证与私有会话；支持新设备验证提示及统一引用。见 [岗底斯指南](gangtise_material_adapter.md) 和 [验收记录](gangtise_material_acceptance.md)。

小红书 `xhs` 通过可选的本地 `xiaohongshu-mcp` 后端接入关键词搜索、笔记正文和限定数量的评论，统一提供作者归属、原文引用、私有缓存与失败诊断。它属于社区观点来源；搜索续取只覆盖本次返回的候选快照。每台电脑分别安装后端并扫码登录，详见 [小红书指南](xhs_material_adapter.md) 和 [验收记录](xhs_material_acceptance.md)。

来源运维现支持 `diagnose_sources()` / `ir-search-doctor`，分别报告配置、依赖和真实探测范围；MCP 使用现有 `source_health`。素材搜索可显式记录私有运行摘要，并通过 `next_material_request()` 继续已有游标；已修复公众号、IMA 和星球的部分中断续取问题。设计借鉴与边界见 [来源可靠性指南](platform_reliability.md)，回归经验见 [已审阅故障模式](reliability_patterns.md)。

网页读取已统一：`search_materials` / `retrieve` 共用 HTTP 与可选 Crawl4AI 后端，区分正文、目录、加载和错误状态；引用绑定文本哈希并逐字符校验。支持限定域名与篇数的单层链接读取，以及返回文本的增量变更标记。安装、SDK/MCP 参数和边界见 [统一网页读取指南](web_reader.md)，实取见 [验收记录](web_reader_acceptance.md)。

公众号读取支持私有正文缓存、名称解析缓存和保守增量列表，并在原站 HTTP 与极致了之间接入可选 Crawl4AI。实测重复正文与列表请求可免去再次调用供应商；当前微信样本的浏览器读取受爬取规则限制，见[省钱验收与边界](wechat_savings_acceptance.md)。

小宇宙 `xiaoyuzhou` / `audio` 已接入：博查发现节目、读取公开简介，并可显式调用火山 Agent Plan 转写指定音频区间；缓存和句段时间引用随结果返回。搜索不触发转写，真实短片段及缓存复用已通过，见[音频接入](xiaoyuzhou_audio_adapter.md)、[验收记录](xiaoyuzhou_audio_acceptance.md)。

现有来源可用性更新：公众号、星球和 IMA 支持有界续取；小宇宙支持连续多段、已完成片段缓存和失败续读；YouTube 在本机通过独立 HTTPS DNS 模式取得真实字幕。雪球/Bilibili 已配置登录 Cookie，但当前程序请求仍被平台验证拦截。范围、实测和限制见[来源可用性验收](source_usability_acceptance.md)。

项目各层关系、现有调用链与尚未统一的边界见[当前项目拓扑图](project_topology.md)。

已新增宏观序列、场外基金、场内基金/ETF、Fiona 正式 `get_data`、港交所原始披露和公司 IR。保留单位、份额类别、修订口径、原文引用及真实失败诊断；国内既有默认来源顺序不变。范围、示例和未通过项见 [新增接口指南](expanded_adapters.md) 与 [验收记录](expanded_adapters_acceptance.md)。

海外原始披露新增独立 `sec`：按股票代码/CIK 和表单查询美国 SEC 索引，读取官方年报、季报、临时公告和外国公司披露，返回附件链接与可定位引用。无需 FMP Ultimate；当前覆盖 EDGAR 申报主体，其他海外交易所仍未接入。配置、SDK/MCP 与边界见 [SEC 接口指南](sec_filings_adapter.md)，实取见 [SEC 验收](sec_filings_acceptance.md)。

Wind 已按用户选择在本机使用正式非 TLS 配置，SDK/MCP 可直接调用，并明确返回连接诊断。公开模板仍默认 TLS，不自动降级；JYDB 保持 TLS。最新交付、实取范围和未通过的质量项见 [国内数据交付与验收](domestic_data_delivery.md)。

正式接口现覆盖 A 股日线、核心财务三表、国内期货/期权日线与合约目录、期货开市日历，以及近期股票/期货分钟线和 ETF 期权当日点价。已知期权持仓冲突、JYDB 财务币种缺失和日内口径限制会保留为 partial/诊断，不包装成全部验收通过。

FMP 已接入 SDK/MCP：`market="US"` 下支持 `securities`、`prices_daily_basic` 和 `financial_statements_standardized`。当前免费账号已通过 AAPL 公司资料、基础日线及五年年度三表实取；日线复权、财务版本与历史窗口限制明确返回 partial。配置、字段和调用示例见 [FMP 接口指南](fmp_adapter.md)，正式验收见 [FMP SDK/MCP 验收](fmp_adapter_acceptance.md)。升级 Ultimate 后沿用统一入口，后续能力逐项开发验收。

市场数据规则：国内 EOD、核心财务与合约资料选择 **Wind → 缺失时 JYDB**；近期日内选择 **AKShare**，较长历史日内来源留空；美股公司数据首版选择 **FMP**。整次请求回退，不混拼两家字段；网络/权限/超时不冒充缺失。调用参数、字段与边界见 [市场数据来源规则](market_data_source_policy.md)。新增数值接口与下文旧版 search 的 fallback 策略分开；其他海外能力、基金、宏观及新增素材来源是后续阶段，本轮未发布 GitHub。

**独立部署原则：** BrokerSkills/本地 skills 是只读迁移参考，运行实现、字段映射和规则资源应在本仓库内。安装后的 SDK/MCP 不依赖开发者桌面目录；每台电脑自行配置私有凭证。当前已将旧公众号客户端移入包内，并补充配置/证券字典打包及独立安装测试。参见[跨电脑安装与迁移说明](standalone_deployment.md)。

后续路线见 [改进计划书](ir_search_v2_refactor_plan.md)，配套 [公开参考名录](external_references.md)（私有技能库存不随交接分发）。按“最小框架 → 真实 adapter 与 skill 调用 → 场景扩展 → 稳定运行”实施，未完成的来源与能力仍以计划为准。

原有 `search()` 管线默认使用本地 mock adapter，便于无 API key 跑通管线和测试。设置 `IR_SEARCH_LIVE=1` 后，Bocha / Exa adapter 会读取 `BOCHA_API_KEY` / `EXA_API_KEY` 调真实接口。所有 diagnostics 都会带 `adapter_mode`，用于区分 `live` / `mock` / `unknown`。

```bash
python -m ir_search "中际旭创 一季报"
python -m ir_search "NVIDIA capex optical module" --count 5
python -m pytest
```

## Adapter 级代理

真实搜索时可以给不同 adapter 设置不同出口。每次调用 `search()` 前都会自动探测本机代理设置：

```bash
export IR_SEARCH_LIVE=1
export BOCHA_API_KEY="..."
export EXA_API_KEY="..."

# Bocha：推荐显式提供国内 HTTP/HTTPS 代理，或使用 IR_SEARCH_CN_PROXY/CN_PROXY 自动填入。
export BOCHA_PROXY="http://user:pass@cn-proxy.example:8080"

# Exa：推荐显式提供海外 HTTP/HTTPS 代理，或使用 IR_SEARCH_OVERSEAS_PROXY/OVERSEAS_PROXY 自动填入。
export EXA_PROXY="http://127.0.0.1:7890"
```

如果没有手动设置 `EXA_PROXY`，系统 HTTP/HTTPS 代理会自动填给 Exa。Bocha 默认不会自动使用系统代理，避免被全局 VPN 带到海外出口；若你的本机系统代理本身是规则代理且能保证 Bocha 走国内出口，可设置：

```bash
export BOCHA_AUTO_PROXY=1
```

Bocha 仍默认 `BOCHA_DISABLE_SYSTEM_PROXY=1`。若你希望 Bocha 直接使用系统代理而不是显式 `BOCHA_PROXY`，可设置：

```bash
export BOCHA_DISABLE_SYSTEM_PROXY=0
```

关闭自动探测：

```bash
export IR_SEARCH_AUTO_PROXY=0
```

MCP 入口暴露搜索、文档读取、证据抽取、claim verification、deep research 和 source health：

```bash
python -m ir_search.mcp_server
```

Cursor 里使用 MCP 时，`IR_SEARCH_PYTHON` 必须是 Python 3.10+，并且要能同时 import `ir_search`、`mcp.server.fastmcp.FastMCP` 和 `ir_search.mcp_server`。可用 doctor 先检查：

```bash
python scripts/doctor_ir_search_mcp.py \
  --ir-search-python /ABSOLUTE/PATH/TO/python \
  --ir-search-path /ABSOLUTE/PATH/TO/ir-search
```

如果缺少 FastMCP，请把 MCP extra 安装到 Cursor 将使用的解释器中：

```bash
/ABSOLUTE/PATH/TO/python -m pip install -e "/ABSOLUTE/PATH/TO/ir-search[mcp]"
```

Cursor GUI 可能不继承 shell 里的 API key。生成 research workspace 后，推荐复制 `.env.local.example` 为 `.env.local`，只在本地填写 key；MCP wrapper 会在启动时 source 它，`source_health` 只会显示 `has_KEY=true/false` 和不可用原因，不会输出真实 key。

当前工具列表：

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

本地调试：

```bash
python -m ir_search.source_health
python -m ir_search.deep_research "中际旭创 最近一季报 海外 AI 光模块需求"
```

新 skills 先查看 `list_capabilities`，按研究任务组合 `get_data`、`search_materials` 和 `retrieve`，并检查每次返回的来源、日期、覆盖范围及失败诊断。`source_health` 可作为已有来源的辅助状态入口。素材服务还区分扫描/命中数量与最终返回的正文、研报摘要、搜索摘要、标题和引用数量，详见[素材服务说明](material_search.md)。

旧 `deep_research` 继续保留其搜索、正文读取、规则核验和报告骨架，供已有调用方兼容使用；它尚未串联新的数值和素材服务。其 `supported`、`mixed` 等标签来自启发式规则，不能单独证明事实或替代研究判断。网页、PDF、微信文章和搜索摘要一律视为 untrusted source text。

如果要把 Cursor 配成独立投研问答工作台，请使用 Cursor research workspace template 和 bootstrap 脚本生成单独的 research workspace，避免代码仓库上下文污染。详见 [docs/cursor_research_workspace_setup.md](cursor_research_workspace_setup.md)。

配置校验：

```bash
python -m ir_search.config_validation --strict
```

## 素材搜索额度回退

`search_materials` 的网页路线明确额度耗尽时，返回 `fallback_requests`，由调用方 Agent 使用自带 Web Search，并把链接交回 `retrieve` 读取原文。中文博查、英文 Exa 可通过私有 env 配置；保留旧 AnySearch 配置兼容。接续范围、诊断和未完成状态见[搜索额度回退指南](web_search_fallback.md)。

## 旧版 search 的 fallback（兼容保留）

默认不自动 fallback。只有显式设置 `Query(..., allow_fallback=True, fallback_policy="all")` 或对应策略时，Bocha / Exa 因 API key、额度、限流或网络错误失败后，系统才会显式记录失败 diagnostics，然后尝试：

```text
bocha -> anysearch -> searxng -> web_search
exa -> tavily -> anysearch -> searxng -> web_search
tavily -> anysearch -> searxng -> web_search
anysearch -> searxng -> web_search
tushare -> longbridge -> market_public
longbridge -> market_public
```

fallback 会经过 source capability 防火墙过滤，规则见 [docs/fallback_firewall.md](fallback_firewall.md)。关键约束：

- `FILING` / 公告 / 监管披露类查询不会降级到 `searxng`、`web_search`、普通媒体或 UGC。
- 结构化市场数据查询只在 `tushare` / `longbridge` / `market_public` 这类数据源之间 fallback。
- 被 policy 拦截的 fallback 不会静默跳过，会在 `SearchResult.diagnostics` 中记录 `skipped=true`、`failure_kind=blocked_by_policy` 和 `skipped_reason`。

海外 fallback key：

```bash
export TAVILY_API_KEY="..."
```

`anysearch` 作为兜底使用 `ANYSEARCH_API_KEY`：

```bash
export ANYSEARCH_API_KEY="..."
```

`searxng` 是自建低成本元搜索兜底源，位于 Bocha / Exa / Tavily / AnySearch 之后、`web_search` 之前。它默认关闭，启用前需要自建实例并设置 `SEARXNG_ENABLED=true` 和 `SEARXNG_URL`。配置说明见 [docs/searxng_setup.md](searxng_setup.md)。

`web_search` 是中文 Bocha 链路的最后兜底，默认使用 AnySearch 匿名免费额度，不发送 `ANYSEARCH_API_KEY`。如果所有来源都失败，`SearchResult.diagnostics` 会保留每一步失败原因，不会静默假装成功。

fallback 命中的结果会在 `Hit.extra` 中标记：

```json
{"is_fallback_result": true, "fallback_from": "exa"}
```

## Source 模式

当前 live-capable adapter：

```text
cninfo, bocha, exa, tavily, wechat_opencli, manual_wechat, dajiala, zsxq, longbridge, tushare, market_public
```

当前 fallback / experimental adapter：

```text
searxng, anysearch, web_search
```

当前仍为 mock / placeholder 的投研结构化源：

```text
sse, szse, hkex, sec, company_ir, broker_research, regulator_sites, industry_media
```

这些 mock 源只用于验证路由、证据分类和排序，不代表已经接入真实官方公告或研报源。调用 mock source 时，`SourceStatus.adapter_mode == "mock"`，并且 `Hit.extra["adapter_mode"] == "mock"`。

公告类查询的代码原则：

```text
FILING -> cninfo / sse / szse / hkex / sec
```

Bocha 不是权威公告源，不在 `FILING` 默认路由中。live 模式下，`cninfo` 已接入真实公告元数据查询。`sse/szse/hkex/sec` 如尚未实现真实 adapter，会以 `adapter_mode="placeholder"` 明确失败，而不是用 Bocha 或 mock 结果伪装成官方公告。

## LLM rewrite guard

`Query.llm_rewrite` 目前是 reserved flag，默认关闭。`search()` 的确定性热路径不会调用 LLM。未来如果实现 LLM query rewrite，必须作为独立、可关闭、可记录、可回放的 pre-processing stage，不能混入 adapter routing / fan-out / rerank。

## 微信公众号来源

新 skills 请使用 `search_materials(providers=["wechat"], wechat_accounts=[...])`，正文使用 `retrieve`；参见[公众号素材指南](wechat_material_adapter.md)。以下为旧 `search` 的兼容配置和历史案例，不代表新素材接口的当前验收结果。

微信公众号有两条入口：

```text
manual_wechat -> 本地手工文章库，确定性最高
wechat_opencli -> 外部浏览器/微信自动化命令，未配置命令时会尝试 manual_wechat fallback
gzh_fetch -> 极致了 API + wewe-rss + 通用 RSS 三 provider cross-check
```

手工库默认读取当前目录下的 `manual_wechat_articles/`，也可以显式设置：

```bash
export MANUAL_WECHAT_ROOT="/ABSOLUTE/PATH/TO/manual_wechat_articles"
```

支持 `.md` / `.json` / `.jsonl`。Markdown 推荐格式：

```markdown
---
title: "文章标题"
url: "https://mp.weixin.qq.com/s/..."
published_at: "2026-06-11"
account_name: "一凌策略研究"
---
正文或摘录
```

如果要先用公开网页搜索找候选文章，可把搜狗微信候选脚本接到 OpenCLI 命令位：

```bash
export WECHAT_OPENCLI_COMMAND="python3 /ABSOLUTE/PATH/TO/ir-search/tools/wechat_search_sogou.py --json"
```

搜狗微信可能触发验证码或返回滞后结果，因此它只适合作为候选发现；关键投研文章建议落到 `manual_wechat` 手工库。更多配置见 [docs/wechat_opencli_setup.md](wechat_opencli_setup.md)。

更推荐的自动化方案是 `gzh_fetch` 三源 cross-check：

```bash
cp configs/accounts.example.json accounts.json
export DAJIALA_KEY="..."
export WECHAT_OPENCLI_COMMAND="python3 /ABSOLUTE/PATH/TO/ir-search/tools/gzh_fetch.py --accounts /ABSOLUTE/PATH/TO/ir-search/accounts.json --opencli --providers dajiala,wewe,rss --default-days 14"
python3 -m ir_search "一凌策略研究 最新文章" --source wechat --count 5
```

`accounts.json` 里每个公众号可单独配置 `dajiala` / `wewe` / `rss`。`wewe` 指自建 `wewe-rss` / `we-mp-rss` 服务，微信读书小号只在其管理界面扫码登录，本项目只读取本地 feed，例如 `http://127.0.0.1:4000/feeds/xxx.json`，不接触微信读书账号密码。

本项目已经验证过 `一凌策略研究`：极致了负责发现最新文章链接，拿到 `mp.weixin.qq.com` 链接后，`tools/gzh_fetch.py --fulltext` 可以直接抓正文。正文策略是质量优先：已有 feed/list 正文优先，其次直抓 mp.weixin，若直抓失败或为空，再用极致了详情 API 兜底。成功经验见 [docs/wechat_gzh_success_playbook.md](wechat_gzh_success_playbook.md)。

## 极致了单来源

`dajiala` adapter 是公众号单 provider 来源，复用 `tools/gzh_fetch.py`，但固定只调用 `--providers dajiala`，不会访问 `wewe` 或 `rss`：

```bash
export DAJIALA_KEY="..."
export DAJIALA_ACCOUNTS_PATH="/ABSOLUTE/PATH/TO/ir-search/accounts.json"

# 可选：默认回看 14 天；需要正文兜底时打开。
export DAJIALA_DEFAULT_DAYS=14
export DAJIALA_FULLTEXT=1
```

显式查询：

```bash
python -m ir_search "一凌策略研究 最新文章" --source dajiala --count 5
python -m ir_search "一凌策略研究 最新文章" --source 极致了 --freshness oneWeek
```

当查询文本包含“极致了 / dajiala”时，路由会自动加入 `dajiala`。如果你要三源交叉验证，继续使用 `wechat_opencli` + `gzh_fetch.py --providers dajiala,wewe,rss`；如果只要极致了，使用 `dajiala`。

## 知识星球素材：SDK / MCP

新 `ZsxqMaterialAdapter` 已独立实现：使用私有 env 中的 `ZSXQ_KEY`，通过官方 HTTPS MCP 读取有限星球时间流、帖子详情和可选评论；`search_materials(providers=["zsxq"])` 提供作者角色、文本层级、附件目录和版本引用，`retrieve` 可读取显式帖子或 PDF 附件引用。搜索阶段只发现附件，不自动下载。

代码位于安装包内，无 BrokerSkills、CLI 或浏览器依赖。官方 RAG 不进入确定性检索链路；有限扫描、来源片段、未确认问答角色和附件原始发布方均有诊断。配置与例子见[知识星球素材指南](zsxq_material_adapter.md)，真实 SDK/MCP/PDF 与工程检查见[验收记录](zsxq_material_acceptance.md)。

### 原有 `search` 的 CLI 兼容来源

以下仅适用于旧 `search`，新素材入口无需这些步骤。旧来源使用官方 `zsxq-cli` / `zsxq-skill`。登录推荐走 OAuth，token 存系统钥匙串，不要把账号密码或 token 写入仓库：

```bash
npm install -g zsxq-cli
npx skills add https://github.com/unnoo/zsxq-skill --yes --global
zsxq-cli auth login
zsxq-cli group +list --json
```

旧搜索 registry 中的 `zsxq` adapter 是只读搜索源，调用 `topic +search --json`。配置要搜索的星球 ID：

```bash
export ZSXQ_GROUP_IDS="12345,67890"
# 如果 zsxq-cli 不在当前 shell PATH，可显式指定：
export ZSXQ_CLI_COMMAND="/ABSOLUTE/PATH/TO/zsxq-cli"
```

显式查询：

```bash
python -m ir_search "光模块 产业链 观点" --source zsxq --count 5
```

当查询文本包含“知识星球 / 星球 / zsxq”时，路由会自动加入 `zsxq`。知识星球不在网页搜索 fallback 链路里；需要登录且配置了 `ZSXQ_GROUP_IDS` 后才会返回结果。

## Longbridge 只读来源

Longbridge adapter 只用于行情、资讯和机构一致预期等只读数据，显式屏蔽持仓、账户、下单、撤单、改单等功能。登录 Longbridge CLI 时只需要 Quote 权限；不要为本项目勾选 Trade 权限：

```bash
brew install longportapp/tap/longbridge
longbridge auth login

# 如果 longbridge 不在当前 shell PATH，可显式指定：
export LONGBRIDGE_CLI_COMMAND="/opt/homebrew/bin/longbridge"
```

显式查询：

```bash
python -m ir_search "长桥 NVDA.US 最新 AI 新闻" --source longbridge --count 5
python -m ir_search "长桥 600519 评级 估值" --source 长桥 --count 5
```

当前 `longbridge` adapter 只会调用这些 CLI 子命令：

```text
news search, quote, institution-rating
```

代码层会拒绝 `portfolio / positions / assets / cash-flow / statement / order / trade / max-qty` 等账户或交易相关命令；即使本机 Longbridge 账户拥有 Trade 权限，本项目 adapter 也不会调用这些能力。

## TuShare A股结构化数据来源

TuShare adapter 按本机 `a-stock-data` skill 的规则接入，只读调用 TuShare 代理 API。它优先读取：

```bash
export TUSHARE_TOKEN="..."
# 或 fallback：
export TUSHARE_PRO_TOKEN="..."

# 按 a-stock-data skill，默认也是这个代理 URL；一般无需改。
export TUSHARE_HTTP_URL="https://fastapic.stockai888.top"
export TUSHARE_RATE_LIMIT_SECONDS="0.65"
```

显式查询：

```bash
python -m ir_search "600519 财务指标" --source tushare --count 5
python -m ir_search "300750 股东人数 龙虎榜 解禁" --source A股数据 --count 10
```

当查询包含 `tushare / a-stock-data`，或出现“股东人数、龙虎榜、限售、解禁、财务指标、业绩预告、业绩快报、日行情、换手率、资金流、市值”等结构化 A 股数据关键词时，路由会自动加入 `tushare`。

当前只读支持的 TuShare API 包括：

```text
stock_basic, daily, daily_basic, fina_indicator, forecast, express,
stk_holdernumber, top_list, share_float, moneyflow
```

字段以 TuShare 返回的 `data.fields + data.items` 转为 `Hit.extra["row"]` 保存，便于后续审计和表格化。

## Public 行情兜底

`market_public` adapter 是无 token 的公开行情兜底源，适合补充或交叉验证最新价、涨跌幅、PE/PB、市值、换手、涨跌停、个股基本面快照、盘口/K线字段、同花顺热点归因等公开字段。

默认 provider 顺序：

```text
tencent, eastmoney, baidu, akshare, mootdx, ths
```

可以按需收窄，避免某些公开源临时变慢：

```bash
export MARKET_PUBLIC_PROVIDERS="tencent,eastmoney"
```

显式查询：

```bash
python -m ir_search "600519 最新行情 PE PB 市值" --source market_public --count 5
python -m ir_search "腾讯行情 300750 最新估值" --count 5
```

也可以作为行情链路 fallback：

```bash
python -m ir_search "600519 最新行情 估值" --source tushare --fallback-policy all --fallback-on-empty
```

对应链路：

```text
tushare -> longbridge -> market_public
longbridge -> market_public
```

当前包装的数据源：

| Provider | 当前用途 |
| --- | --- |
| `tencent` | 最新价、涨跌幅、PE/PB、市值、换手、涨跌停、量比 |
| `eastmoney` | 东财 quote 快照，补充价格、估值、市值 |
| `baidu` | 百度股市通 quote 防御式解析，补充价格/涨跌 |
| `akshare` | `stock_individual_info_em` 个股基本面快照 |
| `mootdx` | 通达信实时行情字段，补充价格、盘口/K线底层字段 |
| `ths` | 同花顺强势股/热点题材归因，命中个股时返回 reason |

`market_public` 只读、无需 token，但它是公开源聚合，字段和可用市场取决于各 provider 当前返回；关键结论建议与 TuShare、Longbridge 或公告交叉验证。

公众号已支持初始隐藏正文恢复、文章结构与图片引用；可通过 SDK/MCP 显式导出版本化本地资料。引用继续绑定纯文本，图片不自动 OCR。归档器也可复用其他 adapter 返回的素材，见[文章结构与本地归档](article_materials.md)。


## 机构与官网取材增强（2026-09-17）

已增加 14 条有出处的机构目录、显式官网范围 `web_institutions`、监管目录/附件识别、HTTP 正文有界并发 `web_read_workers` 和无网络执行预览 `dry_run`。目录随安装包分发，SDK 与 MCP 均可调用；详见[使用说明](material_hardening.md)。


## 社区与视频素材（2026-09-17）

新增独立素材来源 `xueqiu`、`eastmoney`（股吧）、`video`（Bilibili / YouTube），统一通过 `search_materials` 与 `retrieve` 调用。视频简介、搜索摘要与实际字幕分开，字幕引用可定位到播放时间。配置、用法与访问限制见[社区与视频 adapters](community_video_adapters.md)和[真实验收记录](community_video_acceptance.md)。此前借鉴资源已整理成[本地参考资源库](reference_library.md)，不成为跨电脑运行依赖。音频后续已接入，见[小宇宙接入](xiaoyuzhou_audio_adapter.md)。

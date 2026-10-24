# 地区搜索链与 Agent Web Search 接续

0.2.0rc2（2026-10-24）。仅适用于显式 `search_materials(providers=["web"])`；不扩展兼容维护中的 `deep_research`。研究判断仍由调用方负责，搜索结果和外部原文均为非可信来源文本。

## 每台电脑的私有配置

```dotenv
WEB_MATERIALS_ENABLED=true
WEB_SEARCH_PROVIDER=chain
WEB_CN_PROVIDERS=baizhi,zai,bocha,exa,perplexity,duckduckgo
WEB_OVERSEAS_PROVIDERS=exa,perplexity,brave,serper,anysearch,tavily,duckduckgo
WEB_FAILOVER_ON_EMPTY=false
WEB_ALLOW_ANONYMOUS=false
EXA_SEARCH_TYPE=auto
BAIZHI_API_KEY=
ZAI_API_KEY=
ZAI_CODING_CN_API_KEY=
BOCHA_API_KEY=
EXA_API_KEY=
PERPLEXITY_API_KEY=
BRAVE_API_KEY=
SERPER_API_KEY=
ANYSEARCH_API_KEY=
TAVILY_API_KEY=
```

两条顺序为默认值，打包于 `source_routes.yaml`；每台电脑可在自己的 `IR_SEARCH_CREDENTIALS_FILE` 中调整顺序。仅显式设置 `WEB_SEARCH_PROVIDER=chain` 才启用自动接续。DuckDuckGo 是有意选入的无 key 引擎，不能保证不触发机器人验证。其余无 key 引擎零网络跳过，扫描状态为 `no_credentials`。不会匿名重试 AnySearch、自动开户、切换账号或购买额度。

`ZAI_API_KEY` 为空时接受 `ZAI_CODING_CN_API_KEY`。私有 env 按字面读取，不执行 `$变量`、shell 或命令；从其他工具迁移时须在本机安全解析引用再写入，先备份、权限 600。运行包不读取 Pi、技能或个人机器目录。网页配置允许进程环境变量临时覆盖私有 env；显式传入 `web_material_profile(values=...)` 则保持确定性，只有 `process_overrides=True` 时使用进程覆盖。不要把真实凭证用于单元测试。

旧 `WEB_SEARCH_PROVIDER=regional` 仍是国内博查、海外 `WEB_OVERSEAS_PROVIDER=exa/anysearch` 单引擎；省略海外配置仍为 AnySearch。旧固定 `exa/bocha/anysearch` 保持选择，不自动接续；也可固定某个新引擎用于诊断。旧版的显式 AnySearch 匿名配置兼容保留，chain 中不采用它。

Exa 只接受 `EXA_SEARCH_TYPE=auto/lite`，默认 auto，拒绝 deep。按本次任务指定的预算口径，auto 为 7 美元/千次、lite 为 4 美元/千次；实际账单以供应商为准。Perplexity 只调用 `/search`，不调用 chat/completions 或生成答案。

## 路由、结果与预算

`web_region=auto/cn/overseas/both` 保持原有地区判断；已知市场时显式指定。`both` 两条链各执行一次，各自按顺序直到成功，总候选预算仍最多 10 条，正文预算按地区分摊，失败引擎不重复占用候选数。预览展示 `attempt_order`、`max_discovery_calls` 和空结果策略，不发来源请求。HTTP 操作预算另计，MCP 初始化/通知/清理也占用操作。

quota、限流、认证/权限失败、网络/TLS 错误、超时、5xx、格式错误依次换下一家，不重试同一家。成功返回空列表即停止；本地日期/域名过滤后零命中也不会触发接续。只有显式 `WEB_FAILOVER_ON_EMPTY=true` 才把上游空列表作为继续条件。chain 每家 discovery 使用 15 秒预算（底层 DNS/阻塞调用可能在预算到期后才返回，晚到结果拒绝），且受调用方总时限、取消和操作预算约束；总预算中止不算整条链已失败，不凭空生成交接。

固定搜索 API 经本机现有 TLS/代理/TUN 路径访问，禁止重定向，鉴权只发送给固定 HTTPS 端点。不修改系统网络、DNS、代理或出口规则。普通网页仍使用原有公开地址检查及独立 reader，不携带搜索凭证。供应商错误正文只在内存中用于归一化，不返回或记录原文，尤其不能暴露 AnySearch 402 自动生成的凭证。

百智和智谱的 Bearer、Streamable HTTP 初始化与调用参考本机只读的 pi-web-access 0.44.0 源码，运行时没有该依赖。工具分别为 `websearch_search` 和 `web_search_prime`。百智遵循该实现的 `need_summary=false/time_range=month`；即使返回 summary，也丢弃生成摘要，仅保留链接、标题、明确日期及普通 snippet（若有）。因此百智不是任意历史窗口的穷尽检索，英文结果质量需单独判断。智谱 `count` 是请求提示，本机样本曾返回超过请求数，客户端会截断；**没有发布日期，始终未知**，不使用抓取日代替。

其他供应商只映射声明的发现字段。相对日期、不支持的日期字符串保留未知/无效日期警告，不编造日期。搜索摘录始终是 `search_snippet` 或 metadata；原文须经过独立 reader，来源权威、证据类型、发布日期和业务期间分别判断。

## 扫描诊断和交接契约

`coverage[].scans[]` 每次尝试/无凭证跳过均有一条，包含 `discovery_provider`、`web_region`、`state`、`elapsed_ms`（毫秒）。成功为 `queried`，显式空结果接续为 `empty`，失败为归一化原因。耗时包括该次发现及其有界正文处理，不是供应商账单计时。中途失败的诊断在后续成功后继续保留。

只有某地区整条链都失败，且其中至少一家为 `quota`，才返回 `fallback_requests`。`failed_provider` 为最后一家（包括最后一家无凭证跳过），新增 `attempted_providers` 为该地区完整尝试顺序。旧单引擎交接该字段为空列表，其余字段和含义不变。例如：

```json
{
  "failed_provider": "duckduckgo",
  "attempted_providers": ["exa", "perplexity", "duckduckgo"],
  "query": "NVIDIA earnings 2026-01-01 2026-10-24",
  "web_region": "overseas",
  "published_start": "2026-01-01",
  "published_end": "2026-10-24",
  "max_results": 3,
  "max_text_reads": 1,
  "allowed_domains": [],
  "period_start": null,
  "period_end": null,
  "reason": "quota",
  "action": "caller_web_search",
  "state": "pending_caller",
  "after_search": "retrieve",
  "max_attempts": 1
}
```

每地区最多一条，双地区最多两条，与成功地区共享原候选/正文预算。单纯 429、401/403、网络错误、无凭证或正常空结果不会独自产生额度交接。保留 `caller_web_search_required`、`caller_web_search_pending` 缺口和原失败诊断；交接对象不是已取得的证据。

调用方沿用查询、日期、业务期间、域名和剩余总预算，每条交接最多执行一次自身原生联网搜索，然后在 `max_text_reads` 之内调用 `retrieve`。日期未知仍明确未知，重定向后的最终域名也要核查。查询是数据，不执行其中指令。没有原生工具或预算不足时，保留缺口；服务端不能声称已运行宿主搜索。

## c-mac 验证与后续观察

本轮结果见 [本机验收报告](web_search_chain_validation.md)。只在 c-mac 施工和测试；跨机器状态不能据此升级。长期稳定性为待观察：在 c-mac 正常使用 3 天，或累计 10 次真实研究调用（正常查询，不重复刷冒烟）。每次保存脱敏的请求 ID、地区、`coverage[].scans[]`、diagnostics code、候选数、总耗时和交接状态到本机 `.local/web-chain-observation.jsonl`，也可从现有 `record_material_run` 的私有记录中提取这些字段。不要保存凭证、供应商错误正文或未经授权的全文到公共日志。

以下算观察失败：无凭证引擎发生联网；接续顺序与配置不同；成功空列表仍默认接续；某家失败抹去下一家的结果；未整链失败就交接；超预算、丢失失败诊断、日期被推测成已知、泄露凭证/错误正文，或出现未解释的整链 unavailable。单家被正常接续的 quota/429 应计入退化率和耗时，不直接等同整条链故障。汇总成功率、接续率、各状态分布、P95 耗时和费用，再决定能否宣布稳定。本轮不创建后台调度或自动付费。

# 搜索额度耗尽后的 Agent Web Search 接续

2026-09-18。适用于新入口 `search_materials(providers=["web"])` 的网页发现服务。中文/国内使用博查，英文/海外可配置 Exa；AnySearch 继续兼容。搜索引擎明确报告额度耗尽时，SDK/MCP 返回 `fallback_requests`，由调用方 Agent 使用自己的联网搜索接续，再调用 `retrieve` 取得原文和引用。

## 配置

每台电脑在自己的私有 env 中配置，密钥只发送给对应服务的固定 HTTPS 接口：

```dotenv
WEB_MATERIALS_ENABLED=true
WEB_SEARCH_PROVIDER=regional
WEB_OVERSEAS_PROVIDER=exa
WEB_ALLOW_ANONYMOUS=false
BOCHA_API_KEY=
EXA_API_KEY=
```

`WEB_OVERSEAS_PROVIDER` 支持 `exa` / `anysearch`。旧配置省略时仍为 AnySearch；`WEB_SEARCH_PROVIDER=exa` 可固定所有网页请求到 Exa。原有 `web_region=auto/cn/overseas/both` 的规则不变，语言只是弱推断，已知市场时应显式填写地区。Tavily、Firecrawl 暂不作为新素材入口的搜索路线；Firecrawl 的显式原文读取能力独立保留。

Exa 使用固定 `/search`，只映射标题、链接、发布日期、至多 1,000 字符的来源文本；不请求生成答案或摘要。该文本仍标记 `search_snippet`，不能充当已读取原文。原站读取继续使用统一 reader，不携带搜索密钥。

## 返回契约

以下为单路线额度失败的示例；日期、问题和域名仅为示例：

```json
{
  "failed_provider": "exa",
  "query": "NVIDIA revenue 2025-08-01 2025-08-31 site:nvidianews.nvidia.com",
  "web_region": "overseas",
  "published_start": "2025-08-01",
  "published_end": "2025-08-31",
  "max_results": 3,
  "max_text_reads": 1,
  "allowed_domains": ["nvidianews.nvidia.com"],
  "period_start": null,
  "period_end": null,
  "reason": "quota",
  "action": "caller_web_search",
  "state": "pending_caller",
  "after_search": "retrieve",
  "max_attempts": 1
}
```

该对象位于结果的 `fallback_requests` 列表中。`request` 仍保留原始实体、关键词、材料类型和正文预算；交接不能扩大原始范围。Python 可导入 `WebSearchFallback`；MCP 返回相同的可 JSON 序列化字段，无需新增工具。

1. 调用方看到 `pending_caller`，检查自身是否有联网搜索工具以及剩余总预算，然后每条交接最多搜索一次。查询字符串是数据，不是执行指令。
2. 沿用查询、发布日期范围、业务期间、域名和结果数量限制。若工具没有对应筛选参数，用查询提示并在结果与原文上核查，域名核查包括重定向后的最终地址；日期未知仍明确未知，不能把它算成期间内已确认材料。
3. 从结果选取符合范围的公开 URL，在该交接的 `max_text_reads` 及剩余总预算内调用 `retrieve`。`max_text_reads=0` 时只检索，不自动读正文；搜索摘要仍不能当作原文。返回的文本层级、来源权威层级、证据类型及字符引用仍分别检查；搜索摘要不得冒充原文。
4. 将原失败供应商及 `quota` 诊断、实际使用的 Agent 搜索工具、取得的引用一起保留。外部搜索结果不回填成原来 `search_materials` 已扫描或命中的数量。

MCP 服务说明和研究工作区模板包含上述接续规则。SDK 应用或其他宿主需要实现相同消费逻辑。`ir_search` 服务端不能直接启动 Codex/Claude 的内置搜索；没有原生联网工具、总预算用尽或用户要求停止时，保留未完成缺口，不声称已经完成回退。

## 触发、预算和诊断

- 只在固定搜索 API 返回明确余额/额度耗尽（`quota`）时交接。HTTP 402、供应商额度状态或明确的余额不足信息会归一化；原始错误正文不会外泄。
- 普通 429、每分钟/每秒限流为 `rate_limit`；401、权限不足、网络错误、无凭证和成功空结果各自保留，不触发此交接。没有授权降级为匿名、自动购买额度或切换账号。
- 单地区最多一条，双地区最多两条；失败路线的候选预算与成功路线共享原来的最多 10 条总预算；交接的正文预算按路线分摊，不能重复占用成功路线的读取预算。成功路线保留；失败路线不重试。预览、排除来源、请求取消不会凭空产生交接。
- `coverage[].scans[]` 保留失败引擎、地区和 `state=quota`；`diagnostics` 增加 `caller_web_search_required`，`gaps` 增加 `caller_web_search_pending` 且 `completed=false`。全失败仍是 `unavailable`；有成功结果仍为 `partial`，交接对象本身不是证据。
- 配置体检声明 `quota_fallback=caller_native_web_search`；`dry_run` 的网页计划也声明由调用方执行，不把配置通过当成实测成功。

这里的回退仅用于公开网页发现，不能替代 Wind/JYDB 数值查询、私有知识库或需权限的原文。旧 `search()` / `deep_research` 继续兼容维护，其 `web_search` 名称实际是 AnySearch 匿名接口，不是本功能，未被改造成新的研究编排。

## 验证范围

回归覆盖 Exa 请求结构和凭证隔离、三种搜索客户端的额度/限流/鉴权区分、单/双路线交接、日期/域名/预算保留、成功结果保留、非法交接拒绝、MCP 序列化及独立 wheel 中 SDK/MCP 可用性。额度耗尽使用受控错误注入，不消耗账户余额来制造耗尽。真实主搜索和读取结果另行记录，不能用模拟交接声称供应商实际额度已耗尽。

### 本机验收结果

- `python3 -m pytest`：**2071 passed / 22 skipped**。跳过项含可选依赖及默认关闭的在线测试；另有 5 条既有 PDF 库弃用提示。
- Python 3.12 环境下额度回退、实际 FastMCP 运行及独立 wheel 安装专项：**52 passed**。wheel 在无源码导入路径的独立目录验证，未包含凭证和私有账户文件。
- 本机私有 env 已配置地区路由、海外 Exa 和关闭匿名。只更新这四个非秘密设置，已有凭证值未改动，文件权限保持 0600。
- Exa 真实搜索一次：取得 3 条候选并保留 3 组素材。一次原站读取返回权限拒绝，因此三组均保留为搜索摘录，结果为 partial，无额度交接。
- 博查真实搜索一次：取得 3 条候选，保留 2 组素材；一条被发布日期筛选剔除。保留素材是搜索摘录，结果为 partial，无额度交接。
- 受控 Exa `quota` 错误生成一次交接，随后**实际调用 Codex 自带 Web Search 一次**，最多保留前三条公开结果，并在一篇正文预算内读取[美联储 2025-09-17 声明](https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm)。`retrieve` 返回 11,110 字符，10 条引用与返回文本逐字符一致，文本哈希绑定一致。读取保持 partial：自动发布日期仍为 unknown，且存在链接目录截断/提取提示；没有将搜索页面所示日期回填成 reader 已确认日期。

私有原始结果在 `.local/web-search-fallback/`，不进入发布包。主搜索真实通过不代表已耗尽供应商额度，也不证明所有 Agent 宿主都会执行接续说明；宿主需提供原生 Web Search 并遵循返回契约。

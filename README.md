# ir_search

面向投研工作的确定性搜索内核。核心入口是 `ir_search.search()`，返回 `SearchResult(query, hits, diagnostics)`。

默认使用本地 mock adapter，便于无 API key 跑通管线和测试。设置 `IR_SEARCH_LIVE=1` 后，Bocha / Exa adapter 会读取 `BOCHA_API_KEY` / `EXA_API_KEY` 调真实接口。所有 diagnostics 都会带 `adapter_mode`，用于区分 `live` / `mock` / `unknown`。

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

MCP 入口只暴露一个 `search` 工具：

```bash
python -m ir_search.mcp_server
```

配置校验：

```bash
python -m ir_search.config_validation --strict
```

## 额度耗尽时的 fallback

默认不自动 fallback。只有显式设置 `Query(..., allow_fallback=True, fallback_policy="all")` 或对应策略时，Bocha / Exa 因 API key、额度、限流或网络错误失败后，系统才会显式记录失败 diagnostics，然后尝试：

```text
bocha -> anysearch -> web_search
exa -> tavily -> anysearch -> web_search
```

海外 fallback key：

```bash
export TAVILY_API_KEY="..."
```

`anysearch` 作为兜底使用 `ANYSEARCH_API_KEY`：

```bash
export ANYSEARCH_API_KEY="..."
```

`web_search` 是中文 Bocha 链路的最后兜底，默认使用 AnySearch 匿名免费额度，不发送 `ANYSEARCH_API_KEY`。如果所有来源都失败，`SearchResult.diagnostics` 会保留每一步失败原因，不会静默假装成功。

fallback 命中的结果会在 `Hit.extra` 中标记：

```json
{"is_fallback_result": true, "fallback_from": "exa"}
```

## Source 模式

当前 live-capable adapter：

```text
cninfo, bocha, exa, tavily, wechat_opencli
```

当前 experimental adapter：

```text
anysearch, web_search
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

OpenCLI 微信公众号配置见 [docs/wechat_opencli_setup.md](docs/wechat_opencli_setup.md)。

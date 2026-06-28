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
bocha -> anysearch -> searxng -> web_search
exa -> tavily -> anysearch -> searxng -> web_search
tavily -> anysearch -> searxng -> web_search
anysearch -> searxng -> web_search
tushare -> longbridge -> market_public
longbridge -> market_public
```

海外 fallback key：

```bash
export TAVILY_API_KEY="..."
```

`anysearch` 作为兜底使用 `ANYSEARCH_API_KEY`：

```bash
export ANYSEARCH_API_KEY="..."
```

`searxng` 是自建低成本元搜索兜底源，位于 Bocha / Exa / Tavily / AnySearch 之后、`web_search` 之前。它默认关闭，启用前需要自建实例并设置 `SEARXNG_ENABLED=true` 和 `SEARXNG_URL`。配置说明见 [docs/searxng_setup.md](docs/searxng_setup.md)。

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

微信公众号有两条入口：

```text
manual_wechat -> 本地手工文章库，确定性最高
wechat_opencli -> 外部浏览器/微信自动化命令，未配置命令时会尝试 manual_wechat fallback
gzh_fetch -> 极致了 API + wewe-rss + 通用 RSS 三 provider cross-check
```

手工库默认读取当前目录下的 `manual_wechat_articles/`，也可以显式设置：

```bash
export MANUAL_WECHAT_ROOT="/Users/chen/macro-strategy/manual_wechat_articles"
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
export WECHAT_OPENCLI_COMMAND="python3 /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/tools/wechat_search_sogou.py --json"
```

搜狗微信可能触发验证码或返回滞后结果，因此它只适合作为候选发现；关键投研文章建议落到 `manual_wechat` 手工库。更多配置见 [docs/wechat_opencli_setup.md](docs/wechat_opencli_setup.md)。

更推荐的自动化方案是 `gzh_fetch` 三源 cross-check：

```bash
cp configs/accounts.example.json accounts.json
export DAJIALA_KEY="..."
export WECHAT_OPENCLI_COMMAND="python3 /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/tools/gzh_fetch.py --accounts /Users/chen/Documents/Codex/2026-06-08/files-mentioned-by-the-user-ir/accounts.json --opencli --providers dajiala,wewe,rss --default-days 14"
python3 -m ir_search "一凌策略研究 最新文章" --source wechat --count 5
```

`accounts.json` 里每个公众号可单独配置 `dajiala` / `wewe` / `rss`。`wewe` 指自建 `wewe-rss` / `we-mp-rss` 服务，微信读书小号只在其管理界面扫码登录，本项目只读取本地 feed，例如 `http://127.0.0.1:4000/feeds/xxx.json`，不接触微信读书账号密码。

本项目已经验证过 `一凌策略研究`：极致了负责发现最新文章链接，拿到 `mp.weixin.qq.com` 链接后，`tools/gzh_fetch.py --fulltext` 可以直接抓正文。正文策略是质量优先：已有 feed/list 正文优先，其次直抓 mp.weixin，若直抓失败或为空，再用极致了详情 API 兜底。成功经验见 [docs/wechat_gzh_success_playbook.md](docs/wechat_gzh_success_playbook.md)。

## 极致了单来源

`dajiala` adapter 是公众号单 provider 来源，复用 `tools/gzh_fetch.py`，但固定只调用 `--providers dajiala`，不会访问 `wewe` 或 `rss`：

```bash
export DAJIALA_KEY="..."
export DAJIALA_ACCOUNTS_PATH="/Users/chen/Documents/ir_search/accounts.json"

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

## 知识星球来源

知识星球使用官方 `zsxq-cli` / `zsxq-skill`。登录推荐走 OAuth，token 存系统钥匙串，不要把账号密码或 token 写入仓库：

```bash
npm install -g zsxq-cli
npx skills add https://github.com/unnoo/zsxq-skill --yes --global
zsxq-cli auth login
zsxq-cli group +list --json
```

项目内 `zsxq` adapter 是只读搜索源，调用 `topic +search --json`。配置要搜索的星球 ID：

```bash
export ZSXQ_GROUP_IDS="12345,67890"
# 如果 zsxq-cli 不在当前 shell PATH，可显式指定：
export ZSXQ_CLI_COMMAND="/Users/chen/.hermes/node/bin/zsxq-cli"
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

# c-mac 网页搜索链验收（0.2.0rc2）

日期：2026-10-24（America/Los_Angeles）。仅此机器，分支 `feat/web-search-chain`，不 merge、不 push。用户既有改动已先保存为 `48def87`（`wip: pre-search-upgrade snapshot`）。本报告不含凭证或供应商错误正文。

## 测试环境与范围

- 原系统 Python 3.9.6 基线：2209 passed、29 skipped、1 failed（45.51 秒）。失败为已有 `test_parser_and_resume_never_require_network`，隔离子进程的依赖缺失；未修改该功能代码。
- 使用本机独立 `.local/web-chain-venv`（Python 3.12）安装测试/MCP/PDF 依赖，剥离真实进程凭证并设置空测试 env，执行 `python3 -m pytest`。最终计数见文末。
- 独立安装验证构建 wheel、删除构建源码，从无关目录运行 SDK/MCP、资源加载、诊断和私有文件排除检查。
- 新增 adapter/传输/路由测试覆盖成功、空结果、quota、429、401、超时、5xx、格式错误、无 key、空结果策略、整链失败、旧配置、日期未知、MCP/SSE、交接篡改和错误脱敏。原 SDK/MCP 相等性测试会分别检查耗时非负，并排除每次调用不同的耗时后比较其他字段。
- MCP doctor 通过：所选解释器、FastMCP、服务模块导入和工具清单正常；它是预检，真实 stdio 调用另列。

## 单引擎真实冒烟

每家分别固定引擎，中文“宁德时代 2026年三季报”、英文“NVIDIA Q3 FY2027 earnings”，每次请求 3 条，未读取正文。每家恰好 2 次搜索尝试，无自动重试或购买额度。标题、日期为供应商原字段的有限样本，不代表事实核实、相关性合格或财报已经发布。

| Engine | Language | Status | Count | Seconds | First title | Date metadata |
|---|---|---|---:|---:|---|---|
| baizhi | zh | success | 3 | 9.763 | 宁德时代:第三季度实现营收1652.2亿元，同比增长58.6%_央广网 | 2026-10-20T20:38:00+08:00 |
| baizhi | en | success | 3 | 6.524 | 英伟达财报日期，财务报告和业绩会 - 富途牛牛 | 2026-10-17T12:32:22+08:00 |
| zai | zh | success | 10 | 7.517 | 宁德时代新能源科技股份有限公司2026 年第三季度报告 | unknown |
| zai | en | success | 3 | 6.972 | NVIDIA Announces Financial Results for Second Quarter | unknown |
| bocha | zh | tls_error | 0 | 5.263 | - | unknown |
| bocha | en | tls_error | 0 | 5.012 | - | unknown |
| exa | zh | success | 3 | 2.956 | 宁德时代新能源科技股份有限公司 | 2026-10-21T00:00:00.000Z |
| exa | en | success | 3 | 1.962 | NVDA Q3 FY2027 Earnings: Date, Time & Expectations \| Theta Research | unknown |
| perplexity | zh | success | 3 | 2.286 | 宁德时代：2026年三季度报告网页链接 | 2026-10-20 |
| perplexity | en | success | 3 | 2.757 | Nvidia (NVDA) Earnings Call Highlights AI Supercycle | 2026-09-14 |
| brave | zh | success | 3 | 1.667 | 宁德时代，三季报出炉_新浪财经_新浪网 | 2026-10-21T02:38:56 |
| brave | en | success | 3 | 1.742 | NVIDIA Corporation - Financial Reports | 2026-08-28T00:40:04 |
| serper | zh | success | 3 | 2.713 | 宁德时代新能源科技股份有限公司2026 年第三季度报告 | 3 days ago |
| serper | en | success | 3 | 2.796 | NVIDIA Announces Financial Results for Second Quarter ... | Aug 26, 2026 |
| anysearch | zh | success | 3 | 3.649 | 宁德时代新能源科技股份有限公司2026 年第三季度报告 | unknown |
| anysearch | en | success | 3 | 1.944 | NVIDIA Announces Financial Results for Second Quarter Fiscal 2027 | unknown |
| tavily | zh | success | 3 | 3.935 | 宁德时代（300750）2026年三季报简析：营收净利润同比双双增长，应收账款上升_腾讯新闻 | unknown |
| tavily | en | success | 3 | 3.204 | Nvidia Earnings Date: Q3 FY2027 Time and Estimates \| CMEG | unknown |
| duckduckgo | zh | rate_limit | 0 | 1.678 | - | unknown |
| duckduckgo | en | rate_limit | 0 | 1.574 | - | unknown |

智谱中文首轮忽略 count=3 返回 10 条，表中据实保留。随后修复客户端截断，英文及后续链输出受请求预算约束。智谱没有日期字段，unknown 和正式结果的未知状态均不推断。Serper 相对日期/英文月份日期不转换为确定发布日期，正式结果保留未知日期警告。百智返回 summary，即使 need_summary=false 也会丢弃，未当原文或 snippet 使用。

博查中英文均为 `tls_error`；保留 TLS 校验，没有关闭校验或修改网络配置。DuckDuckGo 中英文均为机器人验证/限流 `rate_limit`，没有绕过验证，也没有当空结果处理。两家本轮未成功，不额外刷调用。其他 8 家中英文均返回结果；百智英文样本返回中文来源，搜索质量仍需实际研究观察。

## 地区链、错误 key 接续和 MCP

预算口径已由用户确认：上述单引擎每家最多 2 次；指定地区链、坏 key 接续和 MCP 另计。只执行正常 CN、正常 overseas、坏 key CN、坏 key overseas、MCP both 各一次。共 9 次额外引擎尝试（含一次正常链的超时接续），全轮合计 29 次。MCP 初始化/通知/清理另占 HTTP 操作。Perplexity 共 3 次 Search API 成功调用，没有 chat/completions。

| Test | Status | Count | Seconds | Actual engine order (state; seconds) |
|---|---|---:|---:|---|
| cn_chain | partial | 6 | 24.994 | baizhi@cn(timeout; 18.432) -> zai@cn(queried; 6.545) |
| overseas_chain | partial | 6 | 3.533 | exa@overseas(queried; 3.527) |
| mcp_both | partial | 6 | 10.875 | baizhi@cn(queried; 8.361) -> exa@overseas(queried; 2.270) |

两次坏 key 仅用临时进程环境变量覆盖，私有 env 未改成坏 key。百智、Exa 均记录 `authentication_failed`，随后智谱、Perplexity 成功，`failover_verified=true`。正常 CN 链遇到一次百智超时并接到智谱；这是实际退化证据，不能抹去。正常 overseas 直接 Exa 成功。

MCP 使用真实 `mcp.client.stdio` 子进程 initialize/list_tools/call_tool，调用 `search_materials(providers=['web'], web_region='both')`，CN 百智和 overseas Exa 各完成一次。返回 `partial` 是有界发现、零正文读取及非穷尽诊断，不表示搜索调用失败。全部接续/MCP 返回均验证不含已知凭证；诊断 message 仅由本地固定模板组成，未含供应商原始错误正文。

## 安全记录

私有 env 写前备份至忽略目录，权限 600，已确认 `credentials.env` 被 `.gitignore` 的 `*.env` 排除。Pi 中的环境变量引用在本机解析，运行包没有依赖 Pi。原文件 3 行无效语法以注释保留；验证期间另发现 3 个重复字段，另行备份后保留较后值、注释较前值，没有丢弃历史。没有修改网络、代理、DNS、Clash、Tailscale 或其他电脑。

**本轮发生一次泄露事件：** 一个失败单元测试受继承的真实智谱环境变量影响，pytest 比较断言将该凭证回显到工具输出。该值未写入代码、报告或 Git；输出无法撤回。已分开显式 `values` 与进程覆盖，后续完整测试剥离真实凭证并使用空 env，接续/MCP 后续输出扫描通过。建议用户轮换曾回显的智谱凭证；本轮未擅自操作上游账户。不能把整轮“无泄露”标为通过。

## 稳定性验收

| 项目 | 状态 | 依据 / 后续 |
|---|---|---|
| A. pytest、独立安装、每家中英文至少成功一次 | 未通过 | 离线测试和 wheel 通过；8/10 家双语成功，博查 TLS、DuckDuckGo 验证挑战未成功 |
| B. 接续实测通过且没有泄露 | 未通过 | 两次坏 key 接续通过，接续/MCP 输出未回显凭证或错误正文；整轮曾发生上述开发测试回显事件 |
| C. 真实使用期 | 待观察 | c-mac 正常 Agent 使用 3 天或累计 10 次真实研究调用 |
| D. 跨机器部署 | 待 b-mac 验证 | 本轮未部署 b-mac、a-win 或其他机器 |

C 的观察方法：每次研究调用将 request_id、地区、scans 的 provider/state/elapsed_ms、diagnostics code、候选数、总耗时和交接状态写入本机 `.local/web-chain-observation.jsonl`，或从 `record_material_run` 私有记录提取；不把本轮重复冒烟算进去。检查成功率、接续率、超时/认证/限流/无凭证分布、P95 耗时和费用。无 key 发生调用、顺序错误、成功空结果仍接续、丢结果/诊断、提前交接、超预算、推测日期、凭证/错误正文外泄或未解释的整链 unavailable 均算失败；已被接续的单家退化另计，不伪造稳定通过。未创建后台任务。

## 本次变更文件

仅列相对 snapshot 的升级内容；先前用户改动已独立保存。

- `CHANGELOG.md`
- `credentials.env.example`
- `docs/current_capabilities.md`
- `docs/standalone_deployment.md`
- `docs/web_search_chain_validation.md`
- `docs/web_search_fallback.md`
- `ir_search/adapters/anysearch.py`
- `ir_search/adapters/baizhi.py`
- `ir_search/adapters/bocha.py`
- `ir_search/adapters/brave.py`
- `ir_search/adapters/duckduckgo.py`
- `ir_search/adapters/exa.py`
- `ir_search/adapters/perplexity.py`
- `ir_search/adapters/serper.py`
- `ir_search/adapters/tavily.py`
- `ir_search/adapters/web_materials.py`
- `ir_search/adapters/zai.py`
- `ir_search/config_validation.py`
- `ir_search/configs/fallback_routes.yaml`
- `ir_search/configs/source_capabilities.yaml`
- `ir_search/configs/source_routes.yaml`
- `ir_search/contracts/materials.py`
- `ir_search/infrastructure/credentials.py`
- `ir_search/infrastructure/search_transport.py`
- `ir_search/infrastructure/web_engine.py`
- `ir_search/infrastructure/web_material_plan.py`
- `ir_search/infrastructure/web_routing.py`
- `ir_search/infrastructure/web_search.py`
- `ir_search/material_registry.py`
- `ir_search/registry.py`
- `ir_search/services/material_search.py`
- `pyproject.toml`
- `tests/test_search_transport.py`
- `tests/test_standalone_package.py`
- `tests/test_web_materials.py`
- `tests/test_web_quota_fallback.py`
- `tests/test_web_search_chain.py`

## 最终离线检查

最终完整回归：**2319 passed、25 skipped、8 warnings，45.13 秒，exit 0**。使用 Python 3.12 独立环境，命令为 `python3 -m pytest`（PATH 指向该 venv；真实凭证从测试子进程环境剥离，IR_SEARCH_CREDENTIALS_FILE 指向空测试文件）。8 条 warning 为 PDF/绑定库的既有弃用提示。

`tests/test_standalone_package.py` 三项均通过，包含真实 wheel 的独立安装及 MCP 离线验证。另构建本地 `.local/web-chain-dist/ir_search-0.2.0rc2-py3-none-any.whl`，核对新增 adapter、三个 YAML、Version 0.2.0rc2；无私有文件或已知凭证值。SHA-256：`b56174c05572c4e78bcfa3e3cfbdf247ca672202578d991c54d4f50a310cf300`。

最终仓库已跟踪/待提交文件凭证扫描通过；后续 pytest、冒烟、接续/MCP 结果和 MCP stderr 的已知凭证扫描通过。这些检查不能撤销此前已记录的测试输出泄露。

本报告随本地功能提交保存；提交号可由 `git log -1` 查看。本轮没有 merge/push。

# Windows 与网页搜索链整合验收（0.2.0rc2）

日期：2026-10-24；仅 c-mac。候选分支 `release/rc2-merge` 从 `origin/main`（`891847e`）依次 merge Windows 分支 `1b5bdb2` 与本机 `feat/web-search-chain`（`b601afd`），不 rebase、不 force push、不修改 main。用户先前的 WIP 快照及功能提交保留在历史中。本轮接续用户停止时留下的合并现场，操作前另存私有备份。

## 冲突与复核修复

实际文本冲突共 6 个文件：

| 文件 | 处理 |
|---|---|
| CHANGELOG.md | 合并为一个 rc2 版本段，保留 Windows 与搜索链说明及历史边界 |
| docs/standalone_deployment.md | 同时保留跨平台路径、输出目录、分页密钥与各机搜索链/key 配置 |
| ir_search/infrastructure/public_web.py | 保留 Windows 有界 DNS、TLS/fake-IP 诊断与显式开关；保留本机精确域名真实 DNS 选项，浏览器 CONNECT 门不接入该选项 |
| ir_search/infrastructure/_xueqiu_browser_worker.py | 保留可选 Patchright 驱动与 Windows TLS 约束；fake-IP 占位放行仍只允许 Playwright 自带 Chromium |
| ir_search/services/source_diagnostics.py | 合并配置键、凭证解析/权限元数据与分操作诊断，不返回凭证值 |
| tests/test_standalone_package.py | 保留双方安装探针；MCP 归档改用服务器输出根内相对目录 |

credentials.py、credentials.env.example、registry.py、recovery.py 等自动合并后保留双方新增项；模板中的真实凭证字段为空，fake-IP 开关默认 false，版本统一为 `0.2.0rc2`。

复核修复及回归：

- 中文通用词最长优先且顺序固定；两个独立哈希种子的进程均将“毛利率为什么”规划为“毛利率”，相关素材可返回。
- 旧文档抓取的操作预算只在实际传输层计数；真实传输逻辑配模拟 HTTP 覆盖 0/3/5 次重定向成功及第 6 次拒绝。
- TLS reset 回环服务器按 Windows/POSIX 使用正确的 linger 结构，主线程检查服务线程结果；不再以线程崩溃后的关闭充当 reset 测试成功。
- 星球时间线保留独立匹配结构，不因新增局部匹配排序字段丢弃有效记录，也不增加误导的关键词不足提示。
- fake-IP 测试同步精确诊断码；增加 Patchright 不取得 Playwright 占位放行资格的回归。

## 本机测试

Python 3.12 独立环境，剥离真实进程凭证，使用空私有测试 env。`python3 -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning`：**2813 passed、30 skipped、8 warnings，31.19 秒**。跳过项保留平台或可选依赖理由；warning 为既有 PDF/绑定库弃用提示。完整套件包含 wheel 构建、删除构建源后在独立目录运行 SDK/MCP、资源加载和私有文件排除验证。

`scripts/doctor_ir_search_mcp.py`：通过。解释器、FastMCP、服务模块、工具清单均通过，12 个工具，无错误或警告；这项预检不代表所有真实来源均可用。独立安装测试同时验证真实 FastMCP 工具调用；本轮没有为验证额外执行 MCP 真实搜索。

## 有限真实冒烟

每家锁定引擎，中文查询一条、最多请求 2 条、不读取正文。每家恰好一次搜索尝试，无自动重试；Perplexity 一次 Search API 调用。终端只输出引擎、状态、结果条数；未输出标题、原始响应、错误正文或凭证。

| 引擎 | 状态 | 条数 |
|---|---|---:|
| baizhi | success | 2 |
| zai | success | 2 |
| exa | success | 2 |
| perplexity | success | 2 |
| brave | success | 2 |
| serper | success | 2 |
| anysearch | success | 2 |
| tavily | success | 2 |
| bocha | tls_error | 0 |
| duckduckgo | 未测，保留此前验证挑战诊断 | — |

博查在本轮 Python 3.12 下失败，未重试或关闭 TLS 验证；此前系统 Python 3.9 的补测成功记录保留，不能替代本轮结果。DuckDuckGo 遵照用户要求不再排查或调用。其他 8 家返回发现结果只证明有限请求可用，不证明相关性、事实、日期或检索完整性。

冒烟准备时本机私有 env 的 Brave 条目格式不合法，未发出请求；先保存权限 600 的忽略目录备份，仅从本机已存在的 Pi 配置恢复该项，确认其他字段未变、网页配置可解析。真实凭证和备份均不属于发布内容。

## 秘密检查与交接边界

发布前检查当前 Git 跟踪文件、相对 main 的变更及两条合并历史中的 blob，覆盖 WIP 旧内容；检查真实本机凭证值、常见 token 前缀、长凭证赋值、模板空值及私有路径排除。命中仅输出文件/行号，测试合成值与文档变量引用分别核对。提交前检查通过：真实本机凭证命中 0、未解释的模式命中 0、私有路径命中 0；credentials.env 被忽略、模板凭证字段为空。合并提交后、推送前再复核历史检查；真实 key 命中必须停止上传。

只发布源码、测试、公开资源和空凭证模板；credentials.env、accounts.json、.local、.env/.key/.pem、备份、会话及真实素材不上传。GitHub 上提供候选分支与 PR，暂不合并 main。b-mac 后续下载步骤见[部署指南](standalone_deployment.md)。

| 稳定性项目 | 状态 | 边界 |
|---|---|---|
| A. 离线/独立安装与所有引擎真实成功 | 未通过 | 离线安装通过；本轮 8 家成功、博查 TLS 失败，DuckDuckGo 未测 |
| B. 接续与无泄露 | 本轮离线回归通过；本轮真实接续未测 | 先前坏 key 接续记录保留；此前测试输出泄露事件不能由本轮检查撤销 |
| C. 正常使用 3 天或累计 10 次研究调用 | 待观察 | 不为凑次数搜索，不把冒烟计入；沿用网页链报告的脱敏观察方法，尚无连续使用验收 |
| D. 跨机器部署 | 待 b-mac 验证 | 本轮未操作其他电脑，未以 c-mac 结果替代 Windows/b-mac 实测 |

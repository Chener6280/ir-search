# 历史独立安装与逐次验收记录

本文归档于 0.2.0rc1。当前安装步骤见[跨电脑部署](standalone_deployment.md)，当前行为见[交接说明](../HANDOFF.md)。下文的已完成/待完成状态仅对应记录当时。

# 独立安装、跨电脑调用与迁移原则

`ir_search` 是独立发布的实现仓库。投研 skills 和 agent 是调用方，经 Python SDK 或 MCP 获取数据和证据；运行时不需要开发者桌面的 BrokerSkills、skills 或其中的配置文件。

## 本地 skills 如何迁入

BrokerSkills 仅作为只读参考。迁移时提取其中可复用的连接方式、已核实的字段定义、单位和业务约束，重构为仓库内的实现并增加测试。原 skill 的投研方法可以继续保留在调用方；原文中的指令不作为来源数据执行。

| 参考内容 | 本仓库中的独立实现 | 已迁移范围 |
|---|---|---|
| Wind A 股数据库查询与字典 | `ir_search/adapters/wind_mysql.py` | 证券目录、未复权日线、参数化单表查询、单位换算 |
| JYDB 查询约束与字典 | `ir_search/adapters/jydb_market.py`、`ir_search/adapters/jydb.py` | A 股/科创板日线、公告元数据及正文 |
| 财务与国内衍生品字典 | `ir_search/adapters/financial_statements.py`、`ir_search/adapters/domestic_derivatives.py` | 三表核心字段、期货/期权日线、合约与日历 |
| 数据库连接、凭证与分页 | `ir_search/infrastructure/` | 集中 env、显式 TLS/非 TLS、只读事务、短连接、请求绑定游标、取消恢复 |
| AKShare 接口规范 | `ir_search/adapters/akshare_intraday.py`、`ir_search/adapters/akshare_futures.py`、`ir_search/adapters/akshare_options.py` | 近期沪深 A 股价格、具体期货分钟数据和 ETF 期权当天点价；独立进程期限/取消控制 |
| 原仓库公众号工具 | `ir_search/clients/wechat_articles.py` | 客户端迁入安装包；`tools/gzh_fetch.py` 仅保留兼容入口 |
| 华福星球取材流程与官方 MCP | `ir_search/adapters/zsxq_materials.py`、`ir_search/infrastructure/zsxq.py`、`ir_search/infrastructure/zsxq_documents.py` | 有限时间流、详情与角色、附件发现和显式 PDF 提取；无需 CLI 或桌面参考目录 |
| FMP 官方 Stable API | `ir_search/adapters/fmp.py`、`ir_search/infrastructure/fmp.py` | 指定美股公司资料、基础日线及年度标准化三表；固定 HTTPS、查询预算及进程内短时缓存 |

没有把账号、密码、Key、Cookie 或整套私有技能目录复制进发布包。当前支持范围与 partial/unsupported 限制见[数据接口说明](market_data_source_policy.md)；实际样本见[交付报告](domestic_data_delivery.md)。本轮只做本地安装回归，跨电脑部署和 GitHub 发布尚未执行。

## 在另一台电脑安装

以下步骤针对包含本轮改动的仓库版本；发布前使用本地 checkout 验证，发布后可以按对应 Git 分支/提交检出。使用 Python 3.10+ 运行 MCP，建议统一到 Python 3.12。

```bash
git clone https://github.com/Chener6280/ir-search.git
cd ir-search
python3 -m venv .venv
.venv/bin/python -m pip install '.[mcp,mysql,akshare]'
```

Windows 使用 `python -m venv .venv`，解释器路径为 `.venv\Scripts\python.exe`。包会在 Windows 安装时补充时区数据库。macOS/Linux 的解释器路径为 `.venv/bin/python`。无需安装 BrokerSkills，也无需配置指向桌面源码的 `PYTHONPATH`。

把 `credentials.env.example` 复制到本机私有位置，填写该电脑使用的数据源账户。macOS/Linux 将文件权限设为 `600`；Windows 使用当前账户的私有目录和文件访问权限。每台电脑分别配置 `IR_SEARCH_CREDENTIALS_FILE`，指向该文件的绝对路径。若需要私有 CA，应在该电脑单独配置已核实的证书路径和指纹。

只使用 FMP 的 SDK 不需要 mysql/akshare extras；FMP 使用 Python 标准库。MCP 调用安装 `.[mcp]` 即可。填写本机 `FMP_API_KEY` 并启用 `FMP_ENABLED=true`，其他来源可保持关闭。首次调用示例与套餐升级边界见 [FMP 接口指南](fmp_adapter.md)。

网页素材使用基础依赖即可连接博查/AnySearch；配置 `WEB_SEARCH_PROVIDER=regional` 和两家的独立 Key，调用时可明确 `web_region`。读取原站 PDF 另装 `.[extract]`。参见[网页指南](web_material_adapter.md)。

动态网页可选安装 `.[crawl,extract,mcp]` 并运行 `python -m playwright install chromium`，使用 Python 3.10+。SDK/MCP 的 `web_read_mode` 控制 HTTP/自动/浏览器读取，缺包和缺浏览器均有诊断；不用安装浏览器也能读取普通网页。调用与边界见[统一网页读取指南](web_reader.md)。包内 worker 随 wheel 分发，运行时不依赖本仓库的实验环境或脚本。

知识星球新素材 SDK 只需基础依赖与本机 `ZSXQ_KEY`，启用 `ZSXQ_MATERIALS_ENABLED`；作为 MCP 服务安装 `.[mcp]`，提取 PDF 另装 `.[extract]`。星球选择、预算和调用例子见[知识星球指南](zsxq_material_adapter.md)。

Python SDK：

```python
from ir_search import DataRequest, get_data, list_capabilities

capabilities = list_capabilities()
result = get_data(DataRequest(
    "prices_daily", symbols=["000001.SZ"],
    start="2026-09-01", end="2026-09-11", fields=["close"],
)).to_dict()
```

MCP 客户端使用 stdio 启动本机已安装的服务。以下是通用配置示例，实际外层配置格式以客户端为准：

```json
{
  "mcpServers": {
    "ir_search": {
      "command": "/ABSOLUTE/PATH/TO/.venv/bin/python",
      "args": ["-m", "ir_search.mcp_server"],
      "env": {
        "IR_SEARCH_CREDENTIALS_FILE": "/ABSOLUTE/PATH/TO/private/credentials.env",
        "IR_SEARCH_LIVE": "1"
      }
    }
  }
}
```

也可使用安装后提供的 `ir-search-mcp` 命令。Windows 的 JSON 路径需转义反斜线，或使用正斜线。

`IR_SEARCH_LIVE` 控制旧搜索来源；新的数值 adapters 由 env 文件中的各自 `ENABLED` 开关控制。旧搜索来源的 Key/账号映射尚未全部迁入集中 env，仍需按各 adapter 的文档配置进程环境。仅旧 `search` 的 CLI 型来源（知识星球、Longbridge 等）仍可能需要其明确声明的外部工具，新 `search_materials` 的知识星球不依赖 CLI；它们是可选来源，不是导入核心包的前提。

## 发布验收

- 运行完整离线测试。
- 在临时目录构建并安装 wheel，删除构建源码，再从无关工作目录运行 SDK、配置校验、实体字典、搜索及 MCP。
- 测试禁止网络；搜索的测试来源明确标为 mock，不把离线样例当作真实数据验收。
- wheel 必须包含规则 YAML、实体 CSV、来源客户端；不包含私有 env、账号文件、桌面目录或技能盘点资料。
- GitHub workflow 覆盖 Linux、macOS 和 Windows 的独立安装检查。配置了 workflow 不等于这些系统已全部在线验证，需以实际运行结果为准。
- 发布到 GitHub 不携带使用者权限。新电脑还需要可用的网络、数据源账户和对应权限；任何失败都应返回诊断。

本原则已写入仓库 `AGENTS.md`，后续接手开发的 agent 也应遵守。

## 本轮验证记录（2026-09-14）

后续真实验收已补充 AKShare 新浪股票后端、具体期货合约分钟数据与可独立运行的 `ir-search-acceptance`。最新样本与测试结果见[验收报告](data_acceptance.md)。以下为独立安装改造阶段的历史记录。

本机全项目离线测试 645 通过、6 跳过。默认 Python 的 setuptools 低于项目构建要求，包构建测试在该环境明确跳过；随后在隔离的 Python 3.12 环境运行独立安装、MCP、数据入口、MySQL 与凭证相关测试，55 项通过。实际独立安装测试已包含删除构建源码、从无关目录调用，以及私有文件不进入 wheel 的检查。

GitHub 的多系统 workflow 已添加到本地仓库，本轮没有推送或运行远端 CI；不能据此宣称 Windows/Linux 已在线验收。桌面的 BrokerSkills 保持为只读参考，本轮未作修改。

## FMP 接入后的补充验证（2026-09-15）

全项目离线测试 823 通过、7 跳过。另在 Python 3.12 环境运行 FMP、真实 MCP 注册和独立安装测试，90 项通过。wheel 验证包含 FMP adapter、HTTP 客户端、数据定义、启用/关闭配置、SDK/MCP 能力发现和未授权请求诊断；构建源码被删除后，从无关目录验证，不依赖私有 env 或桌面文件。默认环境跳过的可选 MCP/构建检查由此环境补充。真实取数单独记录在 [FMP 正式验收](fmp_adapter_acceptance.md)，没有将离线样例当成实取结果。

## 公众号素材的私有配置

新 `wechat` 来源通过包内 `adapters/wechat_materials.py` 和 `infrastructure/wechat.py` 工作。`WECHAT_ACCOUNTS_FILE` 相对于 env 文件目录解析，不依赖源码目录或桌面 skills；凭证、公众号清单和真实响应不放进 wheel。独立安装测试覆盖新来源注册、模拟读取、SDK/MCP 账号筛选和供应商正文标记。详见[公众号素材指南](wechat_material_adapter.md)。

## IMA 独立部署

`ima_materials`、`infrastructure/ima.py` 和 `ima_documents.py` 全部随 wheel 安装。无需官方 skill、Node 或桌面路径；每台电脑提供自己的 `IMA_API_KEY`、`IMA_CLIENT_ID` 及显式启用配置。独立安装测试覆盖已安装包的 IMA 注册、SDK/MCP 搜索、`ima://` 原文读取和安全状态查询。私人凭证、知识库清单、参考 skill 包和真实响应不打包。详见 [IMA 素材指南](ima_material_adapter.md)。

公众号缓存默认在当前电脑 env 文件旁的 `.local/wechat-cache/`，不进入安装包。`WECHAT_CACHE_DIR` 可改位置；不需要搬运桌面 skills、Cookie 或原电脑缓存。缓存模式与可选浏览器要求见[公众号素材指南](wechat_material_adapter.md)。

公众号结构解析与本地导出位于包内 `documents/wechat_html.py`、`services/material_archive.py`，无新增基础依赖。`archive_dir` 由各电脑的调用方显式指定，不把开发者目录或远程 Skill 引入运行时；本地文章和图片不能提交或随 wheel 打包。

## 智堡独立部署

`adapters/wisburg_materials.py`、`infrastructure/wisburg.py` 与 `wisburg_documents.py` 随 wheel 安装，使用标准库的官方 TLS/MCP 客户端。每台电脑单独配置 `WISBURG_API_KEY`（兼容 `WISBERG_KEY`）与显式启用项；不需要复制私有响应、Skill 或本机目录。安装测试覆盖来源注册、SDK/MCP 分类搜索、摘要范围、URI 读取、引用与归档。见[智堡接入指南](wisburg_material_adapter.md)。


## 机构与官网取材增强（2026-09-17）

已增加 14 条有出处的机构目录、显式官网范围 `web_institutions`、监管目录/附件识别、HTTP 正文有界并发 `web_read_workers` 和无网络执行预览 `dry_run`。目录随安装包分发，SDK 与 MCP 均可调用；详见[使用说明](material_hardening.md)。

小宇宙语音转写可选安装 `[audio]`（`websockets==15.0.1`）并配置 PATH 中的 FFmpeg；不转写时无需音频依赖。每台电脑配置自己的 Agent Plan key 与私有缓存，见[音频部署及调用](xiaoyuzhou_audio_adapter.md)。

## 在实际运行环境检查来源

安装后可运行 `ir-search-doctor` 或调用 `diagnose_sources()`，检查当前 Python 环境的配置和依赖；不能用另一个虚拟环境的成功记录替代本环境验收。默认零网络请求。需要时用 `--provider xhs --live` 显式检查本地后端登录，其他来源通过有界真实样本验收。操作、恢复建议和私有运行记录见 [来源可靠性指南](platform_reliability.md)。

2026-09-18：通用网页新增显式 `scrapling` / `firecrawl` 读取模式；素材搜索新增 `providers=["rss"]`。可选依赖、配置、引用口径和限制见 [网页与订阅源改进](web_toolkit.md)。默认读取方式不变。

宏观、基金、Fiona 与港股原文的包内 CSV、数据集、SDK / MCP 与诊断已纳入独立安装包检查。无密钥开关与具体依赖见 [新增来源指南](expanded_adapters.md)。

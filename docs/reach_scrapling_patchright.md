# Agent Reach、Scrapling 与 Patchright 增强

2026-10-03，未发布工作区增强。核心仍是 `get_data`、`search_materials`、`retrieve`；默认 HTTP、显式选源和已有来源分层保持原契约。本次独立编写诊断与解析逻辑，没有复制或执行外部项目安装器，没有加入 LLM 搜索、报告生成或旧 `deep_research` 扩展。

## 采用范围

| 参考项目 | 本次采用 | 运行依赖 |
|---|---|---|
| [Agent Reach](https://github.com/Panniantong/Agent-Reach) | 按操作探测、可用后端与声明后端分开、安装/修复提示 | 无 Agent Reach 依赖 |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | 已有 Selector 的正文选择；增加有界 DOM 表格、跨度和结构指纹 | 原有 `.[scrapling]`，固定 0.4.15 |
| [patchright-enhanced](https://github.com/whaleyxbt/patchright-enhanced) | 借鉴临时 Chrome 会话与清理生命周期；保留项目现有网络门禁和期限 | 不引入 Node/TypeScript 封装 |
| [Patchright Python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) | 直接作为实验性雪球浏览器驱动 | 新增 `.[patchright]`，固定 1.62.1，Python 3.10+ |

参考材料为公开文档和源码，不是运行时发现目录。原始 `D4Vinci/ScraplingPatchright` 链接未成功访问，采用范围分别依据可确认的 Scrapling 与 Patchright 上游。Patchright 1.62.1 的已发布 wheel 声明 Python >=3.10；上游 main 的开发要求可能不同，安装以固定发布版为准。不使用 Scrapling `ai/fetchers/all` extras，其 MCP 2.x 要求不进入本项目 MCP 1.x 环境。

## 来源诊断：按操作证明一个样本

`diagnose_sources()` 和 `source_health()` 默认仍为本地检查，零来源调用，不安装包或执行登录。报告增加 `operation_status`；`search_materials`、`retrieve`、`get_data` 的默认状态分别是 `not_probed`。依赖增加已安装版本和当前 Python 环境的安装提示，包存在不证明浏览器二进制或平台可用。

`live=True` 必须显式选择 providers。轻量检查包括：

- `xhs`：原有只读登录检查，结果在 `operation_status.login`，不证明搜索或正文。
- `rss`：有界读取用户已配置的最多五个 feed；结果在 `feed_snapshot`，不证明搜索匹配、文章正文或全量历史。
- `search_samples`：最多五个已选来源，每个提供一个明确查询与发布日期窗口。固定一条候选、零正文读取、一个返回组，不自动改选其他来源。可传证券、账号/集合、CIK、视频平台等限定字段。供应商查询可能计费，费用不是零的承诺。
- `read_samples`：目前仅接受 `{"web": "https://..."}`，一个普通公开网页，无查询串、fragment、认证信息或专用平台 URL。读取模式限 `http/scrapling/browser`，不触发 Firecrawl 云计费。网页仍需有效的 web 启用配置。

```python
from ir_search import diagnose_sources, RequestContext

health = diagnose_sources(
    ["web", "rss"], live=True,
    read_samples={
        "web": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm"
    },
    web_read_mode="scrapling",
    context=RequestContext(timeout_seconds=40, max_operations=10),
)

search_check = diagnose_sources(
    ["web"], live=True,
    search_samples={"web": {
        "question": "NVIDIA revenue", "keywords": ["revenue"],
        "published_start": "2026-09-01", "published_end": "2026-09-18",
        "web_region": "overseas",
    }},
)
```

所有样本先整体校验，再开始来源调用。`search_samples` 不接受 `providers`、正文预算、续取或 dry-run 覆盖，provider 始终锁定到对应已选来源。查询验证只证明该次请求成功执行；0 命中也是一种有效查询结果，不证明研究问题得到回答。缺少必需范围、待宿主搜索接续、权限/额度/网络/上游结构错误均不标为通过。

网页样本必须实际取得文章/文档正文。目录、验证码页、浏览器失败或显式 Scrapling 回退不能标为所选读取路径成功。任何失败保留稳定错误和修复步骤，不打印源正文、查询明文或原始异常。失败来源停止后续样本读取；共享 `RequestContext` 控制全部调用的期限和操作预算。

`sample_verified` 表示某个有界操作通过；要看具体 `operation_status` 和三个 `*_live_verified` 标志。顶层 `status=ok` 仍只表示诊断报告完成。`source_calls_started` 是探测派发数，不是 HTTP 请求数，`operations_used` 单独记录实际预算消耗。

CLI 与 MCP 使用相同实现：

```sh
ir-search-doctor --provider web --live --read-mode scrapling \
  --read-url https://www.federalreserve.gov/newsevents/pressreleases/monetary20250917a.htm
ir-search-doctor --provider rss --live
ir-search-doctor --provider web --live --search-samples samples.json
```

`samples.json` 是上述 `search_samples` 对象；CLI 限 16 KiB。MCP `source_health` 增加同名 `search_samples/read_samples/web_read_mode` 参数，返回 JSON-serializable dict。`get_data` 的真实验收继续通过明确的数值请求完成，诊断不会自动查询行情或财报。

## Scrapling：表格与结构变化可见

`read_details.html_extraction` 新增：

- `structure_hash`：选择器、匹配数、最多 5,000 个 DOM 标签次序及表格单元格跨度的 SHA-256。修改正文数字或日期不会改变结构指纹；调用方可比较同一 URL 和同一选项的指纹，不自动学习或恢复选择器。达到节点上限时标记 `structure_truncated`，上限之后的变化可能未被观测。
- `tables`：保留 caption、行、`th/td`、单元格文字和原始 rowspan/colspan。金额字符串、缺失符号和单位文字保持原文，不转数、不展开合并单元格，不解释金融口径。
- `table_scope=selected_body_dom_cells_not_normalized_financial_data`：这些结构来自所选 HTML 正文，不是标准化财务数据集；不能代替 `get_data`。
- `tables_truncated`、`invalid_spans`、`nested_tables`：结构不完整、非法跨度或嵌套表格显式暴露。正文区域之外存在表格时，`state=tables_outside_selected_body`，回退整页文本而不静默漏掉表格。

最多五张表、每表 100 行、每行 30 格、每格/标题 500 字符。表格诊断文字总预算为 `min(max_chars, 10000)`，与正文长度上限分别计量。不能将达到预算后的表格当作完整数据。元数据、链接、raw hash 和原有来源分类保留；正文哈希和字符引用仍按实际返回文本生成。选择器和结构指纹均不证明完整性。

## 可选 Patchright 雪球驱动

```sh
python -m pip install '.[patchright,mcp]'
python -m patchright install chromium
```

每台电脑安装自己的浏览器依赖并在私有 env 显式配置：

```dotenv
XUEQIU_READ_MODE=browser
XUEQIU_BROWSER_DRIVER=patchright
XUEQIU_BROWSER_CHANNEL=chromium
XUEQIU_BROWSER_HEADLESS=true
XUEQIU_BROWSER_USE_COOKIE=false
```

已安装 Chrome 的机器可显式选择 `CHANNEL=chrome`。默认驱动仍为 `playwright`，HTTP 模式不因配置驱动而启动浏览器。Patchright 要求精确 1.62.1；不支持版本、缺包或缺浏览器时返回相应稳定诊断，不自动安装或切回 Playwright。

驱动运行于隔离子进程，使用临时浏览器上下文；完成、失败、取消和超时均清理进程与临时目录。只通过现有短期认证 CONNECT 门禁访问固定公网主机，验证 TLS，限制请求、流量、导航重试和资源类型。只读主帖，日期必须绑定同一文章，继续保持 UGC/opinion 和非独立转载的证据分类。Cookie 仅显式选择时使用本机提供值；不读取个人 Chrome 资料、自动登录或处理验证码。

Patchright 自身包含上游驱动补丁，结果明确记录 `driver_patches=upstream_patchright`、`backend=patchright_chromium`、`session_scope=disposable_context` 和 `automatic_failover=false`。它仍是实验性方案，不提供平台放行保证。没有改动本机网络配置、Clash、TUN、DNS、hosts 或现有网站规则。

## 本次验收与限制

| 检查 | 实际结果 | 范围 |
|---|---|---|
| Python 3.12 独立环境全量 pytest | 2,211 passed、3 skipped | 含真实 Scrapling Selector 回归；跳过的在线/环境项不算验收通过 |
| 定向诊断、Scrapling、雪球测试 | 135 passed | 显式预算、错误脱敏、失败不变绿、表格/结构和驱动选择 |
| wheel 独立构建安装 | 3 passed | 无关目录 SDK/MCP、资源与私有文件排除；新增接口随包可用 |
| 美联储 2025-09-17 原文 / Scrapling | 正文样本通过，返回 2,000 字符 | `completeness_verified=false`，仅此 URL 的读取能力 |
| 美联储 press_all RSS | 一张快照、20 条记录 | 仅 feed 可读，search/retrieve 标志保持未验 |
| 上述网页与 RSS 诊断预算 | 合计 2 次操作 | 临时无凭证配置；不更改既有账号文件 |
| 雪球既有公开样本 / Patchright 1.62.1、本机 Chrome、匿名 | **blocked_url，4 次操作，未取得正文** | `https://xueqiu.com/1965894836/341130177`；未放宽主机或地址策略，未声称修复平台验证 |

系统 Python 3.9 的全量运行另观察到既有 local_archive 隔离 worker 无法加载用户目录 PyYAML，导致该环境的归档测试失败。新 Patchright 仅支持 Python 3.10+。推荐的 Python 3.12 独立环境已通过同一归档测试及全量检查；未通过修改系统依赖或降低 worker 隔离来隐藏环境问题。

默认配置路径的首次 live 检查返回 `configuration_error`、0 次操作；随后使用明确启用 web/RSS 的临时配置完成以上公开样本。配置检查通过不代表旧凭证或其他供应商已重新验收。真实搜索样本本轮只做协议/错误路径与零正文预算回归，没有执行新的付费供应商查询。此轮未发布 GitHub，未宣布跨系统或长期可用性。
